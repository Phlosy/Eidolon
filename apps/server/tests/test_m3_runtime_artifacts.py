"""U14 artifact bytes and origin: real adapter code over a protocol-only double."""

import base64
import hashlib
from copy import deepcopy

import pytest
from test_m3_runtime_turns import session

from app.runtimes.base import RuntimeProtocolError
from app.runtimes.openclaw.adapter import OpenClawAdapter

BODY = "# Result\n\n原始内容。\r\n"
RUN = "original-run"
REMOTE_SESSION = "agent:main:eidolon-original"


class ArtifactGateway:
    """Shape copied from the pinned 2026.8.1 artifacts handler, not production output."""

    def __init__(self):
        self.calls = []
        self.summary = {
            "id": "artifact_original",
            "type": "file",
            "title": "result.md",
            "mimeType": "text/markdown",
            "sizeBytes": len(BODY.encode("utf-8")),
            "sessionKey": REMOTE_SESSION,
            "runId": RUN,
            "messageSeq": 3,
            "source": "session-transcript",
            "download": {"mode": "bytes"},
        }
        self.listed = [deepcopy(self.summary)]
        self.download = {
            "artifact": deepcopy(self.summary),
            "encoding": "base64",
            "data": base64.b64encode(BODY.encode("utf-8")).decode("ascii"),
        }
        self.failure = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def call(self, method, params, **kwargs):
        self.calls.append((method, params))
        if self.failure:
            raise self.failure
        if method == "artifacts.list":
            return {"artifacts": deepcopy(self.listed)}
        if method == "artifacts.get":
            return {"artifact": deepcopy(self.summary)}
        if method == "artifacts.download":
            return deepcopy(self.download)
        raise AssertionError(method)


@pytest.fixture()
def rpc_artifacts(monkeypatch):
    adapter, current, client = OpenClawAdapter(), session("artifact-local"), ArtifactGateway()
    current.status = "completed"
    current.remote_run_id, current.remote_session_ref = RUN, REMOTE_SESSION
    # Deliberately stale legacy cache: U13's explicit original references are authoritative.
    current.instance.details[f"run_id:{current.id}"] = "stale-cache-run"
    monkeypatch.setattr(adapter, "_connect", lambda instance: client)
    return adapter, current, client


async def test_openclaw_downloads_exact_bytes_from_original_run_and_does_not_append_on_replay(
    rpc_artifacts,
):
    adapter, current, client = rpc_artifacts
    first = await adapter.get_artifacts(current)
    second = await adapter.get_artifacts(current)
    assert len(first) == len(second) == 1
    assert first[0].content == BODY
    assert second[0].content == BODY
    assert first[0].type == "other"  # Native file metadata does not invent a business type.
    origin = first[0].origin
    assert origin.local_session_ref == current.id
    assert origin.remote_run_id == RUN
    assert origin.remote_session_ref == REMOTE_SESSION
    assert origin.source_ref == "artifact_original"
    assert origin.sha256 == hashlib.sha256(BODY.encode("utf-8")).hexdigest()
    assert origin.size_bytes == len(BODY.encode("utf-8"))
    assert current.artifacts == []  # Never partially cache an unaccepted collection.
    assert any(method == "artifacts.download" for method, _ in client.calls)
    for method, params in client.calls:
        assert params["runId"] == RUN
        assert params["sessionKey"] == REMOTE_SESSION
        if method != "artifacts.list":
            assert params["artifactId"] == "artifact_original"
            assert "id" not in params


@pytest.mark.parametrize("where", ["list", "download"])
@pytest.mark.parametrize("field,value", [("runId", "another-run"), ("sessionKey", "another")])
async def test_openclaw_rejects_artifacts_from_another_execution(
    rpc_artifacts, where, field, value
):
    adapter, current, client = rpc_artifacts
    target = client.listed[0] if where == "list" else client.download["artifact"]
    target[field] = value
    with pytest.raises(RuntimeProtocolError, match="artifact_origin_mismatch"):
        await adapter.get_artifacts(current)
    assert current.artifacts == []


@pytest.mark.parametrize("status", ["running", "failed", "cancelled"])
async def test_openclaw_does_not_collect_non_completed_runs(rpc_artifacts, status):
    adapter, current, client = rpc_artifacts
    current.status = status
    with pytest.raises(RuntimeProtocolError, match="artifact_run_not_completed"):
        await adapter.get_artifacts(current)
    assert client.calls == []


