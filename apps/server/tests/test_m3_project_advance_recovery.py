"""Actual process death must not split project advancement from its source events."""

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.core.database import SessionLocal
from app.events.bus import bus
from app.models.event import Event
from app.models.organization import Company
from app.models.project import Milestone, Project, Task, TaskDependency
from app.work import contracts as C
from app.work import work_defaults
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
