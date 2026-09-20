"""Immutable bytes of a canonical DriveRevision, never a second artifact store.

Database references publish history. A rolled-back transaction may leave an
unreferenced, complete file; it is never discovered as a document, and a retry
must verify it before reuse. Only the host writes this private Drive directory.
"""

import hashlib
import os
import re
import stat
from contextlib import ExitStack, contextmanager
from pathlib import Path
from uuid import uuid4

from app.core.config import settings

MAX_CONTENT_BYTES = 256 * 1024


class RevisionContentError(ValueError):
    def __init__(self):
        super().__init__("revision content unavailable or invalid")


def _location(node_id: int, version: int, digest: str) -> tuple[str, str]:
    if (
        type(node_id) is not int
        or node_id < 1
        or type(version) is not int
        or version < 1
        or not isinstance(digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", digest) is None
    ):
        raise RevisionContentError()
    filename = f"{version}-{digest}.txt"
    return f"drive/.revisions/{node_id}/{filename}", filename


@contextmanager
def _directory(node_id: int, *, create: bool = False):
    root = Path(settings.data_root)
    if not root.is_absolute():
        raise RevisionContentError()
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    with ExitStack() as stack:
        descriptor = os.open(root, flags)
        stack.callback(os.close, descriptor)
        for part in ("drive", ".revisions", str(node_id)):
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
                else:
                    os.fsync(descriptor)
            descriptor = os.open(part, flags, dir_fd=descriptor)
            stack.callback(os.close, descriptor)
        yield descriptor


def _stamp(info):
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
        info.st_nlink,
    )


def _read(directory: int, filename: str, digest: str) -> bytes:
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    descriptor = os.open(filename, flags, dir_fd=directory)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size > MAX_CONTENT_BYTES
        ):
            raise RevisionContentError()
        body = stream.read(MAX_CONTENT_BYTES + 1)
        after = os.fstat(stream.fileno())
        current = os.stat(filename, dir_fd=directory, follow_symlinks=False)
        if (
            _stamp(before) != _stamp(after)
            or _stamp(after) != _stamp(current)
            or len(body) != before.st_size
            or len(body) > MAX_CONTENT_BYTES
            or hashlib.sha256(body).hexdigest() != digest
        ):
            raise RevisionContentError()
    return body


def preserve_content(node_id: int, version: int, content: str) -> str:
    """Publish fully written bytes without ever replacing an existing revision file."""
    try:
        body = content.encode("utf-8")
        if len(body) > MAX_CONTENT_BYTES:
            raise RevisionContentError()
        digest = hashlib.sha256(body).hexdigest()
        relative, filename = _location(node_id, version, digest)
        with _directory(node_id, create=True) as directory:
            temporary = f".pending-{uuid4().hex}"
            try:
                descriptor = os.open(
                    temporary,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                    0o600,
                    dir_fd=directory,
                )
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(body)
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    os.link(
                        temporary,
                        filename,
                        src_dir_fd=directory,
                        dst_dir_fd=directory,
                        follow_symlinks=False,
                    )
                except FileExistsError:
                    pass
            finally:
                try:
                    os.unlink(temporary, dir_fd=directory)
                except FileNotFoundError:
                    pass
            os.fsync(directory)
            if _read(directory, filename, digest) != body:
                raise RevisionContentError()
        return relative
    except (OSError, UnicodeError, AttributeError, TypeError, ValueError):
        raise RevisionContentError() from None


def read_revision_content(node, revision) -> str | None:
    """Read an authorized node's exact revision; NULL means historical bytes unknown."""
    if revision.node_id != node.id or node.kind != "document":
        raise RevisionContentError()
    if revision.content_path is None:
        return None
    try:
        relative, filename = _location(node.id, revision.version, revision.sha256)
        if revision.content_path != relative:
            raise RevisionContentError()
        with _directory(node.id) as directory:
            return _read(directory, filename, revision.sha256).decode("utf-8")
    except (OSError, UnicodeError, TypeError, ValueError):
        raise RevisionContentError() from None
