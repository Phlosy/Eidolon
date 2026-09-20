"""Actual process death must not split project advancement from its source events."""

import asyncio
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.database import SessionLocal
from app.events.bus import bus
from app.models.event import Event
from app.models.organization import Company
from app.models.project import Milestone, Project, Task, TaskDependency
from app.work import contracts as C
from app.work import recovery, work_defaults
from app.workflow.orchestrator import Orchestrator

EVENTS = (
    "company.work_default_changed",
    "project.delivery_ready",
    "project.completed",
)


@pytest.fixture()
def world(db, default_company_id):
    company = db.get(Company, default_company_id)
    company.stage, company.settings = "FOUNDING", {}
    project = Project(
        company_id=company.id, name="原项目", status="in_progress", work_mode="guided"
    )
    db.add(project)
    db.flush()
    milestone = Milestone(project_id=project.id, name="原里程碑", status="in_progress")
    db.add(milestone)
    db.flush()
    task = Task(project_id=project.id, milestone_id=milestone.id, title="已验收任务", status="done")
    db.add(task)
    db.commit()
    return SimpleNamespace(company=company, project=project, milestone=milestone, task=task)


def events(db, w):
    return list(
        db.scalars(
            select(Event)
            .where(Event.company_id == w.company.id, Event.type.in_(EVENTS))
            .order_by(Event.id)
        )
    )


ADVANCE_PROCESS = """
import os, sys
from sqlalchemy import event
from app.core.database import SessionLocal
from app.workflow import orchestrator as module

if sys.argv[2] == "crash":
    def die(db):
        if not db.in_nested_transaction():
            os._exit(23)
    def crashing_session():
        db = SessionLocal()
        event.listen(db, "after_commit", die)
        return db
    module.SessionLocal = crashing_session
module.Orchestrator()._advance(int(sys.argv[1]), True)
"""


