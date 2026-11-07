"""Idempotency ledger: which side-effecting executions already happened.

The agent loop hands every execution an idempotency key and, after a crash, hands
the *same* key again (v2-ADR-8). The runner answers a repeated key with the stored
result instead of running the tool twice. The ledger is an append-only JSON-lines
file inside the workspace's ``.eidolon/`` directory, so it survives runner restarts.
"""

import json
import time
from pathlib import Path
from typing import Any


class Ledger:
    def __init__(self, path: Path):
        self.path = path
        self._entries: dict[str, dict[str, Any]] = {}
        if path.exists():
            with path.open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    entry = json.loads(line)
                    self._entries[entry["key"]] = entry["result"]

    def get(self, key: str) -> dict[str, Any] | None:
        return self._entries.get(key)

    def put(self, key: str, result: dict[str, Any]) -> None:
        self._entries[key] = result
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"key": key, "result": result, "at": time.time()}) + "\n")
            fh.flush()

    def __len__(self) -> int:
        return len(self._entries)
