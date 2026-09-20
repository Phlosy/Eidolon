"""The paid-test relay is exercised only against a local, synthetic upstream."""

import importlib.util
import json
import os
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest


def load_relay():
    path = Path(__file__).parent / "integration" / "model_budget_proxy.py"
    return load_file(path, "test_budget_relay")


def load_file(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def model_response(*, usage=None):
    return {
        "model": "deepseek-flash",
        "choices": [{"message": {"role": "assistant", "content": "OK"}}],
        "usage": usage if usage is not None else {"prompt_tokens": 5, "completion_tokens": 2},
    }


@pytest.fixture()
def relay(tmp_path, monkeypatch):
    module = load_relay()
    receipt = tmp_path / "receipt.json"
    monkeypatch.setattr(module, "RECEIPT", receipt)
    monkeypatch.setenv("RELAY_TOKEN", "local-relay-token")
    monkeypatch.setenv("REAL_PROVIDER_KEY", "synthetic-provider-key")
    monkeypatch.setenv("MAX_COST_USD", "0.05")
    monkeypatch.setenv("MAX_REQUESTS", "8")
    monkeypatch.setenv("OUTPUT_LIMIT", "32")
    monkeypatch.setenv("PRICING_VALID_UNTIL", (datetime.now(UTC) + timedelta(hours=1)).isoformat())
    state = SimpleNamespace(
        module=module,
        receipt=receipt,
        calls=[],
        reply=lambda _index, _body: (200, model_response(), {}),
    )

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state.calls.append({"path": self.path, "body": body})
            code, result, headers = state.reply(len(state.calls), body)
            raw = result if isinstance(result, bytes) else json.dumps(result).encode()
            self.send_response(code)
            self.send_header("Content-Length", str(len(raw)))
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(raw)

    servers = []

    def serve(handler):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        servers.append((server, thread))
        return f"http://127.0.0.1:{server.server_port}"

    upstream_url = serve(Upstream)
    monkeypatch.setattr(module, "UPSTREAM", upstream_url + "/fixed-model")
    state.url = serve(module.Handler)

    def post(payload=None, *, token="local-relay-token", url=None):
        request = urllib.request.Request(
            (url or state.url) + "/v1/chat/completions",
            data=json.dumps(
                payload
                if payload is not None
                else {
                    "model": "deepseek-flash",
                    "messages": [{"role": "user", "content": "hello"}],
                }
            ).encode(),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    state.post = post
    state.serve = serve
    yield state
    for server, thread in reversed(servers):
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def read_receipt(relay):
    return json.loads(relay.receipt.read_text())


def test_default_retains_one_request_and_32_output_tokens(relay, monkeypatch):
    monkeypatch.delenv("MAX_REQUESTS")
    monkeypatch.delenv("OUTPUT_LIMIT")
    assert relay.post()[0] == 200
    assert relay.post()[0] == 429
    assert len(relay.calls) == 1
    assert relay.calls[0]["body"]["max_tokens"] == 32
    receipt = read_receipt(relay)
    assert receipt["attempts"] == 1
    assert receipt["http_status"] == 200
    assert receipt["usage"] == {"input_tokens": 5, "output_tokens": 2}
    assert Decimal(receipt["cost_usd"]) == Decimal("0.0000039")


def test_known_usage_releases_only_the_unused_reservation(relay, monkeypatch):
    monkeypatch.setenv("MAX_COST_USD", "0.0003")
    monkeypatch.setenv("MAX_REQUESTS", "3")
    assert [relay.post()[0] for _ in range(4)] == [200, 200, 200, 429]
    receipt = read_receipt(relay)
    assert receipt["attempts"] == len(relay.calls) == 3
    assert len(receipt["requests"]) == 3
    assert receipt["usage"] == {"input_tokens": 15, "output_tokens": 6}
    assert Decimal(receipt["cost_usd"]) == Decimal("0.0000117")
    assert Decimal(receipt["reserved_cost_usd"]) == 0
    assert Decimal(receipt["accounted_cost_usd"]) == Decimal(receipt["cost_usd"])


def test_pending_reservation_prevents_concurrent_overspend(relay, monkeypatch):
    monkeypatch.setenv("MAX_COST_USD", "0.0003")
    entered, release = threading.Event(), threading.Event()

    def pending(_index, _body):
        entered.set()
        assert release.wait(3)
        return 200, model_response(), {}

    relay.reply = pending
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(relay.post)
        try:
            assert entered.wait(2)
            assert relay.post()[0] == 429
            pending_receipt = read_receipt(relay)
            assert Decimal(pending_receipt["reserved_cost_usd"]) > 0
            assert len(relay.calls) == 1
        finally:
            release.set()
        assert first.result()[0] == 200


def test_parallel_completions_preserve_every_receipt(relay, monkeypatch):
    monkeypatch.setenv("MAX_REQUESTS", "2")
    barrier = threading.Barrier(2)

    def pending(index, _body):
        barrier.wait(timeout=3)
        return 200, model_response(usage={"prompt_tokens": index, "completion_tokens": 1}), {}

    relay.reply = pending
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: relay.post()[0], range(2)))
    assert results == [200, 200]
    assert relay.post()[0] == 429
    receipt = read_receipt(relay)
    assert receipt["attempts"] == len(receipt["requests"]) == len(relay.calls) == 2
    assert {item["sequence"] for item in receipt["requests"]} == {1, 2}
    assert receipt["usage"] == {"input_tokens": 3, "output_tokens": 2}


