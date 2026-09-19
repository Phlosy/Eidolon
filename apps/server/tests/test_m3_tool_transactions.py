"""A tool's domain effects, audit and source events share one commit boundary."""

from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.database import SessionLocal
from app.events.bus import bus
from app.models.decision import DecisionRecord, ToolAudit
from app.models.enums import DecisionStatus, ReviewVerdict
from app.models.event import Event
from app.models.project import Project, Task
from app.models.review import ReviewRequest
from app.repositories import organization as org_repo
from app.repositories import project as project_repo
from app.services import tasks as task_service
from app.work import contracts as C
from app.work import decisions, reviews
from app.work import tool_executor as executor
from app.workflow import orchestrator as orchestrator_module


@pytest.fixture()
def world(db, default_company_id, monkeypatch):
    people = {row.slug: row for row in org_repo.list_employees(db, default_company_id)}
    manager = people["alice"]
    project = project_repo.create_project(
        db,
        company_id=default_company_id,
        name="Atomic tool actions",
        description="",
        status="in_progress",
        work_mode="managed",
        management_employee_id=manager.id,
    )
    task = task_service.create_task(
        db,
        project_id=project.id,
        title="Original task",
        kind="general",
        assignee_id=people["charlie"].id,
    )
    db.commit()
    context = executor.context_for_employee(db, manager, project_id=project.id)
    decision = decisions.open_decision(
        db,
        context,
        decision_type=C.DecisionKind.decompose_project,
        reason="Keep each applied action traceable",
        scope=f"project:{project.id}",
    )
    broadcasts = []
    notifications = []
    monkeypatch.setattr(
        bus,
        "_broadcast",
        lambda kind, data, company_id, *, event_id: broadcasts.append(
            (kind, data, company_id, event_id)
        ),
    )
    monkeypatch.setattr(orchestrator_module.orchestrator, "notify", notifications.append)
    return SimpleNamespace(
        company_id=default_company_id,
        manager=manager,
        worker=people["charlie"],
        project=project,
        task=task,
        context=context,
        decision=decision,
        broadcasts=broadcasts,
        notifications=notifications,
    )


def _execute(db, world, name="create_task", *, commit=True, **args):
    return executor.execute_tool(
        db,
        name=name,
        args=args or {"project_id": world.project.id, "title": "New task"},
        context=world.context,
        decision_id=world.decision.id,
        commit=commit,
    )


def _project_events(db, world):
    return list(db.scalars(select(Event).where(Event.project_id == world.project.id)))