@pytest.mark.parametrize("explicit_default", [False, True])
def test_process_exit_after_first_commit_preserves_all_project_sources(db, world, explicit_default):
    w = world
    if explicit_default:
        w.company.settings = {C.WORK_MODE_SETTINGS_KEY: {"work_mode": "guided"}}
        db.commit()
    task_id = w.task.id
    crashed = subprocess.run(
        [sys.executable, "-c", ADVANCE_PROCESS, str(task_id), "crash"],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert crashed.returncode == 23, crashed.stderr
    db.expire_all()
    assert w.project.status == w.milestone.status == "completed"
    assert w.task.status == "done" and w.project.work_mode == "guided"
    assert work_defaults.company_work_mode_default(db, w.company).value == (
        "guided" if explicit_default else "managed"
    )
    saved = events(db, w)
    assert [row.type for row in saved] == list(EVENTS[1:] if explicit_default else EVENTS)
    for row in saved:
        if row.type.startswith("project."):
            assert row.project_id == w.project.id and row.task_id is None
            assert row.payload == {"id": w.project.id, "name": "原项目"}
    event_ids = [row.id for row in saved]
    default_snapshot = json.loads(json.dumps(w.company.settings))
    db.rollback()
    for _ in range(2):
        replayed = subprocess.run(
            [sys.executable, "-c", ADVANCE_PROCESS, str(task_id), "replay"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert replayed.returncode == 0, replayed.stderr
    db.expire_all()
    assert [row.id for row in events(db, w)] == event_ids
    assert w.company.settings == default_snapshot


@pytest.mark.parametrize("event_type", EVENTS)
def test_rejected_source_event_rolls_back_all_project_effects(db, world, event_type):
    w = world
    db.execute(
        text(
            "CREATE TRIGGER reject_advance_source BEFORE INSERT ON events "
            f"WHEN NEW.type = '{event_type}' BEGIN SELECT RAISE(ABORT, 'receipt rejected'); END"
        )
    )
    db.commit()
    with pytest.raises(IntegrityError):
        Orchestrator()._advance(w.task.id, True)
    db.expire_all()
    assert w.project.status == w.milestone.status == "in_progress"
    assert w.company.settings == {} and w.project.work_mode == "guided"
    assert w.task.status == "done" and events(db, w) == []
    db.execute(text("DROP TRIGGER reject_advance_source"))
    db.commit()
    Orchestrator()._advance(w.task.id, True)
    db.expire_all()
    event_ids = [row.id for row in events(db, w)]
    assert [row.type for row in events(db, w)] == list(EVENTS)
    Orchestrator()._advance(w.task.id, True)
    db.expire_all()
    assert [row.id for row in events(db, w)] == event_ids


def test_concurrent_advancement_has_one_completion_and_default_change(db, world):
    task_id = world.task.id
    db.rollback()
    barrier = Barrier(2)

    def advance():
        barrier.wait(timeout=5)
        Orchestrator()._advance(task_id, True)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [pool.submit(advance) for _ in range(2)]
        for result in results:
            result.result(timeout=10)
    db.expire_all()
    assert [row.type for row in events(db, world)] == list(EVENTS)


def test_broadcast_only_observes_complete_committed_facts(db, world, monkeypatch):
    seen = []
    project_id, milestone_id, company_id = world.project.id, world.milestone.id, world.company.id

    def broadcast(event_type, data, scope, *, event_id):
        if event_type in EVENTS:
            with SessionLocal() as observer:
                assert observer.get(Project, project_id).status == "completed"
                assert observer.get(Milestone, milestone_id).status == "completed"
                assert (
                    work_defaults.company_work_mode_default(
                        observer, observer.get(Company, company_id)
                    ).value
                    == "managed"
                )
                assert [
                    row.type
                    for row in observer.scalars(
                        select(Event)
                        .where(Event.company_id == company_id, Event.type.in_(EVENTS))
                        .order_by(Event.id)
                    )
                ] == list(EVENTS)
                assert observer.get(Event, event_id) is not None
            seen.append(event_type)
        return True

    monkeypatch.setattr(bus, "_broadcast", broadcast)
    Orchestrator()._advance(world.task.id, True)
    assert seen == list(EVENTS)


def test_default_promotion_uses_callers_commit_and_rollback(db, world, monkeypatch):
    company_id = world.company.id
    delivered = []
    monkeypatch.setattr(bus, "publish_persisted", lambda event_id: delivered.append(event_id))
    assert work_defaults.promote_after_project_completion(db, company_id, commit=False)
    assert delivered == []
    with SessionLocal() as observer:
        assert observer.get(Company, company_id).settings == {}
        assert (
            observer.scalar(
                select(Event.id).where(Event.company_id == company_id, Event.type.in_(EVENTS))
            )
            is None
        )
    db.rollback()
    assert world.company.settings == {} and delivered == []
    assert events(db, world) == []
    assert work_defaults.promote_after_project_completion(db, company_id, commit=False)
    assert delivered == []
    db.commit()
    saved = events(db, world)
    assert [row.type for row in saved] == [EVENTS[0]]
    assert delivered == [saved[0].id]
    assert world.project.work_mode == "guided" and world.project.status == "in_progress"


@pytest.mark.parametrize(
    "event_type", ["task.ready", "project.replan_required", "project.awaiting_management_action"]
)
def test_other_advance_notifications_keep_their_domain_contract(db, world, event_type):
    w = world
    target = w.task
    success = event_type != "project.replan_required"
    if event_type == "task.ready":
        target = Task(project_id=w.project.id, title="下游", status="todo")
        db.add(target)
        db.flush()
        db.add(TaskDependency(task_id=target.id, depends_on_id=w.task.id))
    elif event_type == "project.replan_required":
        w.task.status = "failed"
    else:
        w.project.status, w.task.status = "requested", "in_review"
    db.commit()
    task_status, project_status = w.task.status, w.project.status
    db.execute(
        text(
            "CREATE TRIGGER reject_advance_notice BEFORE INSERT ON events "
            f"WHEN NEW.type = '{event_type}' BEGIN SELECT RAISE(ABORT, 'notice rejected'); END"
        )
    )
    db.commit()
    with pytest.raises(IntegrityError):
        Orchestrator()._advance(w.task.id, success)
    db.expire_all()
    assert w.project.status == project_status and w.task.status == task_status
    assert w.company.settings == {} and w.milestone.status == "in_progress"
    assert db.scalar(select(Event.id).where(Event.project_id == w.project.id)) is None
    db.execute(text("DROP TRIGGER reject_advance_notice"))
    db.commit()
    Orchestrator()._advance(w.task.id, success)
    db.expire_all()
    saved = list(db.scalars(select(Event).where(Event.project_id == w.project.id)))
    assert [row.type for row in saved] == [event_type]
    assert saved[0].company_id == w.company.id
    assert w.task.status == task_status and w.company.settings == {}
    if event_type == "task.ready":
        assert saved[0].task_id == target.id
        assert saved[0].payload == {
            "id": target.id,
            "title": "下游",
            "kind": "general",
            "assignee_id": None,
        }
        assert target.status == "todo" and w.project.status == project_status
    else:
        assert saved[0].task_id is None and saved[0].payload["id"] == w.project.id
        if success:
            assert w.project.status == "planning"
            assert saved[0].payload["reason"] == "awaiting_management_action"
        else:
            assert w.project.status == project_status
            assert saved[0].payload["failed_task_id"] == w.task.id
            assert saved[0].payload["reason"] == "task_failed"


def test_two_projects_share_one_default_promotion_without_mixing_events(db, world):
    other = Project(company_id=world.company.id, name="第二项目", status="in_progress")
    db.add(other)
    db.flush()
    other_task = Task(project_id=other.id, title="已完成的第二任务", status="done")
    db.add(other_task)
    db.commit()
    ids = [world.task.id, other_task.id]
    barrier = Barrier(2)

    def advance(task_id):
        barrier.wait(timeout=5)
        Orchestrator()._advance(task_id, True)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(advance, identifier) for identifier in ids]
        for future in futures:
            future.result(timeout=10)
    db.expire_all()
    saved = events(db, world)
    assert sum(row.type == EVENTS[0] for row in saved) == 1
    for project in (world.project, other):
        assert project.status == "completed"
        assert [(row.type, row.payload) for row in saved if row.project_id == project.id] == [
            (kind, {"id": project.id, "name": project.name}) for kind in EVENTS[1:]
        ]


PROJECT_RECOVERY_PROCESS = """
import json, os, sys
from dataclasses import asdict
from sqlalchemy import event
from app.core.database import SessionLocal
from app.work import recovery

def factory():
    db = SessionLocal()
    if sys.argv[1] == "crash":
        def die(session):
            if not session.in_nested_transaction():
                os._exit(24)
        event.listen(db, "after_commit", die)
    return db
page = recovery.reconcile_projects(
    through_id=recovery.project_boundary(), session_factory=factory,
)
print(json.dumps(asdict(page)))
"""


@pytest.mark.parametrize("project_status", ["in_progress", "in_review"])
def test_task_commit_process_death_recovers_project_without_replaying_tasks(
    db, world, project_status
):
    w = world
    w.project.status, w.task.status = project_status, "in_review"
    db.commit()
    crashed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import os, sys
from app.core.database import SessionLocal
from app.models.project import Task
from app.services.tasks import transition_task
with SessionLocal() as db:
    transition_task(db, db.get(Task, int(sys.argv[1])), "done")
    db.commit()
os._exit(23)
""",
            str(w.task.id),
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert crashed.returncode == 23, crashed.stderr
    db.expire_all()
    assert w.task.status == "done" and w.project.status == project_status
    ended_at = w.task.actual_end_at
    assert ended_at is not None and events(db, w) == []
    db.rollback()
    restarted = subprocess.run(
        [sys.executable, "-c", PROJECT_RECOVERY_PROCESS, "crash"],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert restarted.returncode == 24, restarted.stderr
    db.expire_all()
    assert w.project.status == w.milestone.status == "completed"
    assert w.project.work_mode == "guided"
    assert w.task.status == "done" and w.task.actual_end_at == ended_at
    saved = events(db, w)
    assert [row.type for row in saved] == list(EVENTS)
    original = [(row.id, row.type, row.created_at) for row in saved]
    db.rollback()
    for _ in range(2):
        repeated = subprocess.run(
            [sys.executable, "-c", PROJECT_RECOVERY_PROCESS, "observe"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert repeated.returncode == 0, repeated.stderr
        assert json.loads(repeated.stdout)["items"][0]["outcome"] == "already_recorded"
    db.expire_all()
    assert [(row.id, row.type, row.created_at) for row in events(db, w)] == original
    assert w.task.actual_end_at == ended_at


@pytest.mark.parametrize("present", [(), (EVENTS[1],), (EVENTS[2],), EVENTS[1:]])
def test_legacy_completed_project_only_fills_missing_notifications(db, world, present):
    w = world
    w.project.status = "completed"
    for kind in present:
        bus.publish(
            kind,
            {"id": w.project.id, "name": w.project.name},
            company_id=w.company.id,
            project_id=w.project.id,
            db=db,
        )
    db.commit()
    existing = {row.type: row.id for row in events(db, w)}
    page = recovery.reconcile_projects(through_id=recovery.project_boundary())
    assert page.items[0].outcome == ("already_recorded" if len(present) == 2 else "notified")
    db.expire_all()
    saved = events(db, w)
    assert sorted(row.type for row in saved) == sorted(EVENTS[1:])
    assert all(row.id == existing[row.type] for row in saved if row.type in existing)
    assert w.company.settings == {} and w.milestone.status == "in_progress"
    assert w.project.work_mode == "guided"
    assert (
        recovery.reconcile_projects(through_id=w.project.id).items[0].outcome == "already_recorded"
    )


@pytest.mark.parametrize("entry", ["live", "recovery"])
def test_invalid_completed_task_graph_cannot_complete_project(db, world, entry):
    other = Task(project_id=world.project.id, title="循环依赖", status="done")
    db.add(other)
    db.flush()
    db.add_all(
        [
            TaskDependency(task_id=world.task.id, depends_on_id=other.id),
            TaskDependency(task_id=other.id, depends_on_id=world.task.id),
        ]
    )
    db.commit()
    if entry == "live":
        Orchestrator()._advance(world.task.id, True)
    else:
        page = recovery.reconcile_projects(through_id=world.project.id)
        assert page.items[0].outcome == "invalid_graph"
    db.expire_all()
    assert world.project.status == world.milestone.status == "in_progress"
    assert events(db, world) == [] and world.company.settings == {}
    assert world.task.status == other.status == "done"


async def test_actual_startup_recovers_project_even_with_dispatch_disabled(db, world, monkeypatch):
    monkeypatch.setattr(settings, "orchestrator_dispatch_enabled", False)
    owner = Orchestrator()
    owner.start()
    try:
        async with asyncio.timeout(3):
            while True:
                db.expire_all()
                if world.project.status == "completed":
                    break
                await asyncio.sleep(0.02)
        saved = [row.id for row in events(db, world)]
        assert len(saved) == 3 and owner._project_recovery_through is None
        await owner._handle({"type": "sweep"})
        assert [row.id for row in events(db, world)] == saved
    finally:
        await owner.stop()


@pytest.mark.parametrize(
    "task_status", ["in_progress", "in_review", "blocked", "cancelled", "failed", "todo"]
)
def test_project_recovery_never_turns_an_unfinished_task_into_completion(db, world, task_status):
    world.task.status = task_status
    db.commit()
    page = recovery.reconcile_projects(through_id=world.project.id)
    assert page.items[0].outcome == "tasks_incomplete"
    db.expire_all()
    assert world.task.status == task_status and world.project.status == "in_progress"
    assert world.milestone.status == "in_progress" and world.company.settings == {}
    assert events(db, world) == []


@pytest.mark.parametrize(
    "status", ["requested", "planning", "waiting_for_management", "cancelled", "rejected"]
)
def test_project_recovery_keeps_management_and_terminal_project_states(db, world, status):
    world.project.status = status
    db.commit()
    page = recovery.reconcile_projects(through_id=world.project.id)
    assert page.items[0].outcome == "ineligible_project_status"
    db.expire_all()
    assert world.project.status == status and world.task.status == "done"
    assert events(db, world) == [] and world.company.settings == {}


def test_empty_project_and_inconsistent_historical_completion_are_not_invented(db, world):
    empty = Project(company_id=world.company.id, name="空项目", status="in_progress")
    db.add(empty)
    world.project.status, world.task.status = "completed", "in_review"
    db.commit()
    page = recovery.reconcile_projects(through_id=empty.id)
    assert [row.outcome for row in page.items] == ["tasks_incomplete", "tasks_incomplete"]
    db.expire_all()
    assert empty.status == "in_progress" and world.project.status == "completed"
    assert world.task.status == "in_review" and events(db, world) == []


def add_completed_project(db, company_id):
    project = Project(company_id=company_id, name="后续项目", status="in_progress")
    db.add(project)
    db.flush()
    db.add(Task(project_id=project.id, title="已有完成事实", status="done"))
    db.commit()
    return project


def test_project_recovery_pages_respect_startup_cohort_and_company_scope(db, world):
    from app.core.request_context import (
        RequestIdentity,
        reset_request_identity,
        set_request_identity,
    )

    second = add_completed_project(db, world.company.id)
    through = recovery.project_boundary()
    later = add_completed_project(db, world.company.id)
    first = recovery.reconcile_projects(through_id=through, limit=1)
    assert not first.complete and first.cursor == world.project.id
    second_page = recovery.reconcile_projects(through_id=through, after_id=first.cursor, limit=1)
    assert second_page.complete and [item.project_id for item in second_page.items] == [second.id]
    db.expire_all()
    assert world.project.status == second.status == "completed" and later.status == "in_progress"
    token = set_request_identity(RequestIdentity(1, 99999, "owner", 1))
    try:
        assert recovery.project_boundary() == 0
        assert recovery.reconcile_projects(through_id=later.id).items == ()
    finally:
        reset_request_identity(token)


def test_project_recovery_readonly_pages_never_take_a_writer_lock(db, world):
    Orchestrator()._advance(world.task.id, True)
    with SessionLocal() as setup:
        setup.add(Project(company_id=world.company.id, name="待管理", status="requested"))
        setup.commit()

    def factory():
        session = SessionLocal()

        def read_only(conn, cursor, statement, parameters, context, executemany):
            assert (
                not statement.strip()
                .upper()
                .startswith(("BEGIN IMMEDIATE", "INSERT", "UPDATE", "DELETE"))
            )

        event.listen(session.connection(), "before_cursor_execute", read_only)
        return session

    page = recovery.reconcile_projects(
        through_id=recovery.project_boundary(), session_factory=factory
    )
    assert [item.outcome for item in page.items] == [
        "already_recorded",
        "ineligible_project_status",
    ]


@pytest.mark.parametrize("change", ["cancel", "new_task"])
def test_project_recovery_rechecks_sources_after_readonly_observation(db, world, change):
    project_id = world.project.id
    calls = 0

    def factory():
        nonlocal calls
        calls += 1
        if calls == 2:
            with SessionLocal() as writer:
                if change == "cancel":
                    writer.get(Project, project_id).status = "cancelled"
                else:
                    writer.add(Task(project_id=project_id, title="新增未完成任务", status="todo"))
                writer.commit()
        return SessionLocal()

    page = recovery.reconcile_projects(through_id=project_id, session_factory=factory)
    assert page.items[0].outcome == (
        "ineligible_project_status" if change == "cancel" else "tasks_incomplete"
    )
    db.expire_all()
    assert world.project.status == ("cancelled" if change == "cancel" else "in_progress")
    assert world.company.settings == {} and events(db, world) == []


def test_concurrent_project_scans_and_live_advance_share_original_effects(db, world):
    project_id, task_id = world.project.id, world.task.id
    barrier = Barrier(3)

    def run(source):
        barrier.wait(timeout=5)
        if source == "live":
            Orchestrator()._advance(task_id, True)
        else:
            return recovery.reconcile_projects(through_id=project_id).items[0].outcome

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(run, source) for source in ("live", "scan", "scan")]
        outcomes = [future.result(timeout=10) for future in futures]
    assert all(value in {"completed", "already_recorded"} for value in outcomes[1:])
    db.expire_all()
    assert [row.type for row in events(db, world)] == list(EVENTS)
    assert world.project.status == world.milestone.status == "completed"


def test_project_recovery_storage_failure_rolls_back_and_retries_original_page(db, world):
    db.execute(
        text(
            "CREATE TRIGGER reject_recovery_completion BEFORE INSERT ON events "
            "WHEN NEW.type = 'project.completed' BEGIN SELECT RAISE(ABORT, 'rejected'); END"
        )
    )
    db.commit()
    with pytest.raises(IntegrityError):
        recovery.reconcile_projects(through_id=world.project.id)
    db.expire_all()
    assert world.project.status == world.milestone.status == "in_progress"
    assert world.company.settings == {} and events(db, world) == []
    db.execute(text("DROP TRIGGER reject_recovery_completion"))
    db.commit()
    assert recovery.reconcile_projects(through_id=world.project.id).items[0].outcome == "completed"
    assert (
        recovery.reconcile_projects(through_id=world.project.id).items[0].outcome
        == "already_recorded"
    )


@pytest.mark.parametrize("bounds", [dict(limit=True), dict(limit=101), dict(after_id=2)])
def test_invalid_project_pages_fail_before_reading(bounds):
    def forbidden():
        pytest.fail("invalid bounds reached storage")

    with pytest.raises(ValueError, match="invalid recovery page"):
        recovery.reconcile_projects(through_id=1, session_factory=forbidden, **bounds)


async def test_startup_project_recovery_keeps_cursor_after_storage_failure(db, world, monkeypatch):
    calls = []
    actual = recovery.reconcile_projects

    def fail_once(**kwargs):
        calls.append(kwargs["after_id"])
        if len(calls) == 1:
            raise IntegrityError("private", {}, Exception("storage unavailable"))
        return actual(**kwargs)

    monkeypatch.setattr(recovery, "reconcile_projects", fail_once)
    monkeypatch.setattr(settings, "orchestrator_dispatch_enabled", False)
    owner = Orchestrator()
    owner.start()
    try:
        async with asyncio.timeout(3):
            while True:
                db.expire_all()
                if world.project.status == "completed":
                    break
                await asyncio.sleep(0.02)
        assert calls == [0, 0]
        await owner._handle({"type": "sweep"})
        assert calls == [0, 0]
    finally:
        await owner.stop()