@pytest.mark.parametrize(
    "result",
    [
        (503, {"error": "unavailable"}, {}),
        (200, b"broken JSON", {}),
        (200, {"model": "deepseek-flash", "choices": []}, {}),
        (200, model_response(usage={"prompt_tokens": True, "completion_tokens": 1}), {}),
        (200, model_response(usage={"prompt_tokens": -1, "completion_tokens": 1}), {}),
        (200, model_response(usage={"prompt_tokens": 5, "completion_tokens": 1000}), {}),
    ],
)
def test_failed_or_unknown_usage_keeps_budget_reserved(relay, monkeypatch, result):
    monkeypatch.setenv("MAX_COST_USD", "0.0003")
    relay.reply = lambda _index, _body: result
    assert relay.post()[0] in {200, 502}
    assert relay.post()[0] == 429
    receipt = read_receipt(relay)
    assert receipt["attempts"] == len(relay.calls) == 1
    assert receipt["usage"] is None
    assert receipt["cost_usd"] is None
    assert Decimal(receipt["reserved_cost_usd"]) == Decimal(receipt["estimated_ceiling_usd"])


def test_stream_requires_complete_terminal_usage_before_releasing_budget(relay, monkeypatch):
    monkeypatch.setenv("MAX_COST_USD", "0.0003")
    packet = json.dumps(model_response()).encode()
    relay.reply = lambda _index, _body: (200, b"data: " + packet + b"\n\n", {})
    payload = {
        "model": "deepseek-flash",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": True,
    }
    assert relay.post(payload)[0] in {200, 502}
    assert relay.post()[0] == 429
    assert read_receipt(relay)["usage"] is None


def test_complete_stream_can_settle_and_continue(relay, monkeypatch):
    monkeypatch.setenv("MAX_REQUESTS", "2")
    packet = json.dumps(model_response()).encode()
    relay.reply = lambda _index, _body: (
        200,
        b"data: " + packet + b"\n\ndata: [DONE]\n\n",
        {"Content-Type": "text/event-stream"},
    )
    payload = {
        "model": "deepseek-flash",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": True,
    }
    assert relay.post(payload)[0] == relay.post(payload)[0] == 200
    assert relay.calls[0]["body"]["stream_options"] == {"include_usage": True}
    assert read_receipt(relay)["usage"] == {"input_tokens": 10, "output_tokens": 4}


