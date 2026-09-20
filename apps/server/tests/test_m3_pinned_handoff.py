"""Local DB/file chain: acceptance -> review -> exact input -> append-only use.

Transport completion and reviewer decisions are explicit test inputs, not paid
runtime or model evidence. The production services and filesystem are real.
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from test_m3_artifact_acceptance import artifact, prepare, receipt
from test_m3_artifact_acceptance import completed_world as _completed_world
from test_m3_artifact_acceptance import conversation_world as _conversation_world
from test_m3_artifact_acceptance import workspace_world as _workspace_world

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.base import utcnow
from app.models.drive import DriveNode, DriveRevision
from app.models.enums import ReviewVerdict
from app.models.handoff import ArtifactLink
from app.models.organization import Employee
from app.models.project import Task
from app.repositories import project as project_repo
from app.repositories import review as review_repo
from app.runtimes.base import RuntimeSession, accept_remote_run
from app.services import artifacts, drive
from app.services import tasks as task_service
from app.work import handoff, review_facts, reviews, tool_reads
from app.workflow.orchestrator import Orchestrator

completed_world = _completed_world
conversation_world = _conversation_world
workspace_world = _workspace_world


def finish(db, w):
    Orchestrator()._finalize(
        w.task.id,
        True,
        w.items,
        None,
        1.0,
        work_session_id=w.work.id,
        runtime_session=w.runtime,
    )
    db.expire_all()
    w.revision = db.get(DriveRevision, receipt(db, w).entries_json[0]["revision_id"])
    w.node = db.get(DriveNode, w.revision.node_id)


@pytest.fixture()
def delivered_world(db, completed_world):
    w = completed_world
    finish(db, w)
    w.reviewer = db.scalar(select(Employee).where(Employee.slug == "dana"))
    w.consumer = db.scalar(select(Employee).where(Employee.slug == "bob"))
    w.downstream = Task(
        project_id=w.project.id,
        title="Consume accepted source",
        status="in_progress",
        assignee_id=w.consumer.id,
    )
    db.add(w.downstream)
    db.commit()
    return w


def open_review(db, w):
    return reviews.open_review_request(
        db,
        task=w.task,
        requester_employee_id=w.actor.id,
        reviewer_employee_id=w.reviewer.id,
    )


def decide(db, w, request=None, verdict=ReviewVerdict.passed):
    request = request or open_review(db, w)
    return reviews.submit_verdict(
        db,
        request=request,
        reviewer_employee_id=w.reviewer.id,
        verdict=verdict,
        notes="Test-specified review decision",
    )


def declaration(db, w):
    return handoff.consume_artifact(db, task=w.downstream, artifact_id=w.node.id)


def consumer_session(db, w):
    ws = project_repo.create_work_session(
        db,
        task_id=w.downstream.id,
        employee_id=w.consumer.id,
        runtime_type="mock",
        started_at=utcnow(),
    )
    db.commit()
    return ws


def record(db, w, ws, refs=None):
    return handoff.record_consumed(
        db,
        task_id=w.downstream.id,
        artifact_ids=[w.node.id],
        artifact_refs=refs or handoff.resolve_input_artifacts(db, w.downstream),
        work_session_id=ws.id,
        actor_employee_id=w.consumer.id,
    )


def test_review_input_and_delivery_keep_the_original_version_after_edit(db, delivered_world):
    w = delivered_world
    request = open_review(db, w)
    drive.update_document(db, w.node, content="unreviewed replacement\n")
    facts = {r.kind: r.payload_json for r in review_repo.list_facts(db, request.id)}
    item = facts["artifacts"]["items"][0]
    assert item["revision_id"] == w.revision.id and item["sha256"] == w.revision.sha256
    decide(db, w, request)
    declaration(db, w)
    refs = handoff.resolve_input_artifacts(db, w.downstream)
    assert len(refs) == 1
    assert refs[0].revision_id == w.revision.id and refs[0].version == 1
    assert refs[0].excerpt == w.items[0].content
    assert refs[0].sha256 == w.revision.sha256
    delivery = artifacts.resolve_artifact_refs(
        db, company_id=w.project.company_id, refs=[w.node.id]
    )
    assert delivery[0]["revision_id"] == w.revision.id and delivery[0]["version"] == 1


def test_reviewer_read_tool_exposes_pinned_body_and_current_head_separately(db, delivered_world):
    w = delivered_world
    drive.update_document(db, w.node, content="unreviewed replacement")
    result = tool_reads._inspect_artifact(
        db, SimpleNamespace(company_id=w.project.company_id), {"artifact_id": w.node.id}
    )
    assert result["content"] == w.items[0].content
    assert result["revision_id"] == w.revision.id and result["version"] == 1
    assert result["current_version"] == 2 and result["content_truncated"] is False


@pytest.mark.parametrize("surface", ["consume", "resolve", "delivery"])
def test_forced_done_without_matching_formal_pass_cannot_handoff(db, delivered_world, surface):
    w = delivered_world
    # Model corruption / old host bypass: status alone is not formal review.
    w.task.status = "done"
    db.commit()
    if surface == "consume":
        with pytest.raises(handoff.LineageViolation, match="review"):
            declaration(db, w)
    elif surface == "delivery":
        with pytest.raises(artifacts.ArtifactRefError, match="review"):
            artifacts.resolve_artifact_refs(db, company_id=w.project.company_id, refs=[w.node.id])
    else:
        from app.repositories import handoff as repo

        repo.add_input(db, task_id=w.downstream.id, source_task_id=w.task.id)
        assert handoff.resolve_input_artifacts(db, w.downstream) == ()
        assert handoff.missing_input_sources(db, w.downstream) == (w.task.id,)


@pytest.mark.parametrize("surface", ["pass", "consume", "resolve", "delivery", "inspect"])
@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_unavailable_saved_bytes_never_fall_back_to_current_body(
    db, delivered_world, surface, damage
):
    w = delivered_world
    request = open_review(db, w)
    if surface not in {"pass", "inspect"}:
        decide(db, w, request)
        declaration(db, w)
    original = Path(settings.data_root) / w.revision.content_path
    if damage == "missing":
        original.unlink()
    else:
        original.write_text("damaged")
    if surface == "pass":
        with pytest.raises(reviews.ReviewError, match="artifact"):
            decide(db, w, request)
        db.commit()  # A caught failure must not publish a verdict.
        assert request.status == "open" and w.task.status == "in_review"
    elif surface == "consume":
        with pytest.raises(handoff.LineageViolation):
            declaration(db, w)
    elif surface == "resolve":
        with pytest.raises(handoff.LineageViolation):
            handoff.resolve_input_artifacts(db, w.downstream)
    elif surface == "delivery":
        with pytest.raises(artifacts.ArtifactRefError):
            artifacts.resolve_artifact_refs(db, company_id=w.project.company_id, refs=[w.node.id])
    else:
        from app.work.tools import ToolError

        with pytest.raises(ToolError):
            tool_reads._inspect_artifact(
                db, SimpleNamespace(company_id=w.project.company_id), {"artifact_id": w.node.id}
            )


def test_each_consumer_session_appends_its_exact_reference(db, delivered_world):
    w = delivered_world
    decide(db, w)
    declared = declaration(db, w)
    first = consumer_session(db, w)
    first_rows = record(db, w, first)
    assert len(first_rows) == 1 and first_rows[0].revision_id == w.revision.id
    assert record(db, w, first) == []
    first.status, first.ended_at = "failed", utcnow()
    db.commit()
    second = consumer_session(db, w)
    assert len(record(db, w, second)) == 1
    rows = list(db.scalars(select(ArtifactLink).where(ArtifactLink.task_id == w.downstream.id)))
    assert len(rows) == 3 and declared.work_session_id is None
    uses = handoff.consumed_artifacts(db, w.downstream.id)
    assert {u.work_session_id for u in uses} == {first.id, second.id}
    assert all(u.revision_id == w.revision.id and u.sha256 == w.revision.sha256 for u in uses)


@pytest.mark.parametrize("field", ["revision_id", "sha256", "excerpt"])
def test_context_reference_cannot_be_substituted_when_recording_use(db, delivered_world, field):
    w = delivered_world
    decide(db, w)
    declaration(db, w)
    ws = consumer_session(db, w)
    ref = handoff.resolve_input_artifacts(db, w.downstream)[0]
    bad = replace(ref, **{field: 999999 if field == "revision_id" else "substituted"})
    with pytest.raises(handoff.LineageViolation):
        record(db, w, ws, [bad])
    db.commit()
    assert not list(db.scalars(select(ArtifactLink).where(ArtifactLink.work_session_id == ws.id)))


def new_round(db, w):
    task_service.transition_task(db, w.task, "in_progress")
    w.runtime = RuntimeSession(
        id="rework-artifact-run", instance=w.runtime.instance, task_id=w.task.id
    )
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
    w.recorder = prepare(w)
    accept_remote_run(w.runtime, "remote-rework-artifact-run", w.runtime.id)
    w.recorder.terminal("completed", "hermes.status")
    w.runtime.status = "completed"
    w.items = [artifact(w, "reworked.py", b"print('reworked')\n")]
    finish(db, w)


def test_rework_current_outputs_exclude_rejected_round(db, delivered_world):
    w = delivered_world
    old_id = w.node.id
    decide(db, w, verdict=ReviewVerdict.rework)
    new_round(db, w)
    assert [r.artifact_id for r in handoff.produced_artifacts(db, w.task.id)] == [w.node.id]
    assert db.get(DriveNode, old_id) is not None  # Historical output survives.
    request = open_review(db, w)
    facts = {r.kind: r.payload_json for r in review_repo.list_facts(db, request.id)}
    assert [i["artifact_id"] for i in facts["artifacts"]["items"]] == [w.node.id]
    decide(db, w, request)
    with pytest.raises(handoff.LineageViolation, match="current"):
        handoff.consume_artifact(db, task=w.downstream, artifact_id=old_id)


def test_open_review_from_before_block_and_rerun_cannot_pass_new_outputs(db, delivered_world):
    w = delivered_world
    old_request = open_review(db, w)
    # These are legal Task transitions; an old open request must not certify the rerun.
    task_service.transition_task(db, w.task, "blocked")
    task_service.transition_task(db, w.task, "todo")
    db.commit()
    new_round(db, w)
    with pytest.raises(reviews.ReviewError, match="artifact|session"):
        decide(db, w, old_request)
    db.commit()
    assert old_request.status == "open" and w.task.status == "in_review"


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE artifact_links SET revision_id=NULL WHERE id=:id",
        "UPDATE artifact_links SET reason='changed' WHERE id=:id",
        "DELETE FROM artifact_links WHERE id=:id",
        "INSERT OR REPLACE INTO artifact_links SELECT * FROM artifact_links WHERE id=:id",
    ],
)
def test_pinned_consumption_is_immutable_in_sql(db, delivered_world, statement):
    w = delivered_world
    decide(db, w)
    link = declaration(db, w)
    with pytest.raises(IntegrityError, match="artifact link"):
        db.execute(text(statement), {"id": link.id})
    db.rollback()


def test_review_input_facts_describe_actual_use_not_later_declarations(db, delivered_world):
    w = delivered_world
    decide(db, w)
    declaration(db, w)
    ws = consumer_session(db, w)
    record(db, w, ws)
    ws.status, ws.ended_at = "completed", utcnow()
    db.commit()
    later = artifacts.record_project_artifact(
        db,
        w.project,
        artifact_type="test_report",
        title="Declared after execution",
        content="Too late to be used",
        author_id=w.actor.id,
    )
    handoff.consume_artifact(db, task=w.downstream, artifact_id=later.id)
    facts = review_facts._inputs_fact(db, w.downstream)
    assert facts["count"] == 1
    assert facts["items"] == [
        {
            "artifact_id": w.node.id,
            "source_task_id": w.task.id,
            "doc_type": "source_code",
            "revision_id": w.revision.id,
            "version": 1,
            "sha256": w.revision.sha256,
        }
    ]


@pytest.mark.parametrize(
    "damage",
    [
        "revision",
        "actor",
        "consumer_task",
        "upgrade_unknown",
        "replace_unknown",
        "replace_unknown_slot",
    ],
)
def test_sql_rejects_wrong_revision_and_consumer_attribution(db, delivered_world, damage):
    w = delivered_world
    decide(db, w)
    ws = consumer_session(db, w)
    if damage in {"upgrade_unknown", "replace_unknown", "replace_unknown_slot"}:
        from app.repositories import handoff as repo

        link = repo.add_artifact_link(
            db,
            artifact_id=w.node.id,
            task_id=w.downstream.id,
            role="consumed_by",
            work_session_id=ws.id,
            actor_employee_id=w.consumer.id,
        )
        db.commit()
        with pytest.raises(IntegrityError, match="artifact link"):
            if damage == "upgrade_unknown":
                db.execute(
                    text("UPDATE artifact_links SET revision_id=:revision WHERE id=:id"),
                    {"revision": w.revision.id, "id": link.id},
                )
            else:
                next_id = link.id if damage == "replace_unknown" else link.id + 100
                db.execute(
                    text(
                        "INSERT OR REPLACE INTO artifact_links "
                        "(id,artifact_id,task_id,role,work_session_id,actor_employee_id,"
                        "reason,revision_id,created_at,updated_at) "
                        "SELECT :next_id,artifact_id,task_id,role,work_session_id,"
                        "actor_employee_id,"
                        "reason,:revision,created_at,updated_at FROM artifact_links WHERE id=:id"
                    ),
                    {"next_id": next_id, "revision": w.revision.id, "id": link.id},
                )
    else:
        fields = dict(
            artifact_id=w.node.id,
            task_id=w.downstream.id,
            role="consumed_by",
            work_session_id=ws.id,
            actor_employee_id=w.consumer.id,
            revision_id=w.revision.id,
        )
        if damage == "revision":
            drive.update_document(db, w.node, content="new version is not accepted")
            from app.repositories import drive as repo

            fields["revision_id"] = repo.get_revision(db, w.node.id, 2).id
        elif damage == "actor":
            fields["actor_employee_id"] = w.actor.id
        else:
            fields["task_id"] = w.task.id
        with pytest.raises(IntegrityError, match="artifact link"):
            db.add(ArtifactLink(**fields))
            db.flush()
    db.rollback()


@pytest.mark.parametrize("pending", [False, True])
def test_pass_does_not_overwrite_task_cancellation(db, delivered_world, pending):
    w = delivered_world
    request = open_review(db, w)
    assert w.task.status == "in_review"
    if pending:
        w.task.status = "cancelled"
    else:
        with SessionLocal() as other:
            other.get(Task, w.task.id).status = "cancelled"
            other.commit()
    with pytest.raises(reviews.ReviewError):
        decide(db, w, request)
    db.commit()
    db.expire_all()
    assert w.task.status == "cancelled" and request.status == "open"


def test_consumption_does_not_append_after_consumer_session_was_closed(db, delivered_world):
    from app.models.project import WorkSession

    w = delivered_world
    decide(db, w)
    declaration(db, w)
    ws = consumer_session(db, w)
    assert ws.status == "running"
    with SessionLocal() as other:
        closed = other.get(WorkSession, ws.id)
        closed.status, closed.ended_at = "failed", utcnow()
        other.commit()
    with pytest.raises(handoff.LineageViolation):
        record(db, w, ws)
    db.commit()
    assert not list(db.scalars(select(ArtifactLink).where(ArtifactLink.work_session_id == ws.id)))


def test_current_output_read_preserves_pending_worker_failure(db, delivered_world):
    w = delivered_world
    w.work.status = "failed"
    assert handoff.produced_artifacts(db, w.task.id) == ()
    db.commit()
    assert w.work.status == "failed"


@pytest.mark.parametrize("surface", ["consume", "delivery"])
def test_unaccepted_addition_cannot_borrow_real_tasks_pass(db, delivered_world, surface):
    w = delivered_world
    decide(db, w)
    extra = artifacts.record_project_artifact(
        db,
        w.project,
        artifact_type="source_code",
        title="Unaccepted addition",
        content="not in the receipt",
        author_id=w.actor.id,
        task_id=w.task.id,
    )
    db.commit()
    if surface == "consume":
        with pytest.raises(handoff.LineageViolation, match="current"):
            handoff.consume_artifact(db, task=w.downstream, artifact_id=extra.id)
    else:
        with pytest.raises(artifacts.ArtifactRefError, match="current"):
            artifacts.resolve_artifact_refs(db, company_id=w.project.company_id, refs=[extra.id])


def test_concurrent_same_session_consumption_creates_one_use(db, delivered_world):
    w = delivered_world
    decide(db, w)
    declaration(db, w)
    ws = consumer_session(db, w)
    refs = handoff.resolve_input_artifacts(db, w.downstream)
    task_id, artifact_id, ws_id, actor_id = w.downstream.id, w.node.id, ws.id, w.consumer.id
    db.commit()
    barrier = Barrier(2)

    def consume():
        with SessionLocal() as other:
            barrier.wait(timeout=5)
            return len(
                handoff.record_consumed(
                    other,
                    task_id=task_id,
                    artifact_ids=[artifact_id],
                    artifact_refs=refs,
                    work_session_id=ws_id,
                    actor_employee_id=actor_id,
                )
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: consume(), range(2)))
    assert sorted(results) == [0, 1]
    assert (
        len(list(db.scalars(select(ArtifactLink).where(ArtifactLink.work_session_id == ws_id))))
        == 1
    )


async def test_worker_request_digest_receives_the_exact_consumed_refs(
    db, delivered_world, monkeypatch
):
    from app.models.runtime_execution import RuntimeAttempt
    from app.work import telemetry

    w = delivered_world
    decide(db, w)
    declaration(db, w)
    expected = [r.as_dict() for r in handoff.resolve_input_artifacts(db, w.downstream)]
    w.downstream.status, w.downstream.kind = "todo", "development"
    db.commit()
    prepare_original = telemetry.prepare_attempt
    seen = []

    def capture(*args, **kwargs):
        result = prepare_original(*args, **kwargs)
        seen.append((kwargs["request_context"], result.attempt_id))
        return result

    monkeypatch.setattr(telemetry, "prepare_attempt", capture)
    await Orchestrator()._run_task(w.downstream.id)
    db.expire_all()
    assert w.downstream.status == "in_review" and len(seen) == 1
    context, attempt_id = seen[0]
    assert context["input_artifacts"] == expected
    attempt = db.get(RuntimeAttempt, attempt_id)
    uses = list(
        db.scalars(
            select(ArtifactLink).where(ArtifactLink.work_session_id == attempt.work_session_id)
        )
    )
    assert len(uses) == 1 and uses[0].revision_id == w.revision.id


def test_legacy_cross_project_link_cannot_enter_worker_context(db, delivered_world):
    from app.models.project import Project
    from app.repositories import handoff as repo

    w = delivered_world
    other = Project(company_id=w.project.company_id, name="Separate project")
    db.add(other)
    db.flush()
    node = artifacts.record_project_artifact(
        db,
        other,
        artifact_type="source_code",
        title="Other project's private source",
        content="must not enter this context",
        author_id=w.actor.id,
    )
    repo.add_artifact_link(db, artifact_id=node.id, task_id=w.downstream.id, role="consumed_by")
    db.commit()
    with pytest.raises(handoff.LineageViolation, match="another project"):
        handoff.resolve_input_artifacts(db, w.downstream)


def test_consumed_revision_keeps_its_original_worker_session(db, delivered_world):
    w = delivered_world
    decide(db, w)
    declaration(db, w)
    ws = consumer_session(db, w)
    record(db, w, ws)
    with pytest.raises(IntegrityError, match="artifact link"):
        db.execute(
            text("INSERT OR REPLACE INTO work_sessions SELECT * FROM work_sessions WHERE id=:id"),
            {"id": ws.id},
        )
    db.rollback()
