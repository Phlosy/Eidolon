"""JSON-RPC 2.0 over stdio, one object per line.

    → {"jsonrpc":"2.0","id":1,"method":"runner.hello"}
    ← {"jsonrpc":"2.0","id":1,"result":{"protocol":..,"version":..,"workspace":..,"tools":[...]}}
    → {"jsonrpc":"2.0","id":2,"method":"tool.execute",
       "params":{"name":"write_file","arguments":{...},"idempotency_key":"k1"}}
    ← {"jsonrpc":"2.0","id":2,"result":{"content":..,"is_error":false,"truncated":false,
                                        "replayed":false,"duration_ms":3}}
    → {"jsonrpc":"2.0","id":3,"method":"runner.shutdown"}

Tool failures are *results* with ``is_error`` (the model is meant to see them);
JSON-RPC errors are reserved for malformed requests. Logs go to stderr as JSON lines.
"""

import json
import os
import re
import sys
import time
from typing import Any

from eidolon_runner import PROTOCOL, VERSION
from eidolon_runner.ledger import Ledger
from eidolon_runner.sandbox import Sandbox
from eidolon_runner.tools import SIDE_EFFECTS, TOOLS, Limits, Tools

PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS = -32700, -32600, -32601, -32602
SECRET_LIKE = re.compile(r"(KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL)", re.IGNORECASE)


def scrubbed_environ() -> dict[str, str]:
    """Whatever the parent passed, minus anything that looks like a secret. The
    client already allow-lists; this is the second lock on the same door."""
    return {k: v for k, v in os.environ.items() if not SECRET_LIKE.search(k)}


class Server:
    def __init__(self, workspace: str, limits: Limits):
        self.sandbox = Sandbox(workspace)
        self.limits = limits
        env = scrubbed_environ()
        env["EIDOLON_WORKSPACE"] = str(self.sandbox.workspace)
        self.tools = Tools(self.sandbox, limits, env)
        self.ledger = Ledger(self.sandbox.internal / "runner-ledger.jsonl")
        self.running = True

    # -- methods -----------------------------------------------------------------

    def hello(self, _params: dict[str, Any]) -> dict[str, Any]:
        return {
            "protocol": PROTOCOL,
            "version": VERSION,
            "workspace": str(self.sandbox.workspace),
            "pid": os.getpid(),
            "limits": {
                "max_output_bytes": self.limits.max_output_bytes,
                "default_timeout_s": self.limits.default_timeout_s,
                "max_timeout_s": self.limits.max_timeout_s,
            },
            "tools": TOOLS,
            "ledger_entries": len(self.ledger),
        }

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        arguments = params.get("arguments") or {}
        key = params.get("idempotency_key")
        if not isinstance(name, str) or not isinstance(arguments, dict):
            raise _Invalid("name (string) and arguments (object) are required")
        if key is not None and not isinstance(key, str):
            raise _Invalid("idempotency_key must be a string")
        side_effect = SIDE_EFFECTS.get(name, "WRITE")
        if key and side_effect != "READ":
            stored = self.ledger.get(key)
            if stored is not None:
                return {**stored, "replayed": True}
        started = time.monotonic()
        result = self.tools.execute(name, arguments).as_dict()
        result["duration_ms"] = round((time.monotonic() - started) * 1000)
        if key and side_effect != "READ":
            self.ledger.put(key, result)
        return {**result, "replayed": False}

    def shutdown(self, _params: dict[str, Any]) -> dict[str, Any]:
        self.running = False
        return {"ok": True}

    METHODS = {"runner.hello": hello, "tool.execute": execute, "runner.shutdown": shutdown}

    # -- transport ---------------------------------------------------------------

    def handle(self, line: str) -> dict[str, Any] | None:
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            return _error(None, PARSE_ERROR, f"parse error: {exc}")
        if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
            return _error(None, INVALID_REQUEST, "not a JSON-RPC 2.0 request")
        rid = request.get("id")
        method = self.METHODS.get(request.get("method"))
        if method is None:
            return _error(rid, METHOD_NOT_FOUND, f"unknown method {request.get('method')!r}")
        params = request.get("params") or {}
        if not isinstance(params, dict):
            return _error(rid, INVALID_PARAMS, "params must be an object")
        try:
            return {"jsonrpc": "2.0", "id": rid, "result": method(self, params)}
        except _Invalid as exc:
            return _error(rid, INVALID_PARAMS, str(exc))

    def serve(self, stdin=sys.stdin, stdout=sys.stdout) -> int:
        log("runner.started", workspace=str(self.sandbox.workspace), pid=os.getpid())
        for line in stdin:
            if not line.strip():
                continue
            response = self.handle(line)
            if response is not None:
                stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                stdout.flush()
            if not self.running:
                break
        log("runner.stopped")
        return 0


class _Invalid(ValueError):
    pass


def _error(rid: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def log(msg: str, **extra: Any) -> None:
    sys.stderr.write(
        json.dumps({"ts": time.time(), "logger": "eidolon.runner", "msg": msg, **extra}) + "\n"
    )
    sys.stderr.flush()