def test_only_fixed_text_model_and_configured_output_reach_upstream(relay, monkeypatch):
    monkeypatch.setenv("OUTPUT_LIMIT", "512")
    payload = {
        "model": "deepseek-flash",
        "messages": [
            {"role": "assistant", "content": "private prompt", "tool_calls": [{"id": "x"}]}
        ],
        "tools": [{"type": "function", "function": {"name": "external_call"}}],
        "max_tokens": 999999,
        "thinking": {"type": "enabled"},
    }
    assert relay.post(payload)[0] == 200
    forwarded = relay.calls[0]["body"]
    assert forwarded["messages"] == [{"role": "assistant", "content": "private prompt"}]
    assert forwarded["max_tokens"] == 512
    assert forwarded["thinking"] == {"type": "disabled"}
    assert "tools" not in forwarded
    serialized_receipt = relay.receipt.read_text()
    assert "private prompt" not in serialized_receipt
    assert "synthetic-provider-key" not in serialized_receipt
    assert "local-relay-token" not in serialized_receipt


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("MAX_REQUESTS", "0"),
        ("MAX_REQUESTS", "9"),
        ("OUTPUT_LIMIT", "31"),
        ("OUTPUT_LIMIT", "1025"),
        ("MAX_COST_USD", "0"),
        ("MAX_COST_USD", "0.051"),
        ("MAX_COST_USD", "NaN"),
        ("PRICING_VALID_UNTIL", "2020-01-01T00:00:00+00:00"),
        ("PRICING_VALID_UNTIL", "2999-01-01T00:00:00+00:00"),
        ("PRICING_VALID_UNTIL", "2999-01-01T00:00:00"),
    ],
)
def test_invalid_or_stale_budget_never_reaches_upstream(relay, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    assert relay.post()[0] == 422
    assert relay.calls == []


def test_redirect_does_not_create_an_extra_model_call(relay):
    relay.reply = lambda _index, _body: (302, {}, {"Location": relay.module.UPSTREAM})
    assert relay.post()[0] == 502
    assert len(relay.calls) == 1


def test_process_restart_does_not_reset_spent_budget(relay, monkeypatch):
    monkeypatch.setenv("MAX_REQUESTS", "1")
    assert relay.post()[0] == 200
    restarted = load_relay()
    monkeypatch.setattr(restarted, "RECEIPT", relay.receipt)
    monkeypatch.setattr(restarted, "UPSTREAM", relay.module.UPSTREAM)
    restarted_url = relay.serve(restarted.Handler)
    assert relay.post(url=restarted_url)[0] == 429
    assert len(relay.calls) == 1


def test_reservation_is_durable_before_dispatch_and_all_writes_are_atomic(relay, monkeypatch):
    real_store = relay.module.store_receipt
    real_replace = os.replace
    replacements = []

    def locked_store(**result):
        assert relay.module.lock.locked(), "receipt mutation must hold the ledger lock"
        real_store(**result)

    def replace(source, destination):
        assert Path(source).stat().st_mode & 0o777 == 0o600
        replacements.append(json.loads(Path(source).read_text()))
        real_replace(source, destination)

    def check_reserved(_index, _body):
        receipt = read_receipt(relay)
        assert receipt["attempts"] == 1
        assert Decimal(receipt["reserved_cost_usd"]) > 0
        return 200, model_response(), {}

    monkeypatch.setattr(relay.module, "store_receipt", locked_store)
    monkeypatch.setattr(os, "replace", replace)
    relay.reply = check_reserved
    assert relay.post()[0] == 200
    assert len(replacements) == 2
    assert relay.receipt.stat().st_mode & 0o777 == 0o600


def test_corrupt_existing_ledger_fails_closed(relay):
    relay.receipt.write_text("incomplete JSON")
    assert relay.post()[0] == 503
    assert relay.calls == []


def test_failed_reservation_write_never_dispatches(relay, monkeypatch):
    def unavailable(**result):
        raise OSError("synthetic disk failure")

    monkeypatch.setattr(relay.module, "store_receipt", unavailable)
    assert relay.post()[0] == 503
    assert relay.calls == []


def test_failed_settlement_keeps_durable_full_reservation(relay, monkeypatch):
    monkeypatch.setenv("MAX_COST_USD", "0.0003")
    store = relay.module.store_receipt

    def fail_settlement(**result):
        if result["requests"][0]["state"] != "reserved":
            raise OSError("synthetic disk failure after dispatch")
        store(**result)

    monkeypatch.setattr(relay.module, "store_receipt", fail_settlement)
    assert relay.post()[0] == 503
    assert relay.post()[0] == 429
    assert len(relay.calls) == 1
    receipt = read_receipt(relay)
    assert receipt["cost_usd"] is None
    assert receipt["requests"][0]["state"] == "reserved"
    assert Decimal(receipt["reserved_cost_usd"]) > 0


@pytest.mark.parametrize(
    "payload",
    [
        {"model": "other-model", "messages": [{"role": "user", "content": "hello"}]},
        {"model": "deepseek-flash", "messages": [{"role": "tool", "content": "hello"}]},
        {"model": "deepseek-flash", "messages": ["hello"]},
        {"model": "deepseek-flash", "messages": [{"role": "user", "content": [{}]}]},
        {"model": "deepseek-flash", "messages": []},
    ],
)
def test_invalid_model_or_message_cannot_spend_budget(relay, payload):
    assert relay.post(payload)[0] == 422
    assert relay.calls == []


def test_unauthed_request_cannot_spend_budget(relay):
    assert relay.post(token="wrong-token")[0] == 403
    assert relay.calls == []


def test_existing_ledger_cannot_be_reopened_with_larger_allowance(relay, monkeypatch):
    monkeypatch.setenv("MAX_REQUESTS", "1")
    assert relay.post()[0] == 200
    monkeypatch.setenv("MAX_REQUESTS", "8")
    assert relay.post()[0] == 503
    assert len(relay.calls) == 1


def native_tool_payload(*, description="Write an isolated test file", result=None):
    tools = [
        {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                    "additionalProperties": False,
                },
            },
        }
    ]
    messages = [{"role": "user", "content": "Produce the file"}]
    if result is not None:
        messages.extend(
            [
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "write-1",
                            "type": "function",
                            "function": {
                                "name": "write_file",
                                "arguments": '{"path":"output.txt"}',
                            },
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "write-1", "content": result},
            ]
        )
    return {"model": "deepseek-flash", "messages": messages, "tools": tools}


