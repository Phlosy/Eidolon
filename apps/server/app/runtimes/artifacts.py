"""Bounded artifact transport values. No domain writes or inferred business success."""

import hashlib

from app.runtimes.base import ArtifactOrigin, ProducedArtifact, RuntimeProtocolError, RuntimeSession

MAX_ARTIFACT_BYTES = 256 * 1024
MAX_COLLECTION_BYTES = 1024 * 1024
MAX_ARTIFACTS = 32


def require_completed_origin(session: RuntimeSession) -> None:
    if session.status != "completed":
        raise RuntimeProtocolError("artifact_run_not_completed")
    if any(
        not isinstance(value, str) or not value or len(value) > 256
        for value in (session.id, session.remote_run_id, session.remote_session_ref)
    ):
        raise RuntimeProtocolError("artifact_origin_missing")


def text_artifact(
    session: RuntimeSession,
    *,
    source_ref: str,
    title: str,
    body: bytes,
    artifact_type: str = "other",
) -> ProducedArtifact:
    if len(body) > MAX_ARTIFACT_BYTES:
        raise RuntimeProtocolError("artifact_size_limit")
    try:
        content = body.decode("utf-8")
    except UnicodeDecodeError:
        raise RuntimeProtocolError("artifact_format_unsupported") from None
    if "\0" in content:
        raise RuntimeProtocolError("artifact_format_unsupported")
    return ProducedArtifact(
        type=artifact_type,
        title=title,
        content=content,
        origin=ArtifactOrigin(
            local_session_ref=session.id,
            remote_run_id=session.remote_run_id,
            remote_session_ref=session.remote_session_ref,
            source_ref=source_ref,
            size_bytes=len(body),
            sha256=hashlib.sha256(body).hexdigest(),
        ),
    )
