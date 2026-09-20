"""Original workspace identity is saved before dispatch, never inferred at collection."""

import hashlib
import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from test_m3_runtime_conversation import world as _conversation_world

from app.core.database import SessionLocal
from app.models.base import utcnow
from app.models.runtime_execution import RuntimeAttempt
from app.repositories import project as project_repo
from app.runtimes.base import RuntimeProtocolError, RuntimeSession, accept_remote_run
from app.runtimes.hermes.adapter import HermesAdapter
from app.work import telemetry

conversation_world = _conversation_world


@pytest.fixture()
def workspace_world(db, conversation_world, tmp_path):
    w = conversation_world
    w.work.status, w.work.ended_at = "failed", utcnow()
    w.task.produces_json = ["source_code"]
    root = tmp_path / "original-workspace"
    root.mkdir()
    w.row.workspace_path = str(root)
    w.runtime.instance.home_path = str(root)
    w.work = project_repo.create_work_session(
        db,
        task_id=w.task.id,
        employee_id=w.actor.id,
        runtime_type="hermes",
        runtime_instance_id=w.row.id,
        provider_id=w.provider.id,
        model="original-model",
        runtime_session_ref=w.runtime.id,
        started_at=utcnow(),
    )
    db.commit()
    return w


def prepare(w):
    return telemetry.prepare_attempt(
        w.work.id, w.runtime, "Build source", request_key="worker", kind="worker"
    )


def test_original_workspace_and_required_types_are_durable_before_send(db, workspace_world):
    w = workspace_world
    recorder = prepare(w)
    with SessionLocal() as other:
        row = other.get(RuntimeAttempt, recorder.attempt_id)
        assert row.status == "prepared" and row.remote_run_id is None
        origin = Path(w.row.workspace_path).stat()
        assert row.artifact_context_json == {
            "version": 1,
            "workspace": {
                "path": w.row.workspace_path,
                "device": origin.st_dev,
                "inode": origin.st_ino,
            },
            "required_types": ["source_code"],
        }
    assert w.runtime.artifact_workspace.path == w.row.workspace_path
    with pytest.raises(FrozenInstanceError):
        w.runtime.artifact_workspace.path = "/other"


@pytest.mark.parametrize("damage", ["handle_mismatch", "missing", "symlink", "relative"])
def test_invalid_workspace_cannot_create_an_attempt(db, workspace_world, tmp_path, damage):
    w = workspace_world
    root = Path(w.row.workspace_path)
    if damage == "handle_mismatch":
        other = tmp_path / "other"
        other.mkdir()
        w.runtime.instance.home_path = str(other)
    elif damage == "missing":
        root.rmdir()
    elif damage == "symlink":
        actual = root.with_name("actual")
        root.rename(actual)
        root.symlink_to(actual, target_is_directory=True)
    else:
        w.row.workspace_path = "relative/workspace"
        w.runtime.instance.home_path = w.row.workspace_path
        db.commit()
    with pytest.raises(RuntimeProtocolError, match="artifact_workspace"):
        prepare(w)
    with SessionLocal() as other:
        assert not list(other.scalars(select(RuntimeAttempt)))


@pytest.mark.parametrize("damage", ["path_rebound", "directory_replaced", "required_types_changed"])
def test_repeated_request_cannot_replace_original_artifact_context(
    db, workspace_world, tmp_path, damage
):
    w = workspace_world
    first = prepare(w)
    if damage == "path_rebound":
        root = tmp_path / "replacement"
        root.mkdir()
        w.row.workspace_path = str(root)
        w.runtime.instance.home_path = str(root)
        db.commit()
    elif damage == "directory_replaced":
        root = Path(w.row.workspace_path)
        root.rename(root.with_name("previous"))
        root.mkdir()
    else:
        # Current task edits cannot alter the creation-time required types.
        w.task.produces_json = []
        db.commit()
        assert prepare(w).attempt_id == first.attempt_id
        db.expire_all()
        assert db.get(RuntimeAttempt, first.attempt_id).artifact_context_json["required_types"] == [
            "source_code"
        ]
        return
    with pytest.raises(
        RuntimeProtocolError, match="artifact_context_changed|artifact_workspace_changed"
    ):
        prepare(w)
    with SessionLocal() as other:
        assert len(list(other.scalars(select(RuntimeAttempt)))) == 1


