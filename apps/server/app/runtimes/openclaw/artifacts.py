"""Read text artifacts using the pinned gateway's list/download contract."""

import asyncio
import base64
import binascii
import re

from app.runtimes.artifacts import (
    MAX_ARTIFACT_BYTES,
    MAX_ARTIFACTS,
    MAX_COLLECTION_BYTES,
    require_completed_origin,
    text_artifact,
)
from app.runtimes.base import ProducedArtifact, RuntimeProtocolError, RuntimeSession

_SUMMARY_FIELDS = (
    "id",
    "type",
    "title",
    "mimeType",
    "sizeBytes",
    "sessionKey",
    "runId",
    "taskId",
    "messageSeq",
    "source",
    "download",
)


def _summary(value: object, session: RuntimeSession) -> dict:
    if not isinstance(value, dict) or not isinstance(value.get("id"), str):
        raise RuntimeProtocolError("artifact_list_invalid")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value["id"]):
        raise RuntimeProtocolError("artifact_list_invalid")
    if not isinstance(value.get("runId"), str) or not isinstance(value.get("sessionKey"), str):
        raise RuntimeProtocolError("artifact_list_invalid")
    if (
        value.get("runId") != session.remote_run_id
        or value.get("sessionKey") != session.remote_session_ref
    ):
        raise RuntimeProtocolError("artifact_origin_mismatch")
    title, size = value.get("title"), value.get("sizeBytes")
    if (
        not isinstance(title, str)
        or not title.strip()
        or len(title) > 300
        or any(ord(char) < 32 for char in title)
        or value.get("source") != "session-transcript"
        or type(value.get("messageSeq")) is not int
        or value["messageSeq"] < 1
        or (size is not None and (type(size) is not int or size < 0))
    ):
        raise RuntimeProtocolError("artifact_list_invalid")
    if size is not None and size > MAX_ARTIFACT_BYTES:
        raise RuntimeProtocolError("artifact_size_limit")
    mime = value.get("mimeType")
    if value.get("type") != "file" or (
        mime is not None
        and (
            not isinstance(mime, str)
            or not (
                mime.startswith("text/")
                or mime
                in {
                    "application/json",
                    "application/javascript",
                    "application/xml",
                    "application/octet-stream",
                }
            )
        )
    ):
        raise RuntimeProtocolError("artifact_format_unsupported")
    if value.get("download") != {"mode": "bytes"}:
        raise RuntimeProtocolError("artifact_download_unsupported")
    return {key: value.get(key) for key in _SUMMARY_FIELDS}


async def collect(client, session: RuntimeSession) -> list[ProducedArtifact]:
    require_completed_origin(session)
    scope = {"sessionKey": session.remote_session_ref, "runId": session.remote_run_id}
    local_ref = session.id

    def check_origin():
        require_completed_origin(session)
        if (
            session.id != local_ref
            or session.remote_run_id != scope["runId"]
            or session.remote_session_ref != scope["sessionKey"]
        ):
            raise RuntimeProtocolError("artifact_origin_mismatch")

    try:
        async with asyncio.timeout(30):
            result = await client.call("artifacts.list", scope)
            check_origin()
            items = result.get("artifacts") if isinstance(result, dict) else None
            if not isinstance(items, list):
                raise RuntimeProtocolError("artifact_list_invalid")
            if len(items) > MAX_ARTIFACTS:
                raise RuntimeProtocolError("artifact_count_limit")
            summaries = [_summary(item, session) for item in items]
            if len({item["id"] for item in summaries}) != len(summaries):
                raise RuntimeProtocolError("artifact_duplicate_source")
            collected, total = [], 0
            for expected in summaries:
                downloaded = await client.call(
                    "artifacts.download", {**scope, "artifactId": expected["id"]}
                )
                check_origin()
                if not isinstance(downloaded, dict):
                    raise RuntimeProtocolError("artifact_content_invalid")
                actual = _summary(downloaded.get("artifact"), session)
                if actual != expected:
                    raise RuntimeProtocolError("artifact_metadata_changed")
                data = downloaded.get("data")
                if downloaded.get("encoding") != "base64" or not isinstance(data, str):
                    raise RuntimeProtocolError("artifact_content_invalid")
                if len(data) > 4 * ((MAX_ARTIFACT_BYTES + 2) // 3):
                    raise RuntimeProtocolError("artifact_size_limit")
                try:
                    body = base64.b64decode(data, validate=True)
                except (ValueError, binascii.Error):
                    raise RuntimeProtocolError("artifact_content_invalid") from None
                if expected["sizeBytes"] is not None and len(body) != expected["sizeBytes"]:
                    raise RuntimeProtocolError("artifact_size_mismatch")
                total += len(body)
                if total > MAX_COLLECTION_BYTES:
                    raise RuntimeProtocolError("artifact_size_limit")
                collected.append(
                    text_artifact(
                        session, source_ref=expected["id"], title=expected["title"], body=body
                    )
                )
            return collected
    except RuntimeProtocolError:
        raise
    except Exception:
        raise RuntimeProtocolError("artifact_transport_failed") from None
