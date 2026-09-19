"""Test-only DeepSeek relay; the real key never reaches the agent runtime.

Run in a disposable container. One request, fixed upstream/model, bounded text
input/output and deadline. Receipts contain counts and usage, never prompts/keys.
"""

import hashlib
import json
import os
import threading
import urllib.error
import urllib.request
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MODEL = "deepseek-flash"
INPUT_PRICE = Decimal("0.3")  # verified peak USD/M, 2026-09-20
OUTPUT_PRICE = Decimal("1.2")
OUTPUT_LIMIT = 32
UPSTREAM = "https://api.deepseek.com/v1/chat/completions"
RECEIPT = Path("/evidence/receipt.json")
lock = threading.Lock()
attempts = 0


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def store_receipt(**result):
    RECEIPT.write_text(json.dumps(result))
    RECEIPT.chmod(0o600)


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
        global attempts
        if (
            self.path != "/v1/chat/completions"
            or self.headers.get("Authorization") != "Bearer " + os.environ["RELAY_TOKEN"]
        ):
            self.respond(403, b'{"error":"relay request rejected"}')
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 131072:
                raise ValueError
            payload = json.loads(self.rfile.read(size))
            if payload.get("model") != MODEL:
                raise ValueError
            messages = payload["messages"]
            if not isinstance(messages, list) or not 1 <= len(messages) <= 32:
                raise ValueError
            if any(
                m.get("role") not in {"system", "user", "assistant"}
                or not isinstance(m.get("content"), str)
                for m in messages
            ):
                raise ValueError
            bound = len(json.dumps(messages, ensure_ascii=False).encode()) + 512
            ceiling = (bound * INPUT_PRICE + OUTPUT_LIMIT * OUTPUT_PRICE) / 1_000_000
            budget = Decimal(os.environ["MAX_COST_USD"])
            if not 0 < budget <= Decimal("0.05") or ceiling > budget:
                raise ValueError
        except (ValueError, KeyError, TypeError):
            self.respond(422, b'{"error":"relay model/input/budget rejected"}')
            return
        with lock:
            if attempts >= 1:
                self.respond(429, b'{"error":"single request budget exhausted"}')
                return
            attempts += 1
            receipt = {
                "model": MODEL,
                "attempts": attempts,
                "usage": None,
                "cost_usd": None,
                "estimated_ceiling_usd": str(ceiling),
                "max_cost_usd": str(budget),
                "request_sha256": hashlib.sha256(
                    json.dumps(messages, sort_keys=True).encode()
                ).hexdigest(),
            }
            store_receipt(**receipt)
        # No tools or side-channel features: only the runtime's actual text
        # context and one tiny model response are needed for this U4 check.
        body = {
            "model": MODEL,
            "messages": messages,
            "max_tokens": OUTPUT_LIMIT,
            "thinking": {"type": "disabled"},
            "stream": bool(payload.get("stream")),
        }
        if body["stream"]:
            body["stream_options"] = {"include_usage": True}
        key = os.environ["REAL_PROVIDER_KEY"]
        request = urllib.request.Request(
            UPSTREAM,
            data=json.dumps(body).encode(),
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=25) as response:
                raw = response.read(2 * 1024 * 1024)
                content_type = response.headers.get("Content-Type", "application/json")
                status = response.status
            raw = raw.replace(key.encode(), b"[redacted]")
            packets = []
            if body["stream"]:
                for line in raw.decode().splitlines():
                    if line.startswith("data: ") and line != "data: [DONE]":
                        packets.append(json.loads(line[6:]))
            else:
                packets.append(json.loads(raw))
            usage = next((p["usage"] for p in reversed(packets) if p.get("usage")), None)
            if usage:
                count_in, count_out = usage.get("prompt_tokens"), usage.get("completion_tokens")
                if all(type(n) is int and n >= 0 for n in (count_in, count_out)):
                    receipt["usage"] = {"input_tokens": count_in, "output_tokens": count_out}
                    receipt["cost_usd"] = str(
                        (count_in * INPUT_PRICE + count_out * OUTPUT_PRICE) / 1_000_000
                    )
            receipt["http_status"] = status
            store_receipt(**receipt)
            self.respond(status, raw, content_type)
        except Exception:
            store_receipt(**receipt, error="upstream_failed_or_uncertain")
            self.respond(502, b'{"error":"upstream failed or uncertain; no retry budget"}')


if __name__ == "__main__":
    store_receipt(attempts=0, usage=None, cost_usd=None)
    ThreadingHTTPServer(("0.0.0.0", 8642), Handler).serve_forever()