async def test_openclaw_requires_explicit_original_run_even_with_legacy_cache(rpc_artifacts):
    adapter, current, client = rpc_artifacts
    current.remote_run_id = None
    with pytest.raises(RuntimeProtocolError, match="artifact_origin_missing"):
        await adapter.get_artifacts(current)
    assert client.calls == []


@pytest.mark.parametrize(
    "damage,reason",
    [
        ("duplicate", "artifact_duplicate_source"),
        ("malformed", "artifact_list_invalid"),
        ("oversize", "artifact_size_limit"),
        ("url", "artifact_download_unsupported"),
        ("invalid_base64", "artifact_content_invalid"),
        ("length_mismatch", "artifact_size_mismatch"),
        ("changed_summary", "artifact_metadata_changed"),
        ("binary", "artifact_format_unsupported"),
        ("partial_second", "artifact_list_invalid"),
    ],
)
async def test_openclaw_rejects_entire_invalid_collection(rpc_artifacts, damage, reason):
    adapter, current, client = rpc_artifacts
    if damage == "duplicate":
        client.listed.append(deepcopy(client.listed[0]))
    elif damage == "malformed":
        client.listed = {"not": "a list"}
    elif damage == "oversize":
        client.listed[0]["sizeBytes"] = 262_145
    elif damage == "url":
        client.listed[0]["download"] = {"mode": "url"}
        client.download["artifact"]["download"] = {"mode": "url"}
        client.download["url"] = "http://127.0.0.1/private"
    elif damage == "invalid_base64":
        client.download["data"] = "!not-base64!"
    elif damage == "length_mismatch":
        client.download["data"] = base64.b64encode(b"short").decode("ascii")
    elif damage == "changed_summary":
        client.download["artifact"]["title"] = "different.md"
    elif damage == "binary":
        payload = b"\xff" * len(BODY.encode("utf-8"))
        client.download["data"] = base64.b64encode(payload).decode("ascii")
    elif damage == "partial_second":
        client.listed.append({"id": "second"})
    with pytest.raises(RuntimeProtocolError, match=reason):
        await adapter.get_artifacts(current)
    assert current.artifacts == []


async def test_openclaw_reports_transport_gap_without_leaking_provider_message(rpc_artifacts):
    adapter, current, client = rpc_artifacts
    client.failure = RuntimeError("sensitive transport detail")
    with pytest.raises(RuntimeProtocolError, match="^artifact_transport_failed$") as caught:
        await adapter.get_artifacts(current)
    assert "sensitive" not in str(caught.value)
    assert current.artifacts == []


async def test_openclaw_discards_download_when_run_is_cancelled_while_awaiting(rpc_artifacts):
    adapter, current, client = rpc_artifacts
    call = client.call

    async def cancelled_download(method, params, **kwargs):
        value = await call(method, params, **kwargs)
        if method == "artifacts.download":
            current.status = "cancelled"
        return value

    client.call = cancelled_download
    with pytest.raises(RuntimeProtocolError, match="artifact_run_not_completed"):
        await adapter.get_artifacts(current)
    assert current.artifacts == []


@pytest.mark.parametrize("count", [4, 5, 33])
async def test_openclaw_limits_entire_collection_and_accepts_exact_byte_boundary(
    rpc_artifacts, count
):
    adapter, current, client = rpc_artifacts
    body = b"a" * 262_144
    client.listed = [
        {**client.summary, "id": f"artifact_{index}", "sizeBytes": len(body)}
        for index in range(count)
    ]
    call = client.call

    async def download(method, params, **kwargs):
        if method != "artifacts.download":
            return await call(method, params, **kwargs)
        return {
            "artifact": next(item for item in client.listed if item["id"] == params["artifactId"]),
            "encoding": "base64",
            "data": base64.b64encode(body).decode("ascii"),
        }

    client.call = download
    if count == 4:
        result = await adapter.get_artifacts(current)
        assert len(result) == 4
        assert all(item.origin.size_bytes == 262_144 for item in result)
    else:
        reason = "artifact_count_limit" if count == 33 else "artifact_size_limit"
        with pytest.raises(RuntimeProtocolError, match=reason):
            await adapter.get_artifacts(current)
    assert current.artifacts == []
