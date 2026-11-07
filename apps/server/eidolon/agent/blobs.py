"""Content-addressed blob store for transcripts.

Facts stay small (hash + summary, K4); the full model context, the full response
and full tool results live here under ``sha256:<hex>`` and are referenced from
the facts. A blob is written once (atomic rename) and never changed, so a ref in
a fact is as immutable as the fact. Local directory now; the interface is what
an S3 backend would implement later.
"""

import hashlib
import json
import os
from pathlib import Path
from typing import Any

PREFIX = "sha256:"


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


class BlobMissing(LookupError):
    pass


class BlobStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def _path(self, ref: str) -> Path:
        if not ref.startswith(PREFIX) or len(ref) != len(PREFIX) + 64:
            raise ValueError(f"not a blob ref: {ref!r}")
        hexdigest = ref[len(PREFIX) :]
        return self.root / hexdigest[:2] / hexdigest[2:4] / hexdigest

    def put_bytes(self, data: bytes) -> str:
        ref = PREFIX + hashlib.sha256(data).hexdigest()
        path = self._path(ref)
        if path.exists():
            return ref
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + f".tmp{os.getpid()}")
        tmp.write_bytes(data)
        os.replace(tmp, path)
        return ref

    def put_json(self, value: Any) -> str:
        return self.put_bytes(canonical_json(value))

    def get_bytes(self, ref: str) -> bytes:
        path = self._path(ref)
        if not path.exists():
            raise BlobMissing(f"{ref} not in {self.root}")
        data = path.read_bytes()
        if PREFIX + hashlib.sha256(data).hexdigest() != ref:
            raise BlobMissing(f"{ref} is corrupt on disk")
        return data

    def get_json(self, ref: str) -> Any:
        return json.loads(self.get_bytes(ref).decode("utf-8"))

    def exists(self, ref: str) -> bool:
        return self._path(ref).exists()