def test_restart_reinstalls_saved_workspace_guard_without_new_attempt(db, workspace_world):
    w = workspace_world
    original = prepare(w)
    w.runtime = RuntimeSession(id=w.runtime.id, instance=w.runtime.instance, task_id=w.task.id)
    resumed = prepare(w)
    assert resumed.attempt_id == original.attempt_id
    assert w.runtime.artifact_workspace.path == w.row.workspace_path


def test_workspace_snapshot_is_immutable_even_through_direct_sql(db, workspace_world):
    recorder = prepare(workspace_world)
    with pytest.raises(IntegrityError, match="runtime execution fact"):
        db.execute(
            text("UPDATE runtime_attempts SET artifact_context_json = '{}' WHERE id = :id"),
            {"id": recorder.attempt_id},
        )
    db.rollback()


async def test_collection_rejects_a_replaced_root_even_with_a_matching_manifest(
    db, workspace_world
):
    from app.runtimes.workspace_artifacts import output_directory

    w = workspace_world
    recorder = prepare(w)
    root = Path(w.row.workspace_path)
    root.rename(root.with_name("original-root-retained"))
    root.mkdir()
    target = root / output_directory(w.runtime)
    target.mkdir(parents=True)
    body = b"print('replacement')\n"
    (target / "result.py").write_bytes(body)
    (target / "manifest.json").write_text(
        json.dumps(
            {
                "protocol": "eidolon.artifacts.v1",
                "local_session_ref": w.runtime.id,
                "request_ref": recorder.idempotency_key,
                "complete": True,
                "artifacts": [
                    {
                        "path": "result.py",
                        "title": "Result",
                        "type": "source_code",
                        "size_bytes": len(body),
                        "sha256": hashlib.sha256(body).hexdigest(),
                    }
                ],
            }
        )
    )
    accept_remote_run(w.runtime, "original-run", w.runtime.id)
    recorder.terminal("completed", "hermes.status")
    w.runtime.status = "completed"
    with pytest.raises(RuntimeProtocolError, match="artifact_workspace_changed"):
        await HermesAdapter().get_artifacts(w.runtime)


def test_legacy_worker_context_is_unknown_and_cannot_be_backfilled(db, workspace_world):
    w = workspace_world
    old = RuntimeAttempt(
        work_session_id=w.work.id,
        request_key="worker",
        attempt_number=1,
        idempotency_key=w.runtime.id,
        request_digest="a" * 64,
        local_session_ref=w.runtime.id,
        runtime_instance_id=w.row.id,
        instance_fingerprint=w.runtime.instance.details["instance_fingerprint"],
        kind="worker",
        remote_session_ref=w.runtime.id,
        status="prepared",
        last_progress_at=utcnow(),
    )
    db.add(old)
    db.commit()
    with pytest.raises(RuntimeProtocolError, match="artifact_context_unknown"):
        prepare(w)
    db.refresh(old)
    assert old.artifact_context_json is None


@pytest.mark.parametrize("runtime_type", ["hermes", "openclaw"])
async def test_worker_receives_original_types_even_if_transient_context_changes(
    db, workspace_world, monkeypatch, runtime_type
):
    from app.runtimes.base import TaskContext
    from app.runtimes.openclaw.adapter import OpenClawAdapter

    w = workspace_world
    prepare(w)
    adapter = HermesAdapter() if runtime_type == "hermes" else OpenClawAdapter()
    prompts = []

    async def record(session, prompt, timeout):
        prompts.append(prompt)

    monkeypatch.setattr(adapter, "_pump", record)
    await adapter.send_task(
        w.runtime,
        "Build",
        {
            "task_context": TaskContext(
                task_id=w.task.id,
                project_id=w.project.id,
                title="Build",
                kind="coding",
                required_artifact_types=[],
            )
        },
    )
    await w.runtime.background
    assert json.loads(prompts[0].splitlines()[-1])["required_types"] == ["source_code"]


def test_controlled_attempt_does_not_claim_a_workspace_snapshot(db, workspace_world):
    w = workspace_world
    recorder = telemetry.prepare_attempt(
        w.work.id, w.runtime, "Read", request_key="read", kind="controlled"
    )
    db.expire_all()
    assert db.get(RuntimeAttempt, recorder.attempt_id).artifact_context_json is None
    assert w.runtime.artifact_workspace is None