def test_explicit_native_tool_gate_preserves_definitions_calls_and_results(relay, monkeypatch):
    monkeypatch.setenv("ALLOW_NATIVE_TOOLS", "1")
    first, followup = native_tool_payload(), native_tool_payload(result="File written")
    assert relay.post(first)[0] == 200
    assert relay.calls[0]["body"]["tools"] == first["tools"]
    assert relay.post(followup)[0] == 200
    assert relay.calls[1]["body"]["messages"] == followup["messages"]
    receipt = read_receipt(relay)
    assert receipt["allow_native_tools"] is True
    assert receipt["attempts"] == 2
    assert Decimal(receipt["reserved_cost_usd"]) == 0
    assert "File written" not in relay.receipt.read_text()
    assert "write_file" not in relay.receipt.read_text()


@pytest.mark.parametrize("large_part", ["schema", "tool_result"])
def test_native_tool_schema_and_results_are_reserved_before_any_dispatch(
    relay, monkeypatch, large_part
):
    monkeypatch.setenv("ALLOW_NATIVE_TOOLS", "1")
    monkeypatch.setenv("MAX_COST_USD", "0.0003")
    payload = native_tool_payload(
        description="x" * 20_000 if large_part == "schema" else "write",
        result="x" * 20_000 if large_part == "tool_result" else None,
    )
    assert relay.post(payload)[0] == 429
    assert relay.calls == []


