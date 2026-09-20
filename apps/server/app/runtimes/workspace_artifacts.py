"""Read a complete, attempt-bound manifest inside the managed workspace.

The workspace root is host configuration. Every runtime-controlled descendant
is opened relative to a pinned directory descriptor without following links.
This module returns transport evidence and never creates domain records.
"""

import hashlib
import json
import os
import re
import stat
from contextlib import ExitStack, contextmanager
from pathlib import Path

from app.models.enums import ArtifactType
from app.runtimes.artifacts import (
    MAX_ARTIFACT_BYTES,
    MAX_ARTIFACTS,
    MAX_COLLECTION_BYTES,
    require_completed_origin,
    text_artifact,
)
from app.runtimes.base import (
    ArtifactWorkspace,
    ProducedArtifact,
    RuntimeProtocolError,
    RuntimeSession,
)
from app.runtimes.paths import WORKSPACE_MOUNTS

PROTOCOL = "eidolon.artifacts.v1"
MAX_MANIFEST_BYTES = 64 * 1024
_MANIFEST_KEYS = {"protocol", "local_session_ref", "request_ref", "complete", "artifacts"}
_FILE_KEYS = {"path", "type", "title", "size_bytes", "sha256"}
_TYPES = {item.value for item in ArtifactType}


def output_directory(session: RuntimeSession) -> str:
    request = getattr(session.attempt, "idempotency_key", None)
    if not isinstance(request, str) or not request or len(request) > 200:
        raise RuntimeProtocolError("artifact_request_missing")
    if not isinstance(session.id, str) or not session.id or len(session.id) > 200:
        raise RuntimeProtocolError("artifact_origin_missing")

    def digest(value):
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    return f".eidolon/outputs/{digest(session.id)}/{digest(request)}"


def instructions(
    session: RuntimeSession, runtime_type: str, *, required_types: list[str] | None = None
) -> str:
    directory = f"{WORKSPACE_MOUNTS[runtime_type]}/{output_directory(session)}"
    contract = {
        "directory": directory,
        "manifest": {
            "protocol": PROTOCOL,
            "local_session_ref": session.id,
            "request_ref": session.attempt.idempotency_key,
            "complete": True,
            "artifacts": [
                {
                    "path": "result.md",
                    "type": "other",
                    "title": "Result",
                    "size_bytes": "actual byte count",
                    "sha256": "actual lowercase SHA-256",
                }
            ],
        },
        "allowed_types": sorted(_TYPES),
        "required_types": list(required_types or []),
        "max_file_bytes": MAX_ARTIFACT_BYTES,
        "max_collection_bytes": MAX_COLLECTION_BYTES,
        "max_files": MAX_ARTIFACTS,
    }
    return (
        "\n\n## Required output protocol\n"
        "Create the directory below and write the task's actual UTF-8 deliverable files there. "
        "Copy the full directory path verbatim, including repeated path components; never "
        "shorten, recompute or deduplicate it. "
        "Cover every required type. Use regular files, relative paths, and an appropriate "
        "allowed type; do not link files. "
        "Compute byte counts and SHA-256 from the actual finished bytes. When EVERY file is "
        "complete, write the exact manifest schema to a temporary file and atomically rename "
        "it to manifest.json in that exact directory. Verify the manifest and deliverables "
        "exist at the full supplied path before finishing. Do not add fields. A reply alone is "
        "not a file deliverable. The manifest is collection metadata, not a review verdict.\n"
        + json.dumps(contract, ensure_ascii=False, separators=(",", ":"))
    )


def _parts(path: object) -> list[str]:
    if (
        not isinstance(path, str)
        or not path
        or len(path) > 500
        or "\\" in path
        or any(ord(char) < 32 for char in path)
        or any(part in {"", ".", ".."} for part in path.split("/"))
    ):
        raise RuntimeProtocolError("artifact_path_invalid")
    return path.split("/")


@contextmanager
def _directory(root: str, parts: list[str], expected: ArtifactWorkspace | None = None):
    if not Path(root).is_absolute():
        raise RuntimeProtocolError("artifact_path_unsafe")
    with ExitStack() as stack:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        descriptor = os.open(root, flags)
        stack.callback(os.close, descriptor)
        info = os.fstat(descriptor)
        if expected is not None and expected != ArtifactWorkspace(root, info.st_dev, info.st_ino):
            raise RuntimeProtocolError("artifact_workspace_changed")
        for part in parts:
            descriptor = os.open(part, flags, dir_fd=descriptor)
            stack.callback(os.close, descriptor)
        yield descriptor


def capture_workspace(path: str) -> ArtifactWorkspace:
    try:
        with _directory(path, []) as descriptor:
            info = os.fstat(descriptor)
            return ArtifactWorkspace(path, info.st_dev, info.st_ino)
    except (OSError, RuntimeProtocolError, TypeError, ValueError):
        raise RuntimeProtocolError("artifact_workspace_unavailable") from None