@pytest.mark.parametrize("action", ["create_task", "update_task"])
def test_audit_failure_rolls_back_real_domain_effect_and_source_events(
    db, world, monkeypatch, action
):
    original = executor._record_tool_audit

    def unavailable(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("audit unavailable after flush")

    monkeypatch.setattr(executor, "_record_tool_audit", unavailable)
    args = (
        {"project_id": world.project.id, "title": "New task"}
        if action == "create_task"
        else {"task_id": world.task.id, "title": "Changed task"}
    )
    with pytest.raises(RuntimeError, match="audit unavailable"):
        _execute(db, world, action, **args)
    # Even if the caller commits unrelated work, no partial action may survive.
    db.commit()
    with SessionLocal() as observer:
        tasks = list(observer.scalars(select(Task).where(Task.project_id == world.project.id)))
        assert [row.title for row in tasks] == ["Original task"]
        assert not list(
            observer.scalars(select(ToolAudit).where(ToolAudit.decision_id == world.decision.id))
        )
        assert _project_events(observer, world) == []
    assert world.broadcasts == []


def test_database_audit_rejection_preserves_unrelated_caller_work(db, world):
    # A real failed SQL flush deactivates the savepoint. Its rollback must still
    # restore the Session, without rolling back the caller's earlier work.
    with db.get_bind().begin() as connection:
        connection.exec_driver_sql(
            "CREATE TRIGGER reject_tool_audit BEFORE INSERT ON tool_audits "
            "BEGIN SELECT RAISE(ABORT, 'receipt storage unavailable'); END"
        )
    world.project.description = "Caller-owned change"
    try:
        with pytest.raises(IntegrityError, match="receipt storage unavailable"):
            _execute(db, world, "update_task", task_id=world.task.id, title="Must roll back")
        db.commit()
        with SessionLocal() as observer:
            assert observer.get(Project, world.project.id).description == "Caller-owned change"
            assert observer.get(Task, world.task.id).title == "Original task"
            assert not list(
                observer.scalars(
                    select(ToolAudit).where(ToolAudit.decision_id == world.decision.id)
                )
            )
            assert _project_events(observer, world) == []
        assert world.broadcasts == []
    finally:
        db.rollback()
        with db.get_bind().begin() as connection:
            connection.exec_driver_sql("DROP TRIGGER reject_tool_audit")


@pytest.mark.parametrize("action", ["create_task", "update_task"])
def test_commit_false_keeps_effect_audit_and_events_in_caller_transaction(db, world, action):
    args = (
        {"project_id": world.project.id, "title": "New task"}
        if action == "create_task"
        else {"task_id": world.task.id, "title": "Changed task"}
    )
    result = _execute(db, world, action, commit=False, **args)
    assert result.ok, result.error
    assert db.get(ToolAudit, result.audit_id) is not None
    assert _project_events(db, world)
    assert world.broadcasts == []
    with SessionLocal() as observer:
        assert observer.get(ToolAudit, result.audit_id) is None
        assert _project_events(observer, world) == []
        assert observer.get(Task, world.task.id).title == "Original task"
    db.rollback()
    with SessionLocal() as observer:
        assert observer.get(ToolAudit, result.audit_id) is None
        assert _project_events(observer, world) == []
        assert [
            row.title
            for row in observer.scalars(select(Task).where(Task.project_id == world.project.id))
        ] == ["Original task"]
    assert world.broadcasts == []


@pytest.mark.parametrize("call_kind", ["read", "invalid_arguments", "not_authorized"])
def test_read_and_denial_audits_respect_caller_rollback(db, world, call_kind):
    name = "inspect_project" if call_kind == "read" else "create_task"
    context = (
        executor.context_for_employee(db, world.worker)
        if call_kind == "not_authorized"
        else world.context
    )
    args = {"project_id": world.project.id}
    if call_kind == "not_authorized":
        args["title"] = "Forbidden"
    result = executor.execute_tool(
        db,
        name=name,
        args=args,
        context=context,
        decision_id=None if call_kind == "read" else world.decision.id,
        commit=False,
    )
    assert result.ok if call_kind == "read" else result.reason == call_kind
    assert result.audit_id
    db.rollback()
    with SessionLocal() as observer:
        assert observer.get(ToolAudit, result.audit_id) is None


def test_success_broadcast_observes_committed_domain_audit_and_same_source_event(
    db, world, monkeypatch
):
    observed = []

    def broadcast(kind, data, company_id, *, event_id):
        with SessionLocal() as observer:
            source = observer.get(Event, event_id)
            assert source is not None and source.type == kind
            assert observer.get(Task, source.task_id) is not None
            audits = list(
                observer.scalars(
                    select(ToolAudit).where(ToolAudit.decision_id == world.decision.id)
                )
            )
            assert len(audits) == 1 and audits[0].outcome == "applied"
            observed.append((event_id, source.actor_person_id, source.payload))
        return True

    monkeypatch.setattr(bus, "_broadcast", broadcast)
    result = _execute(
        db,
        world,
        assignee_id=world.worker.id,
        project_id=world.project.id,
        title="Secret sk-transaction-secret",
    )
    assert result.ok
    with SessionLocal() as observer:
        sources = _project_events(observer, world)
        assert {source.type for source in sources} == {
            "task.created",
            "task.assigned",
            "work.task_created",
        }
        assert {item[0] for item in observed} == {source.id for source in sources}
        assert len(observed) == 3
        assert all(item[1] is not None for item in observed)
        assert "sk-transaction-secret" not in str([item[2] for item in observed])


@pytest.mark.parametrize("finish", ["rollback", "close"])
def test_caller_discard_never_offers_events_on_later_session_commit(db, world, finish):
    result = _execute(db, world, commit=False)
    assert result.ok
    getattr(db, finish)()
    db.commit()
    assert world.broadcasts == []


@pytest.mark.parametrize("finish", ["rollback", "commit"])
def test_nested_events_wait_for_outer_commit_and_discard_rolled_back_savepoint(db, world, finish):
    # Use a real outer SQLite transaction before creating a caller-owned savepoint.
    db.connection().exec_driver_sql("BEGIN IMMEDIATE")
    bus.publish("test.outer", {}, project_id=world.project.id, db=db)
    nested = db.begin_nested()
    bus.publish("test.nested", {}, project_id=world.project.id, db=db)
    getattr(nested, finish)()
    assert world.broadcasts == []
    with SessionLocal() as observer:
        assert _project_events(observer, world) == []
    db.commit()
    expected = {"test.outer", "test.nested"} if finish == "commit" else {"test.outer"}
    assert {item[0] for item in world.broadcasts} == expected
    with SessionLocal() as observer:
        assert {row.type for row in _project_events(observer, world)} == expected


def test_event_persistence_failure_rejects_and_rolls_back_action(db, world, monkeypatch):
    original = db.flush

    def fail_event_flush(*args, **kwargs):
        if any(isinstance(row, Event) for row in db.new):
            raise RuntimeError("source event unavailable")
        return original(*args, **kwargs)

    monkeypatch.setattr(db, "flush", fail_event_flush)
    result = _execute(db, world)
    assert not result.ok and result.reason == "handler_failed"
    with SessionLocal() as observer:
        assert [
            row.title
            for row in observer.scalars(select(Task).where(Task.project_id == world.project.id))
        ] == ["Original task"]
        assert observer.get(ToolAudit, result.audit_id).outcome == "handler_failed"
        assert _project_events(observer, world) == []
    assert world.broadcasts == []


def test_decision_commits_each_action_and_preserves_first_on_domain_rejection(
    db, world, monkeypatch
):
    original = executor.execute_tool
    before_second = []

    def execute(*args, **kwargs):
        if kwargs["name"] == "update_task":
            with SessionLocal() as observer:
                first = observer.scalar(select(Task).where(Task.title == "Committed first"))
                assert first is not None
                audits = list(
                    observer.scalars(
                        select(ToolAudit).where(ToolAudit.decision_id == kwargs["decision_id"])
                    )
                )
                assert len(audits) == 1 and audits[0].outcome == "applied"
                before_second.append(first.id)
        return original(*args, **kwargs)

    monkeypatch.setattr(executor, "execute_tool", execute)
    result = decisions.submit_envelope(
        db,
        world.context,
        decision_type=C.DecisionKind.decompose_project,
        reason="First action may stand if the second is rejected",
        scope=f"project:{world.project.id}",
        actions=[
            {
                "tool": "create_task",
                "args": {"project_id": world.project.id, "title": "Committed first"},
            },
            {
                "tool": "update_task",
                "args": {"task_id": world.task.id, "title": "Must roll back", "status": "done"},
            },
        ],
    )
    assert result.status is DecisionStatus.partially_applied
    assert before_second
    with SessionLocal() as observer:
        assert observer.get(Task, world.task.id).title == "Original task"
        assert observer.get(Task, before_second[0]).title == "Committed first"
        assert observer.get(DecisionRecord, result.decision_id).status == "PARTIALLY_APPLIED"
        assert [row.outcome for row in decisions.tool_audits_for(observer, result.decision_id)] == [
            "applied",
            "domain_rejected",
        ]


def _open_review(db, world, *, commit=True):
    world.task.status = "in_review"
    db.commit()
    return reviews.open_review_request(
        db,
        task=world.task,
        requester_employee_id=world.worker.id,
        reviewer_employee_id=world.manager.id,
        commit=commit,
    )


def test_open_review_and_replan_commit_false_drop_events_on_rollback(db, world):
    request = _open_review(db, world, commit=False)
    request_id = request.id
    reviews.replan_project(
        db,
        project=world.project,
        actor_employee_id=world.manager.id,
        reason="Manager requested a different plan",
        commit=False,
    )
    assert world.broadcasts == []
    db.rollback()
    with SessionLocal() as observer:
        assert observer.get(ReviewRequest, request_id) is None
        assert observer.get(Project, world.project.id).status == "in_progress"
        assert _project_events(observer, world) == []


@pytest.mark.parametrize("commit", [False, True])
def test_review_verdict_notifications_follow_durable_commit(db, world, monkeypatch, commit):
    request = _open_review(db, world)
    world.broadcasts.clear()
    observed = []

    def notify(message):
        with SessionLocal() as observer:
            assert observer.get(ReviewRequest, request.id).verdict == ReviewVerdict.passed.value
            assert observer.get(Task, world.task.id).status == "done"
            assert "task.review_passed" in {row.type for row in _project_events(observer, world)}
        observed.append(message)

    monkeypatch.setattr(orchestrator_module.orchestrator, "notify", notify)
    reviews.submit_verdict(
        db,
        request=request,
        reviewer_employee_id=world.manager.id,
        verdict=ReviewVerdict.passed,
        commit=commit,
    )
    if not commit:
        assert observed == [] and world.broadcasts == []
        db.commit()
    assert observed == [{"type": "task_finished", "task_id": world.task.id, "success": True}]
    assert [item[0] for item in world.broadcasts] == ["task.review_passed"]


def test_review_tool_audit_failure_discards_verdict_events_and_notifications(
    db, world, monkeypatch
):
    request = _open_review(db, world)
    world.broadcasts.clear()

    def fail_audit(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(executor, "_record_tool_audit", fail_audit)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        _execute(
            db,
            world,
            "submit_review_verdict",
            review_request_id=request.id,
            verdict=ReviewVerdict.passed.value,
        )
    db.commit()
    with SessionLocal() as observer:
        assert observer.get(ReviewRequest, request.id).verdict is None
        assert observer.get(Task, world.task.id).status == "in_review"
        assert {row.type for row in _project_events(observer, world)} == {"task.review_named"}
    assert world.broadcasts == [] and world.notifications == []


def test_post_commit_broadcast_and_notify_failures_do_not_fail_applied_tool(db, world, monkeypatch):
    request = _open_review(db, world)

    def unavailable(*args, **kwargs):
        raise RuntimeError("delivery unavailable")

    monkeypatch.setattr(bus, "_broadcast", unavailable)
    monkeypatch.setattr(orchestrator_module.orchestrator, "notify", unavailable)
    result = _execute(
        db,
        world,
        "submit_review_verdict",
        review_request_id=request.id,
        verdict=ReviewVerdict.passed.value,
    )
    assert result.ok, result.error
    with SessionLocal() as observer:
        assert observer.get(ToolAudit, result.audit_id).outcome == "applied"
        assert observer.get(Task, world.task.id).status == "done"
        assert observer.get(ReviewRequest, request.id).verdict == ReviewVerdict.passed.value
        assert {row.type for row in _project_events(observer, world)} == {
            "task.review_named",
            "task.review_passed",
        }
