"""Test-only DeepSeek relay; the real key never reaches the agent runtime.

Run in a disposable container. Fixed upstream/model and finite request, token,
cost, and time limits. Receipts contain counts and usage, never prompts/keys.
"""

import hashlib
import json
import os
import tempfile
import threading
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MODEL = "deepseek-flash"
INPUT_PRICE = Decimal("0.3")  # verified peak USD/M, 2026-09-20
OUTPUT_PRICE = Decimal("1.2")
UPSTREAM = "https://api.deepseek.com/v1/chat/completions"
RECEIPT = Path("/evidence/receipt.json")
lock = threading.Lock()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def store_receipt(**result):
    """All callers hold ``lock``; a crash leaves either complete ledger version."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=RECEIPT.parent, delete=False) as output:
            temporary = Path(output.name)
            json.dump(result, output, sort_keys=True)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, RECEIPT)
        directory = os.open(RECEIPT.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def budget_config():
    budget = Decimal(os.environ["MAX_COST_USD"])
    max_requests = int(os.environ.get("MAX_REQUESTS", "1"))
    output_limit = int(os.environ.get("OUTPUT_LIMIT", "32"))
    valid_until = datetime.fromisoformat(os.environ["PRICING_VALID_UNTIL"])
    now = datetime.now(UTC)
    if (
        not budget.is_finite()
        or not 0 < budget <= Decimal("0.05")
        or not 1 <= max_requests <= 8
        or not 32 <= output_limit <= 1024
        or not valid_until.tzinfo
        or not now < valid_until <= now + timedelta(days=1)
    ):
        raise ValueError("invalid budget or stale pricing")
    return {
        "model": MODEL,
        "max_cost_usd": str(budget),
        "max_requests": max_requests,
        "output_limit": output_limit,
        "pricing_valid_until": valid_until.isoformat(),
        "input_price_usd_per_million": str(INPUT_PRICE),
        "output_price_usd_per_million": str(OUTPUT_PRICE),
    }


def token_cost(count_in, count_out):
    return (count_in * INPUT_PRICE + count_out * OUTPUT_PRICE) / 1_000_000


def summarize(receipt):
    requests = receipt["requests"]
    settled = [item for item in requests if item["state"] == "settled"]
    spent = sum((Decimal(item["cost_usd"]) for item in settled), Decimal(0))
    reserved = sum(
        (Decimal(item["estimated_ceiling_usd"]) for item in requests if item["state"] != "settled"),
        Decimal(0),
    )
    known = bool(requests) and len(settled) == len(requests)
    receipt.update(
        attempts=len(requests),
        usage=(
            {
                "input_tokens": sum(item["usage"]["input_tokens"] for item in settled),
                "output_tokens": sum(item["usage"]["output_tokens"] for item in settled),
            }
            if known
            else None
        ),
        cost_usd=str(spent) if known else None,
        estimated_ceiling_usd=str(
            sum((Decimal(item["estimated_ceiling_usd"]) for item in requests), Decimal(0))
        ),
        spent_cost_usd=str(spent),
        reserved_cost_usd=str(reserved),
        accounted_cost_usd=str(spent + reserved),
        http_status=requests[-1]["http_status"] if requests else None,
        request_sha256=requests[-1]["request_sha256"] if requests else None,
    )
    if requests and requests[-1].get("error"):
        receipt["error"] = requests[-1]["error"]
    else:
        receipt.pop("error", None)


def load_receipt(config):
    if not RECEIPT.exists():
        receipt = {**config, "requests": []}
    else:
        receipt = json.loads(RECEIPT.read_text())
        if any(receipt[key] != value for key, value in config.items()):
            raise ValueError("ledger configuration changed")
        requests = receipt["requests"]
        if not isinstance(requests, list) or not len(requests) <= config["max_requests"]:
            raise ValueError("invalid ledger requests")
        if receipt["attempts"] != len(requests):
            raise ValueError("invalid ledger attempts")
        for sequence, item in enumerate(requests, 1):
            bound = item["input_token_bound"]
            if (
                item["sequence"] != sequence
                or type(bound) is not int
                or bound <= 0
                or Decimal(item["estimated_ceiling_usd"])
                != token_cost(bound, config["output_limit"])
                or item["state"] not in {"reserved", "settled", "uncertain"}
            ):
                raise ValueError("invalid ledger reservation")
            if item["state"] == "settled":
                usage = item["usage"]
                count_in, count_out = usage["input_tokens"], usage["output_tokens"]
                if (
                    type(count_in) is not int
                    or type(count_out) is not int
                    or not 0 <= count_in <= bound
                    or not 0 <= count_out <= config["output_limit"]
                    or Decimal(item["cost_usd"]) != token_cost(count_in, count_out)
                    or item["http_status"] != 200
                ):
                    raise ValueError("invalid ledger settlement")
            elif item["usage"] is not None or item["cost_usd"] is not None:
                raise ValueError("unsettled usage cannot release a reservation")
    summarize(receipt)
    if Decimal(receipt["accounted_cost_usd"]) > Decimal(config["max_cost_usd"]):
        raise ValueError("ledger exceeds budget")
    return receipt


class BudgetExhausted(Exception):
    pass


def reserve(config, messages):
    # Text UTF-8 bytes plus framing safely overestimate input tokens for these
    # text-only requests. No requested tool schema or hidden channel is forwarded.
    bound = len(json.dumps(messages, ensure_ascii=False).encode()) + 512
    ceiling = token_cost(bound, config["output_limit"])
    with lock:
        receipt = load_receipt(config)
        if receipt["attempts"] >= config["max_requests"] or Decimal(
            receipt["accounted_cost_usd"]
        ) + ceiling > Decimal(config["max_cost_usd"]):
            raise BudgetExhausted
        item = {
            "sequence": receipt["attempts"] + 1,
            "state": "reserved",
            "input_token_bound": bound,
            "estimated_ceiling_usd": str(ceiling),
            "request_sha256": hashlib.sha256(
                json.dumps(messages, sort_keys=True).encode()
            ).hexdigest(),
            "usage": None,
            "cost_usd": None,
            "http_status": None,
        }
        receipt["requests"].append(item)
        summarize(receipt)
        store_receipt(**receipt)
        return item


def settle(config, sequence, *, usage=None, http_status=None, error=None):
    with lock:
        receipt = load_receipt(config)
        item = receipt["requests"][sequence - 1]
        item.update(
            state="settled" if usage is not None else "uncertain",
            usage=usage,
            cost_usd=(
                str(token_cost(usage["input_tokens"], usage["output_tokens"])) if usage else None
            ),
            http_status=http_status,
        )
        if error:
            item["error"] = error
        summarize(receipt)
        store_receipt(**receipt)


def trusted_usage(raw, *, stream, reservation, output_limit):
    if stream:
        data = [line[5:].strip() for line in raw.decode().splitlines() if line.startswith("data:")]
        if not data or data[-1] != "[DONE]" or data.count("[DONE]") != 1:
            return None
        packets = [json.loads(line) for line in data[:-1]]
    else:
        packets = [json.loads(raw)]
    if not packets or any(
        not isinstance(packet, dict) or packet.get("model", MODEL) != MODEL for packet in packets
    ):
        return None
    packet = next((packet for packet in reversed(packets) if packet.get("usage")), {})
    usage = packet.get("usage")
    if not isinstance(usage, dict) or packet.get("model") != MODEL:
        return None
    count_in, count_out = usage.get("prompt_tokens"), usage.get("completion_tokens")
    if (
        type(count_in) is not int
        or type(count_out) is not int
        or not 0 <= count_in <= reservation["input_token_bound"]
        or not 0 <= count_out <= output_limit
        or (
            "total_tokens" in usage
            and (
                type(usage["total_tokens"]) is not int
                or usage["total_tokens"] != count_in + count_out
            )
        )
    ):
        return None
    return {"input_tokens": count_in, "output_tokens": count_out}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def respond(self, code, body, content_type="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/") == "/v1/models":
            self.respond(200, json.dumps({"data": [{"id": MODEL}]}).encode())
        else:
            self.respond(404, b"{}")

    def do_POST(self):
        if (
            self.path != "/v1/chat/completions"
            or not os.environ.get("RELAY_TOKEN")
            or self.headers.get("Authorization") != "Bearer " + os.environ["RELAY_TOKEN"]
        ):
            self.respond(403, b'{"error":"relay request rejected"}')
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 131072:
                raise ValueError
            payload = json.loads(self.rfile.read(size))
            if not isinstance(payload, dict) or payload.get("model") != MODEL:
                raise ValueError
            messages = payload["messages"]
            if not isinstance(messages, list) or not 1 <= len(messages) <= 32:
                raise ValueError
            if any(
                not isinstance(m, dict)
                or m.get("role") not in {"system", "user", "assistant"}
                or not isinstance(m.get("content"), str)
                for m in messages
            ):
                raise ValueError
            if type(payload.get("stream", False)) is not bool:
                raise ValueError
            messages = [{"role": item["role"], "content": item["content"]} for item in messages]
            config = budget_config()
        except (ValueError, KeyError, TypeError, ArithmeticError):
            self.respond(422, b'{"error":"relay model/input/budget rejected"}')
            return
        try:
            reservation = reserve(config, messages)
        except BudgetExhausted:
            self.respond(429, b'{"error":"request or cumulative cost budget exhausted"}')
            return
        except (OSError, ValueError, KeyError, TypeError, ArithmeticError):
            self.respond(503, b'{"error":"budget ledger unavailable"}')
            return
        body = {
            "model": MODEL,
            "messages": messages,
            "max_tokens": config["output_limit"],
            "thinking": {"type": "disabled"},
            "stream": bool(payload.get("stream")),
        }
        if body["stream"]:
            body["stream_options"] = {"include_usage": True}
        usage, status, error = None, None, None
        try:
            key = os.environ["REAL_PROVIDER_KEY"]
            request = urllib.request.Request(
                UPSTREAM,
                data=json.dumps(body).encode(),
                headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
            )
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=25) as response:
                raw = response.read(2 * 1024 * 1024 + 1)
                content_type = response.headers.get("Content-Type", "application/json")
                status = response.status
            if status != 200 or len(raw) > 2 * 1024 * 1024:
                raise ValueError("incomplete or unsuccessful upstream response")
            raw = raw.replace(key.encode(), b"[redacted]")
            usage = trusted_usage(
                raw,
                stream=body["stream"],
                reservation=reservation,
                output_limit=config["output_limit"],
            )
            if usage is None:
                error = "usage_missing_or_untrusted"
        except Exception as exc:
            if isinstance(exc, urllib.error.HTTPError):
                status = exc.code
            error = "upstream_failed_or_uncertain"
        try:
            settle(config, reservation["sequence"], usage=usage, http_status=status, error=error)
        except (OSError, ValueError, KeyError, TypeError, ArithmeticError):
            # The previously persisted full reservation still consumes budget.
            self.respond(503, b'{"error":"budget settlement unavailable"}')
            return
        if error == "upstream_failed_or_uncertain":
            self.respond(502, b'{"error":"upstream failed or uncertain; no retry budget"}')
        else:
            self.respond(status, raw, content_type)


if __name__ == "__main__":
    with lock:
        store_receipt(**load_receipt(budget_config()))
    ThreadingHTTPServer(("0.0.0.0", 8642), Handler).serve_forever()
