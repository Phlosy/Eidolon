"""Offline Provider fixture: hold one response until release or a finite deadline.

No upstream URL, credentials or network client exists in this process. The native
runtime performs its real HTTP request here; no model or token usage is claimed.
Only request counts/stream mode/connection outcome are recorded, never prompts.
"""

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path("/evidence")
TOKEN = os.environ["TEST_TOKEN"]
LOCK = threading.Lock()
STATE = {"kind": "offline-held-provider", "paid_requests": 0, "requests": []}


def save():
    staging = ROOT / "receipt.json.new"
    staging.write_text(json.dumps(STATE))
    staging.replace(ROOT / "receipt.json")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def authorized(self):
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            self.send_error(401)
            return False
        return True

    def do_GET(self):
        if not self.authorized():
            return
        if self.path != "/v1/models":
            self.send_error(404)
            return
        payload = json.dumps({"object": "list", "data": [{"id": "held-test-model"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        if not self.authorized():
            return
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return
        size = int(self.headers.get("Content-Length", "0"))
        if not 0 < size <= 2_000_000:
            self.send_error(413)
            return
        body = json.loads(self.rfile.read(size))
        stream = body.get("stream") is True
        with LOCK:
            if len(STATE["requests"]) >= 4:
                self.send_error(429)
                return
            record = {"stream": stream, "state": "held"}
            STATE["requests"].append(record)
            save()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream" if stream else "application/json")
        self.end_headers()
        deadline = time.monotonic() + 90
        try:
            while not (ROOT / "release").exists() and time.monotonic() < deadline:
                if stream:
                    self.wfile.write(b": held\n\n")
                    self.wfile.flush()
                time.sleep(0.2)
            if stream:
                payload = {
                    "id": "offline-held-response",
                    "object": "chat.completion.chunk",
                    "model": "held-test-model",
                    "choices": [
                        {"index": 0, "delta": {"content": "released"}, "finish_reason": None}
                    ],
                }
                self.wfile.write(f"data: {json.dumps(payload)}\n\n".encode())
                payload["choices"] = [{"index": 0, "delta": {}, "finish_reason": "stop"}]
                self.wfile.write(f"data: {json.dumps(payload)}\n\ndata: [DONE]\n\n".encode())
            else:
                self.wfile.write(
                    json.dumps(
                        {
                            "id": "offline-held-response",
                            "object": "chat.completion",
                            "model": "held-test-model",
                            "choices": [
                                {
                                    "index": 0,
                                    "message": {"role": "assistant", "content": "released"},
                                    "finish_reason": "stop",
                                }
                            ],
                        }
                    ).encode()
                )
            self.wfile.flush()
            outcome = "released" if (ROOT / "release").exists() else "deadline"
        except (BrokenPipeError, ConnectionResetError):
            outcome = "disconnected"
        finally:
            with LOCK:
                record["state"] = locals().get("outcome", "transport_error")
                save()


if __name__ == "__main__":
    save()
    ThreadingHTTPServer(("0.0.0.0", 8642), Handler).serve_forever()