def test_native_tool_schema_changes_are_part_of_durable_request_digest(relay, monkeypatch):
    monkeypatch.setenv("ALLOW_NATIVE_TOOLS", "1")
    assert relay.post(native_tool_payload(description="first schema"))[0] == 200
    assert relay.post(native_tool_payload(description="changed schema"))[0] == 200
    first, second = read_receipt(relay)["requests"]
    assert first["request_sha256"] != second["request_sha256"]


def test_existing_text_ledger_cannot_enable_tools_mid_run(relay, monkeypatch):
    assert relay.post()[0] == 200
    monkeypatch.setenv("ALLOW_NATIVE_TOOLS", "1")
    assert relay.post(native_tool_payload())[0] == 503
    assert len(relay.calls) == 1


@pytest.mark.parametrize(
    "damage",
    ["non_function", "duplicate_name", "bad_schema", "orphan_result", "bad_call", "invalid_gate"],
)
def test_native_tools_reject_malformed_protocol_before_spending(relay, monkeypatch, damage):
    monkeypatch.setenv("ALLOW_NATIVE_TOOLS", "1")
    payload = native_tool_payload()
    if damage == "non_function":
        payload["tools"][0]["type"] = "web_search"
    elif damage == "duplicate_name":
        payload["tools"] *= 2
    elif damage == "bad_schema":
        payload["tools"][0]["function"]["parameters"] = "not a schema object"
    elif damage == "orphan_result":
        payload["messages"].append({"role": "tool", "tool_call_id": "unknown", "content": "done"})
    elif damage == "bad_call":
        payload = native_tool_payload(result="done")
        payload["messages"][1]["tool_calls"][0]["function"]["arguments"] = {"not": "a string"}
    elif damage == "invalid_gate":
        monkeypatch.setenv("ALLOW_NATIVE_TOOLS", "yes")
    assert relay.post(payload)[0] == 422
    assert relay.calls == []


@pytest.mark.parametrize(
    ("options", "expected"),
    [({}, (1, 32)), ({"max_requests": 4, "output_limit": 512}, (4, 512))],
)
def test_integration_fixture_passes_only_explicit_finite_allowance(
    tmp_path, monkeypatch, options, expected
):
    fixtures = load_file(Path(__file__).parent / "integration" / "conftest.py", "budget_fixtures")
    monkeypatch.setenv("EIDOLON_REAL_MODEL_TESTS", "1")
    monkeypatch.setenv("EIDOLON_TEST_PROVIDER_KEY", "synthetic-provider-key")
    monkeypatch.setenv("EIDOLON_TEST_MODEL_BUDGET_USD", "0.05")
    expires = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    monkeypatch.setenv("EIDOLON_TEST_PRICING_VALID_UNTIL", expires)
    created = []

    def create(**kwargs):
        created.append(kwargs)
        for source, mount in kwargs["volumes"].items():
            if mount["bind"] == "/evidence":
                (Path(source) / "receipt.json").write_text("{}")
        return "synthetic-container-id"

    docker = SimpleNamespace(
        inspect_image=lambda _tag: {"Id": "fixed-local-image"},
        ensure_network=lambda _name: True,
        create_container=create,
        start_container=lambda _cid: True,
    )
    generator = fixtures.model_relay.__wrapped__(
        SimpleNamespace(param=options), docker, "synthetic-network", [], tmp_path
    )
    try:
        result = next(generator)
        assert (result.max_requests, result.output_limit) == expected
        environment = created[0]["environment"]
        assert (environment["MAX_REQUESTS"], environment["OUTPUT_LIMIT"]) == tuple(
            map(str, expected)
        )
        assert environment["PRICING_VALID_UNTIL"] == expires
        assert environment["MAX_COST_USD"] == "0.05"
    finally:
        generator.close()
