"""Controlled workspace collection uses real files and only transport doubles."""

import hashlib
import json
import os
from types import SimpleNamespace

import httpx
import pytest
from test_m3_runtime_artifacts import ArtifactGateway
from test_m3_runtime_turns import hermes_transport, session

from app.runtimes.base import RuntimeProtocolError, TaskContext
from app.runtimes.hermes.adapter import HermesAdapter
from app.runtimes.openclaw.adapter import OpenClawAdapter

BODY = b"print('exact bytes')\r\n"


@pytest.fixture()
def workspace_output(tmp_path):
    current = session("workspace-session")
    current.instance.home_path = str(tmp_path / "workspace")
    current.status = "completed"
    current.remote_run_id, current.remote_session_ref = "workspace-run", current.id
    current.attempt = SimpleNamespace(idempotency_key="original-attempt")
    local = hashlib.sha256(current.id.encode()).hexdigest()
    attempt = hashlib.sha256(current.attempt.idempotency_key.encode()).hexdigest()
    root = tmp_path / "workspace" / ".eidolon" / "outputs" / local / attempt
    root.mkdir(parents=True)
    target = root / "source.py"
    target.write_bytes(BODY)
    manifest = {
        "protocol": "eidolon.artifacts.v1",
        "local_session_ref": current.id,
        "request_ref": current.attempt.idempotency_key,
        "complete": True,
        "artifacts": [
            {
                "path": "source.py",
                "type": "source_code",
                "title": "Source",
                "size_bytes": len(BODY),
                "sha256": hashlib.sha256(BODY).hexdigest(),
            }
        ],
    }

    def save():
        (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    save()
    return SimpleNamespace(session=current, root=root, target=target, manifest=manifest, save=save)


async def test_hermes_collects_exact_manifest_files_and_retains_original_attempt(workspace_output):
    w = workspace_output
    first = await HermesAdapter().get_artifacts(w.session)
    second = await HermesAdapter().get_artifacts(w.session)
    assert len(first) == len(second) == 1
    item = first[0]
    assert item.content.encode() == BODY
    assert item.type == "source_code" and item.title == "Source"
    assert item.origin.local_session_ref == w.session.id
    assert item.origin.remote_run_id == "workspace-run"
    assert item.origin.remote_session_ref == w.session.id
    assert item.origin.source_ref == "source.py"
    assert item.origin.sha256 == hashlib.sha256(BODY).hexdigest()
    assert item.origin.size_bytes == len(BODY)
    assert w.session.artifacts == []


@pytest.mark.parametrize(
    "path", ["../secret.txt", "/etc/passwd", "a/../../x", "a\\b", ".", "", "a//b"]
)
async def test_manifest_cannot_address_files_outside_its_attempt(workspace_output, path):
    w = workspace_output
    w.manifest["artifacts"][0]["path"] = path
    w.save()
    with pytest.raises(RuntimeProtocolError, match="artifact_path_invalid"):
        await HermesAdapter().get_artifacts(w.session)


@pytest.mark.parametrize("kind", ["file_link", "directory_link", "root_link", "hard_link", "fifo"])
async def test_workspace_rejects_symlinks_hardlinks_and_nonregular_files(workspace_output, kind):
    w = workspace_output
    outside = w.root.parent / "outside.py"
    outside.write_bytes(BODY)
    if kind == "file_link":
        w.target.unlink()
        w.target.symlink_to(outside)
    elif kind == "directory_link":
        (w.root / "alias").symlink_to(w.root.parent, target_is_directory=True)
        w.manifest["artifacts"][0]["path"] = "alias/outside.py"
        w.save()
    elif kind == "root_link":
        original = w.root.with_name("moved")
        w.root.rename(original)
        w.root.symlink_to(original, target_is_directory=True)
    elif kind == "hard_link":
        w.target.unlink()
        os.link(outside, w.target)
    elif kind == "fifo":
        w.target.unlink()
        os.mkfifo(w.target)
    with pytest.raises(RuntimeProtocolError, match="artifact_path_unsafe"):
        await HermesAdapter().get_artifacts(w.session)


@pytest.mark.parametrize(
    "damage,reason",
    [
        ("missing", "artifact_file_missing"),
        ("half_file", "artifact_size_mismatch"),
        ("changed_hash", "artifact_hash_mismatch"),
        ("wrong_session", "artifact_origin_mismatch"),
        ("wrong_request", "artifact_origin_mismatch"),
        ("not_complete", "artifact_manifest_incomplete"),
        ("partial_json", "artifact_manifest_invalid"),
        ("duplicate_key", "artifact_manifest_invalid"),
        ("unknown_field", "artifact_manifest_invalid"),
        ("wrong_type", "artifact_manifest_invalid"),
        ("duplicate_path", "artifact_duplicate_source"),
        ("oversize", "artifact_size_limit"),
        ("too_many", "artifact_count_limit"),
        ("invalid_second", "artifact_file_missing"),
    ],
)
async def test_hermes_never_returns_a_partial_or_unbound_collection(
    workspace_output, damage, reason
):
    w = workspace_output
    if damage == "missing":
        w.target.unlink()
    elif damage == "half_file":
        w.target.write_bytes(BODY[:4])
    elif damage == "changed_hash":
        w.manifest["artifacts"][0]["sha256"] = "0" * 64
    elif damage == "wrong_session":
        w.manifest["local_session_ref"] = "different-session"
    elif damage == "wrong_request":
        w.manifest["request_ref"] = "older-attempt"
    elif damage == "not_complete":
        w.manifest["complete"] = False
    elif damage == "unknown_field":
        w.manifest["allow_outside"] = True
    elif damage == "wrong_type":
        w.manifest["artifacts"][0]["type"] = "invented"
    elif damage == "duplicate_path":
        w.manifest["artifacts"].append(dict(w.manifest["artifacts"][0]))
    elif damage == "oversize":
        w.manifest["artifacts"][0]["size_bytes"] = 262_145
    elif damage == "too_many":
        w.manifest["artifacts"] *= 33
    elif damage == "invalid_second":
        w.manifest["artifacts"].append({**w.manifest["artifacts"][0], "path": "missing.py"})
    w.save()
    if damage == "partial_json":
        (w.root / "manifest.json").write_text('{"protocol":', encoding="utf-8")
    elif damage == "duplicate_key":
        body = json.dumps(w.manifest)
        (w.root / "manifest.json").write_text(body[:-1] + ', "complete": true}', encoding="utf-8")
    with pytest.raises(RuntimeProtocolError, match=reason):
        await HermesAdapter().get_artifacts(w.session)
    assert w.session.artifacts == []


async def test_workspace_detects_file_changes_during_read(workspace_output, monkeypatch):
    w = workspace_output
    original_read = os.read
    changed = False

    def mutate(fd, size):
        nonlocal changed
        data = original_read(fd, size)
        if data == BODY and not changed:
            w.target.write_bytes(b"x" * len(BODY))
            changed = True
        return data

    monkeypatch.setattr(os, "read", mutate)
    with pytest.raises(RuntimeProtocolError, match="artifact_file_changed"):
        await HermesAdapter().get_artifacts(w.session)
    assert changed


async def test_hermes_worker_marks_observed_terminal_before_artifact_collection(monkeypatch):
    def respond(request):
        if request.method == "POST":
            return httpx.Response(202, json={"run_id": "terminal-run"})
        return httpx.Response(200, text='data: {"type":"run.completed"}\n\n')

    hermes_transport(monkeypatch, respond)
    current = session("terminal-session")
    await HermesAdapter()._pump(current, "body", 1)
    assert current.status == "completed"
    assert current.remote_run_id == "terminal-run"


@pytest.mark.parametrize(
    "adapter_type,mount",
    [(HermesAdapter, "/opt/data/workspace"), (OpenClawAdapter, "/home/node/.openclaw/workspace")],
)
async def test_worker_output_protocol_carries_declared_types_without_host_paths(
    workspace_output, monkeypatch, adapter_type, mount
):
    w = workspace_output
    task = TaskContext(task_id=1, project_id=1, title="Source", kind="development")
    task.required_artifact_types = ["source_code", "readme"]
    captured = []

    async def capture(session, prompt, timeout):
        captured.append(prompt)

    adapter = adapter_type()
    monkeypatch.setattr(adapter, "_pump", capture)
    await adapter.send_task(w.session, "Build files.", {"task_context": task})
    await w.session.background
    protocol = json.loads(captured[0].splitlines()[-1])
    assert protocol["required_types"] == ["source_code", "readme"]
    assert protocol["manifest"]["request_ref"] == "original-attempt"
    assert protocol["directory"].startswith(mount + "/.eidolon/outputs/")
    assert w.session.instance.home_path not in captured[0]


@pytest.mark.parametrize(
    "native", ["empty", "matching", "different_bytes", "partial", "transport_failed"]
)
async def test_openclaw_worker_requires_complete_manifest_and_checks_native_inventory(
    workspace_output, monkeypatch, native
):
    import base64
    from copy import deepcopy

    w = workspace_output
    client, adapter = ArtifactGateway(), OpenClawAdapter()
    client.summary.update(
        runId=w.session.remote_run_id, sessionKey=w.session.remote_session_ref, sizeBytes=len(BODY)
    )
    client.listed = [] if native in {"empty", "transport_failed"} else [deepcopy(client.summary)]
    client.download = {
        "artifact": deepcopy(client.summary),
        "encoding": "base64",
        "data": base64.b64encode(
            BODY if native != "different_bytes" else b"x" * len(BODY)
        ).decode(),
    }
    if native == "partial":
        (w.root / "second.txt").write_bytes(b"another")
        w.manifest["artifacts"].append(
            {
                "path": "second.txt",
                "type": "readme",
                "title": "Second",
                "size_bytes": 7,
                "sha256": hashlib.sha256(b"another").hexdigest(),
            }
        )
        w.save()
    elif native == "transport_failed":
        client.failure = RuntimeError("remote failure")
    monkeypatch.setattr(adapter, "_connect", lambda instance: client)
    if native in {"different_bytes", "partial", "transport_failed"}:
        reason = (
            "artifact_transport_failed"
            if native == "transport_failed"
            else "artifact_inventory_mismatch"
        )
        with pytest.raises(RuntimeProtocolError, match=reason):
            await adapter.get_artifacts(w.session)
    else:
        outputs = await adapter.get_artifacts(w.session)
        assert len(outputs) == 1
        assert outputs[0].type == "source_code" and outputs[0].title == "Source"
        assert outputs[0].content.encode() == BODY
        assert outputs[0].origin.source_ref == (
            "source.py" if native == "empty" else "artifact_original"
        )
        assert client.calls[0][0] == "artifacts.list"


async def test_openclaw_empty_native_list_does_not_hide_missing_manifest(
    workspace_output, monkeypatch
):
    w = workspace_output
    (w.root / "manifest.json").unlink()
    client, adapter = ArtifactGateway(), OpenClawAdapter()
    client.listed = []
    monkeypatch.setattr(adapter, "_connect", lambda instance: client)
    with pytest.raises(RuntimeProtocolError, match="artifact_file_missing"):
        await adapter.get_artifacts(w.session)


@pytest.mark.parametrize("status", ["running", "failed", "cancelled"])
async def test_workspace_never_collects_an_unfinished_run(workspace_output, status):
    w = workspace_output
    w.session.status = status
    with pytest.raises(RuntimeProtocolError, match="artifact_run_not_completed"):
        await HermesAdapter().get_artifacts(w.session)


async def test_workspace_rejects_manifest_replacement_during_collection(
    workspace_output, monkeypatch
):
    w = workspace_output
    original_read = os.read
    changed = False

    def replace_manifest(fd, size):
        nonlocal changed
        data = original_read(fd, size)
        if data == BODY and not changed:
            w.manifest["artifacts"][0]["title"] = "Late replacement"
            w.save()
            changed = True
        return data

    monkeypatch.setattr(os, "read", replace_manifest)
    with pytest.raises(RuntimeProtocolError, match="artifact_file_changed"):
        await HermesAdapter().get_artifacts(w.session)
    assert changed
