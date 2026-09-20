"""Whole collection acceptance joins real execution, Drive and transaction facts."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from test_m3_artifact_workspace_identity import conversation_world as _conversation_world
from test_m3_artifact_workspace_identity import prepare
from test_m3_artifact_workspace_identity import workspace_world as _workspace_world

from app.core.database import SessionLocal
from app.models.base import utcnow
from app.models.drive import DriveNode, DriveRevision
from app.models.project import Task
from app.models.runtime_execution import RuntimeAttempt
from app.runtimes.artifacts import text_artifact
from app.runtimes.base import RuntimeProtocolError, accept_remote_run
from app.services import drive
from app.work import runtime_artifacts

conversation_world = _conversation_world
workspace_world = _workspace_world


@pytest.fixture()
def completed_world(db, workspace_world):
    w = workspace_world
    w.recorder = prepare(w)
    accept_remote_run(w.runtime, "original-artifact-run", w.runtime.id)
    w.recorder.terminal("completed", "hermes.status")
    w.runtime.status = "completed"
    w.items = [artifact(w)]
    return w


def artifact(w, source="result.py", body=b"print('exact original')\r\n", kind="source_code"):
    return text_artifact(w.runtime, source_ref=source, title=source, body=body, artifact_type=kind)


def accept(w, *, items=None, commit=True):
    with SessionLocal() as fresh:
        nodes = runtime_artifacts.accept_collection(
            fresh,
            work_session_id=w.work.id,
            attempt_id=w.recorder.attempt_id,
            session=w.runtime,
            produced=w.items if items is None else items,
        )
        ids = [node.id for node in nodes]
        if commit:
            fresh.commit()
        return ids


def receipt(db, w):
    from app.models.runtime_artifact import RuntimeArtifactCollection

    return db.get(RuntimeArtifactCollection, w.recorder.attempt_id)


def assert_no_outputs(db, w):
    db.expire_all()
    assert not list(db.scalars(select(DriveNode).where(DriveNode.work_session_id == w.work.id)))
    assert receipt(db, w) is None


def test_acceptance_saves_exact_revisions_and_original_ownership(db, completed_world):
    w = completed_world
    w.items.append(artifact(w, "report.md", b"# Verified\n", "test_report"))
    ids = accept(w)
    db.expire_all()
    saved = receipt(db, w)
    assert len(ids) == len(saved.entries_json) == 2
    assert len(saved.digest) == 64
    assert {entry["source_ref"] for entry in saved.entries_json} == {"result.py", "report.md"}
    for entry in saved.entries_json:
        revision = db.get(DriveRevision, entry["revision_id"])
        node = db.get(DriveNode, revision.node_id)
        item = next(item for item in w.items if item.origin.source_ref == entry["source_ref"])
        assert node.id in ids and node.task_id == w.task.id and node.work_session_id == w.work.id
        assert node.company_id == w.work.company_id and node.owner_person_id == w.work.person_id
        assert revision.author_person_id == w.work.person_id
        assert drive.read_revision_content(node, revision) == item.content
        assert revision.sha256 == hashlib.sha256(item.content.encode()).hexdigest()
    # Acceptance itself does not judge or finalize the business task.
    assert db.get(Task, w.task.id).status == "in_progress"


@pytest.mark.parametrize(
    "damage",
    [
        "empty",
        "missing_type",
        "no_origin",
        "wrong_run",
        "wrong_session",
        "wrong_remote_session",
        "hash",
        "size",
        "duplicate_source",
        "title",
        "type",
        "oversize",
        "count",
        "batch_size",
    ],
)
def test_entire_batch_is_preflighted_before_any_visible_write(db, completed_world, damage):
    w = completed_world
    bad = artifact(w, "second.py")
    if damage == "empty":
        w.items = []
    elif damage == "missing_type":
        w.items = [artifact(w, kind="other")]
    else:
        if damage == "no_origin":
            bad.origin = None
        elif damage == "wrong_run":
            bad.origin = replace(bad.origin, remote_run_id="other-run")
        elif damage == "wrong_session":
            bad.origin = replace(bad.origin, local_session_ref="other-session")
        elif damage == "wrong_remote_session":
            bad.origin = replace(bad.origin, remote_session_ref="other-session")
        elif damage == "hash":
            bad.content += "changed"
        elif damage == "size":
            bad.origin = replace(bad.origin, size_bytes=1)
        elif damage == "duplicate_source":
            bad.origin = replace(bad.origin, source_ref=w.items[0].origin.source_ref)
        elif damage == "title":
            bad.title = ""
        elif damage == "type":
            bad.type = "invented"
        elif damage == "oversize":
            bad.content = "x" * (256 * 1024 + 1)
            bad.origin = replace(
                bad.origin,
                size_bytes=len(bad.content),
                sha256=hashlib.sha256(bad.content.encode()).hexdigest(),
            )
        elif damage == "count":
            w.items = [artifact(w, str(i)) for i in range(32)]
        elif damage == "batch_size":
            w.items = [artifact(w, str(i), b"x" * (256 * 1024)) for i in range(4)]
        w.items.append(bad)
    with pytest.raises(RuntimeProtocolError):
        accept(w)
    assert_no_outputs(db, w)


@pytest.mark.parametrize(
    "damage",
    [
        "cancelled_handle",
        "cancelled_host",
        "durable_stop",
        "closed_work",
        "cancelled_task",
        "runtime_replaced",
        "person_rebound",
        "root_replaced",
    ],
)
def test_late_or_rebound_output_cannot_be_accepted(db, completed_world, damage):
    w = completed_world
    if damage == "cancelled_handle":
        w.runtime.status = "cancelled"
    elif damage == "cancelled_host":
        w.recorder.finish_host("cancelled", "cancelled")
    elif damage == "durable_stop":
        w.recorder.request_stop("cancelled")
    elif damage == "closed_work":
        w.work.status, w.work.ended_at = "cancelled", utcnow()
    elif damage == "cancelled_task":
        w.task.status = "cancelled"
    elif damage == "runtime_replaced":
        w.row.image_digest = "sha256:replacement"
    elif damage == "person_rebound":
        w.row.person_id = 9999
    else:
        root = Path(w.row.workspace_path)
        root.rename(root.with_name("previous-root"))
        root.mkdir()
    db.commit()
    with pytest.raises(RuntimeProtocolError):
        accept(w)
    assert_no_outputs(db, w)


def test_later_stop_preserves_already_accepted_collection_history(db, completed_world):
    w = completed_world
    original = accept(w)
    w.recorder.request_stop("cancelled")
    assert accept(w) == original
    db.expire_all()
    assert receipt(db, w) is not None


def test_collection_cannot_be_accepted_from_another_company(db, completed_world):
    from app.core.request_context import (
        RequestIdentity,
        reset_request_identity,
        set_request_identity,
    )

    w = completed_world
    token = set_request_identity(RequestIdentity(1, 9999, "owner", 1))
    try:
        with pytest.raises(RuntimeProtocolError):
            accept(w)
    finally:
        reset_request_identity(token)
    assert_no_outputs(db, w)


def test_request_superseded_by_new_worker_attempt_cannot_publish(db, completed_world):
    w = completed_world
    original = db.get(RuntimeAttempt, w.recorder.attempt_id)
    # Model a persisted newer intent. A second first-worker dispatch is itself
    # disallowed by U13's idempotency key, so do not fake that through prepare().
    db.add(
        RuntimeAttempt(
            work_session_id=w.work.id,
            request_key="worker",
            attempt_number=2,
            idempotency_key="newer-worker-intent",
            request_digest=original.request_digest,
            local_session_ref=original.local_session_ref,
            runtime_instance_id=w.row.id,
            instance_fingerprint=original.instance_fingerprint,
            kind="worker",
            remote_session_ref=original.remote_session_ref,
            status="prepared",
            last_progress_at=utcnow(),
            artifact_context_json=original.artifact_context_json,
        )
    )
    db.commit()
    with pytest.raises(RuntimeProtocolError):
        accept(w)
    assert_no_outputs(db, w)


@pytest.mark.parametrize(
    "damage", ["cancelled", "remote_rebound", "root_replaced", "canonical_corrupted"]
)
def test_change_during_drive_writes_rolls_back_whole_acceptance(
    db, completed_world, monkeypatch, damage
):
    from app.services import artifacts

    w = completed_world
    original = artifacts.record_project_artifact

    def changed(*args, **kwargs):
        node = original(*args, **kwargs)
        if damage == "cancelled":
            w.runtime.status = "cancelled"
        elif damage == "remote_rebound":
            w.runtime.remote_run_id = "replacement-run"
        elif damage == "root_replaced":
            root = Path(w.row.workspace_path)
            root.rename(root.with_name("retired-root"))
            root.mkdir()
        else:
            from app.core.config import settings

            revision = drive_repo_revision(args[0], node.id)
            (Path(settings.data_root) / revision.content_path).write_bytes(b"corrupted")
        return node

    monkeypatch.setattr(artifacts, "record_project_artifact", changed)
    with pytest.raises(RuntimeProtocolError):
        accept(w)
    assert_no_outputs(db, w)


def test_caught_file_error_then_outer_commit_still_has_no_partial_collection(
    db, completed_world, monkeypatch
):
    from app.services import artifacts

    w = completed_world
    w.items.append(artifact(w, "second.py"))
    original, calls = artifacts.record_project_artifact, []

    def fail_second(*args, **kwargs):
        calls.append(kwargs["title"])
        if len(calls) == 2:
            raise OSError("injected second-file failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(artifacts, "record_project_artifact", fail_second)
    with SessionLocal() as fresh:
        with pytest.raises(OSError):
            runtime_artifacts.accept_collection(
                fresh,
                work_session_id=w.work.id,
                attempt_id=w.recorder.attempt_id,
                session=w.runtime,
                produced=w.items,
            )
        fresh.commit()
    assert_no_outputs(db, w)


def test_reassignment_keeps_original_author_at_collection(db, completed_world):
    from app.models.organization import Employee

    w = completed_world
    other = db.scalar(select(Employee).where(Employee.id != w.work.employee_id))
    w.task.assignee_id = other.id
    db.commit()
    ids = accept(w)
    db.expire_all()
    node = db.get(DriveNode, ids[0])
    assert node.owner_person_id == w.work.person_id
    assert node.owner_employee_id == w.work.employee_id != other.id


def test_permuted_batch_reuses_same_collection(db, completed_world):
    w = completed_world
    w.items.append(artifact(w, "second.py"))
    first = accept(w)
    assert accept(w, items=list(reversed(w.items))) == first


@pytest.mark.parametrize("target", ["task", "work"])
def test_unflushed_cancellation_in_callers_transaction_is_not_discarded(
    db, completed_world, target
):
    from app.models.project import WorkSession

    w = completed_world
    with SessionLocal() as fresh:
        row = fresh.get(Task, w.task.id) if target == "task" else fresh.get(WorkSession, w.work.id)
        row.status = "cancelled"
        with pytest.raises(RuntimeProtocolError):
            runtime_artifacts.accept_collection(
                fresh,
                work_session_id=w.work.id,
                attempt_id=w.recorder.attempt_id,
                session=w.runtime,
                produced=w.items,
            )
        assert row.status == "cancelled"
        fresh.commit()
    assert_no_outputs(db, w)


def test_replay_after_restart_and_later_edit_keeps_same_nodes_and_original_versions(
    db, completed_world
):
    w = completed_world
    first = accept(w)
    node = db.get(DriveNode, first[0])
    drive.update_document(db, node, content="later mutable body")
    w.work.status, w.work.ended_at = "completed", utcnow()
    w.task.status = "in_review"
    db.commit()
    assert accept(w) == first
    db.expire_all()
    assert len(list(db.scalars(select(DriveRevision).where(DriveRevision.node_id == node.id)))) == 2
    assert receipt(db, w).entries_json[0]["revision_id"] == drive_repo_revision(db, node.id).id


def drive_repo_revision(db, node_id):
    from app.repositories.drive import get_revision

    return get_revision(db, node_id, 1)


def test_replay_with_changed_collection_is_a_conflict(db, completed_world):
    w = completed_world
    first = accept(w)
    with pytest.raises(RuntimeProtocolError, match="conflict"):
        accept(w, items=[artifact(w, body=b"different complete output")])
    assert accept(w) == first


def test_roll_back_second_file_failure_and_replay_without_duplicate_versions(
    db, completed_world, monkeypatch
):
    from app.services import artifacts

    w = completed_world
    w.items.append(artifact(w, "second.py"))
    original, calls = artifacts.record_project_artifact, []

    def fail_second(*args, **kwargs):
        calls.append(kwargs["title"])
        if len(calls) == 2:
            raise OSError("injected second-file failure")
        return original(*args, **kwargs)

    with monkeypatch.context() as injected:
        injected.setattr(artifacts, "record_project_artifact", fail_second)
        with pytest.raises(OSError):
            accept(w)
    assert_no_outputs(db, w)
    ids = accept(w)
    assert len(ids) == 2 and accept(w) == ids


def test_concurrent_acceptance_has_one_receipt_and_one_set_of_versions(db, completed_world):
    w = completed_world
    barrier = Barrier(2)

    def concurrent():
        barrier.wait(timeout=5)
        return accept(w)

    with ThreadPoolExecutor(max_workers=2) as pool:
        left, right = pool.submit(concurrent), pool.submit(concurrent)
        first, second = left.result(timeout=10), right.result(timeout=10)
    assert first == second
    db.expire_all()
    assert (
        len(list(db.scalars(select(DriveNode).where(DriveNode.work_session_id == w.work.id)))) == 1
    )


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE runtime_artifact_collections SET digest=:digest WHERE attempt_id=:id",
        "DELETE FROM runtime_artifact_collections WHERE attempt_id=:id",
        "INSERT OR REPLACE INTO runtime_artifact_collections "
        "SELECT * FROM runtime_artifact_collections WHERE attempt_id=:id",
    ],
)
def test_collection_receipt_is_immutable_even_via_sql(db, completed_world, statement):
    w = completed_world
    accept(w)
    with pytest.raises(IntegrityError, match="artifact collection"):
        db.execute(text(statement), {"id": w.recorder.attempt_id, "digest": "a" * 64})
    db.rollback()


def test_real_finalization_uses_one_transaction_for_receipt_artifacts_and_review_wait(
    db, completed_world
):
    from app.workflow.orchestrator import Orchestrator

    w = completed_world
    Orchestrator()._finalize(
        w.task.id, True, w.items, None, 1.0, work_session_id=w.work.id, runtime_session=w.runtime
    )
    db.expire_all()
    assert w.work.status == "completed" and w.task.status == "in_review"
    assert receipt(db, w) is not None
    assert w.task.status != "done"


def test_real_finalization_with_missing_declared_output_cannot_report_success(db, completed_world):
    from app.workflow.orchestrator import Orchestrator

    w = completed_world
    with pytest.raises(RuntimeProtocolError):
        Orchestrator()._finalize(
            w.task.id, True, [], None, 1.0, work_session_id=w.work.id, runtime_session=w.runtime
        )
    assert_no_outputs(db, w)
    assert w.work.status == "running" and w.task.status == "in_progress"


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE drive_nodes SET task_id=NULL WHERE id=:id",
        "UPDATE drive_nodes SET owner_person_id=9999 WHERE id=:id",
        "UPDATE drive_nodes SET company_id=9999 WHERE id=:id",
        "UPDATE drive_nodes SET work_session_id=NULL WHERE id=:id",
        "UPDATE drive_nodes SET doc_type='other' WHERE id=:id",
        "DELETE FROM drive_nodes WHERE id=:id",
        "INSERT OR REPLACE INTO drive_nodes SELECT * FROM drive_nodes WHERE id=:id",
    ],
)
def test_accepted_node_cannot_be_relabelled_or_replaced(db, completed_world, statement):
    w = completed_world
    node_id = accept(w)[0]
    with pytest.raises(IntegrityError, match="artifact collection"):
        db.execute(text(statement), {"id": node_id})
    db.rollback()


@pytest.mark.parametrize(
    "damage", ["missing_type", "foreign_revision", "extra_key", "duplicate", "stopped"]
)
def test_sql_cannot_insert_a_receipt_with_invalid_canonical_references(db, completed_world, damage):
    from app.models.runtime_artifact import RuntimeArtifactCollection
    from app.services import artifacts

    w = completed_world
    node = artifacts.record_project_artifact(
        db,
        w.project,
        artifact_type="source_code",
        title="Prepared canonical bytes",
        content=w.items[0].content,
        author_id=w.work.employee_id,
        work_session_id=None if damage == "foreign_revision" else w.work.id,
        task_id=w.task.id,
        preserve_revision=True,
    )
    revision = drive_repo_revision(db, node.id)
    db.commit()
    entries = [{"source_ref": "result.py", "revision_id": revision.id}]
    if damage == "missing_type":
        entries = []
    elif damage == "extra_key":
        entries[0]["invented"] = "ignored?"
    elif damage == "duplicate":
        entries *= 2
    elif damage == "stopped":
        w.recorder.request_stop("cancelled")
    db.add(
        RuntimeArtifactCollection(
            attempt_id=w.recorder.attempt_id, digest="a" * 64, entries_json=entries
        )
    )
    with pytest.raises(IntegrityError, match="artifact collection"):
        db.flush()
    db.rollback()