def _stamp(info):
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
        info.st_nlink,
    )


def _read(directory: int, relative: str, limit: int) -> bytes:
    parts = _parts(relative)
    with ExitStack() as stack:
        parent = directory
        for part in parts[:-1]:
            parent = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent
            )
            stack.callback(os.close, parent)
        fd = os.open(
            parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent
        )
        stack.callback(os.close, fd)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise RuntimeProtocolError("artifact_path_unsafe")
        if before.st_size > limit:
            raise RuntimeProtocolError("artifact_size_limit")
        chunks, remaining = [], limit + 1
        while remaining:
            chunk = os.read(fd, min(remaining, 64 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        body = b"".join(chunks)
        after = os.fstat(fd)
        current = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
        if _stamp(before) != _stamp(after) or _stamp(after) != _stamp(current):
            raise RuntimeProtocolError("artifact_file_changed")
        if len(body) > limit:
            raise RuntimeProtocolError("artifact_size_limit")
        return body


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate manifest key")
        result[key] = value
    return result


def _manifest(body: bytes, session: RuntimeSession) -> list[dict]:
    try:
        value = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_object)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise RuntimeProtocolError("artifact_manifest_invalid") from None
    if (
        not isinstance(value, dict)
        or value.keys() != _MANIFEST_KEYS
        or value["protocol"] != PROTOCOL
    ):
        raise RuntimeProtocolError("artifact_manifest_invalid")
    if (
        value["local_session_ref"] != session.id
        or value["request_ref"] != session.attempt.idempotency_key
    ):
        raise RuntimeProtocolError("artifact_origin_mismatch")
    if value["complete"] is not True:
        raise RuntimeProtocolError("artifact_manifest_incomplete")
    items = value["artifacts"]
    if not isinstance(items, list):
        raise RuntimeProtocolError("artifact_manifest_invalid")
    if len(items) > MAX_ARTIFACTS:
        raise RuntimeProtocolError("artifact_count_limit")
    seen, total = set(), 0
    for item in items:
        if not isinstance(item, dict) or item.keys() != _FILE_KEYS:
            raise RuntimeProtocolError("artifact_manifest_invalid")
        _parts(item["path"])
        if item["path"] == "manifest.json":
            raise RuntimeProtocolError("artifact_path_invalid")
        if item["path"] in seen:
            raise RuntimeProtocolError("artifact_duplicate_source")
        seen.add(item["path"])
        if (
            not isinstance(item["type"], str)
            or item["type"] not in _TYPES
            or not isinstance(item["title"], str)
            or not item["title"].strip()
            or len(item["title"]) > 300
            or any(ord(char) < 32 for char in item["title"])
            or type(item["size_bytes"]) is not int
            or item["size_bytes"] < 0
            or not isinstance(item["sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
        ):
            raise RuntimeProtocolError("artifact_manifest_invalid")
        total += item["size_bytes"]
        if item["size_bytes"] > MAX_ARTIFACT_BYTES or total > MAX_COLLECTION_BYTES:
            raise RuntimeProtocolError("artifact_size_limit")
    return items


def collect(session: RuntimeSession) -> list[ProducedArtifact]:
    require_completed_origin(session)

    def identity():
        return (
            session.id,
            session.remote_run_id,
            session.remote_session_ref,
            getattr(session.attempt, "idempotency_key", None),
            session.instance.home_path,
            session.artifact_workspace,
        )

    original = identity()
    root, expected = session.instance.home_path, session.artifact_workspace
    relative = output_directory(session)
    try:
        with _directory(root, _parts(relative), expected) as directory:
            body = _read(directory, "manifest.json", MAX_MANIFEST_BYTES)
            items = _manifest(body, session)
            collected = []
            for item in items:
                content = _read(directory, item["path"], MAX_ARTIFACT_BYTES)
                if len(content) != item["size_bytes"]:
                    raise RuntimeProtocolError("artifact_size_mismatch")
                if hashlib.sha256(content).hexdigest() != item["sha256"]:
                    raise RuntimeProtocolError("artifact_hash_mismatch")
                collected.append(
                    text_artifact(
                        session,
                        source_ref=item["path"],
                        title=item["title"],
                        body=content,
                        artifact_type=item["type"],
                    )
                )
            if _read(directory, "manifest.json", MAX_MANIFEST_BYTES) != body:
                raise RuntimeProtocolError("artifact_file_changed")
            require_completed_origin(session)
            if original != identity():
                raise RuntimeProtocolError("artifact_origin_mismatch")
            if expected is not None and capture_workspace(root) != expected:
                raise RuntimeProtocolError("artifact_workspace_changed")
            return collected
    except FileNotFoundError:
        raise RuntimeProtocolError("artifact_file_missing") from None
    except OSError:
        raise RuntimeProtocolError("artifact_path_unsafe") from None
