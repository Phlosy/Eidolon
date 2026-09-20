"""U7 local dispatcher evidence; no adapter or real model acceptance."""

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier
from types import SimpleNamespace

import pytest
from sqlalchemy import event, func, select, update
from sqlalchemy.orm import sessionmaker

from app.core.database import SessionLocal, engine
from app.events.bus import bus
from app.models.base import utcnow
from app.models.decision import DecisionRecord, ToolAudit
from app.models.drive import DriveNode
from app.models.event import Event
from app.models.knowledge import KnowledgeItem
from app.models.organization import Company, Employee
from app.models.person import Person
from app.models.position import PositionDefinition
from app.models.project import Artifact, Project, Task, WorkSession
from app.models.runtime_operation import RuntimeOperation
from app.repositories import position as position_repo
from app.repositories import project as project_repo
from app.services import artifacts as artifact_service
from app.work import decisions
from app.work import tool_executor as executor
from app.work.runtime_bridge import BridgeError, handle_reply, parse_reply, reply_contract
from app.work.runtime_operations import RuntimeExecution


@pytest.fixture()
def world(db, default_company_id, monkeypatch):
    actor = db.scalar(select(Employee).where(Employee.slug == "alice"))
    colleague = db.scalar(select(Employee).where(Employee.slug == "charlie"))
    project = Project(
        company_id=default_company_id,
        name="Bridge project",
        status="in_progress",
        management_employee_id=actor.id,
    )
    db.add(project)
    db.flush()
    task = Task(
        project_id=project.id,
        title="Active session task",
        status="in_progress",
        assignee_id=actor.id,
    )
    target = Task(project_id=project.id, title="Target task", status="backlog")
    db.add_all([task, target])
    db.flush()
    session = project_repo.create_work_session(
        db,
        task_id=task.id,
        employee_id=actor.id,
        runtime_type="hermes",
        model="original-model",
        started_at=utcnow(),
    )
    db.commit()
    broadcasts = []

    def broadcast(kind, data, company_id, *, event_id):
        # Source broadcasts are only observable after effect+audit+correlation commit.
        with SessionLocal() as observer:
            event = observer.get(Event, event_id)
            assert event is not None
            audits = list(
                observer.scalars(
                    select(ToolAudit).where(
                        ToolAudit.project_id == project.id,
                        ToolAudit.runtime_operation_id.is_not(None),
                    )
                )
            )
            assert audits
        broadcasts.append(event_id)

    monkeypatch.setattr(bus, "_broadcast", broadcast)
    return SimpleNamespace(
        actor=actor,
        colleague=colleague,
        project=project,
        task=task,
        target=target,
        session=session,
        broadcasts=broadcasts,
    )


def _read(world, key="read-1"):
    return {
        "version": 1,
        "kind": "read",
        "operation_key": key,
        "tool": "inspect_project",
        "args": {"project_id": world.project.id},
    }


def _decision(world, key="decision-1", actions=None):
    return {
        "version": 1,
        "kind": "decision",
        "operation_key": key,
        "decision": {
            "decision_type": "decompose_project",
            "reason": "Make the requested change",
            "scope": f"project:{world.project.id}",
            "context": {"project_id": world.project.id, "note": "A supplied fact"},
            "actions": actions
            or [
                {
                    "tool": "create_task",
                    "args": {"project_id": world.project.id, "title": "New work"},
                },
                {
                    "tool": "update_task",
                    "args": {"task_id": world.target.id, "title": "Updated work"},
                },
            ],
        },
    }


def _send(world, request):
    return handle_reply(world.session.id, json.dumps(request))


def _counts():
    with SessionLocal() as db:
        return tuple(
            db.scalar(select(func.count()).select_from(model))
            for model in (RuntimeOperation, DecisionRecord, ToolAudit, Task, Event)
        )


def test_finish_is_a_strict_versioned_request():
    assert parse_reply('{"version":1,"kind":"finish","operation_key":"round-1"}') == {
        "version": 1,
        "kind": "finish",
        "operation_key": "round-1",
        "summary": "",
    }


def test_finish_only_acknowledges_conversation_and_never_changes_business_state(db, world):
    reply = {"version": 1, "kind": "finish", "operation_key": "finished", "summary": "All done"}
    before = _counts()
    result = _send(world, reply)
    assert result["status"] == "finished" and result["results"] == []
    assert _send(world, reply) == result
    assert _counts() == (before[0] + 1, *before[1:])
    db.expire_all()
    assert db.get(WorkSession, world.session.id).status == "running"
    assert db.get(WorkSession, world.session.id).ended_at is None
    assert db.get(Task, world.task.id).status == "in_progress"
    assert db.get(Project, world.project.id).status == "in_progress"


def test_read_is_saved_and_replayed_after_source_and_desired_runtime_change(db, world):
    request = _read(world)
    result = _send(world, request)
    receipt = result["results"][0]
    assert result["status"] == "completed" and receipt["ok"]
    assert receipt["data"]["result_kind"] == "facts_only"
    assert receipt["actor"]["runtime_type"] == "hermes"
    world.project.name = "Changed later"
    world.actor.runtime_type = "openclaw"
    db.commit()
    fresh_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    assert (
        handle_reply(
            world.session.id,
            json.dumps(request, sort_keys=True, indent=2),
            session_factory=fresh_factory,
        )
        == result
    )
    assert db.scalar(select(func.count()).select_from(ToolAudit)) == 1
    assert db.scalar(select(func.count()).select_from(DecisionRecord)) == 0


def test_real_domain_actions_return_full_audits_and_replay_in_a_new_process(db, world):
    request = _decision(world)
    result = _send(world, request)
    assert result["decision_status"] == "APPLIED"
    assert [r["reason"] for r in result["results"]] == ["applied", "applied"]
    assert all(r["authority"]["allowed"] and r["audit_id"] for r in result["results"])
    with SessionLocal() as observer:
        assert observer.get(Task, world.target.id).title == "Updated work"
        assert observer.scalar(select(Task).where(Task.title == "New work")) is not None
        record = observer.get(DecisionRecord, result["decision_id"])
        assert record.actor_employee_id == world.actor.id
        assert record.actor_person_id == world.actor.person_id
        assert record.context_json["authority"]
        assert [a.action_index for a in decisions.tool_audits_for(observer, record.id)] == [0, 1]
    before = _counts()
    events = list(world.broadcasts)
    # The new process has no protocol cache or caller identity map. It only gets
    # the original host session ID and retransmitted body against this private DB.
    program = (
        "import json,sys; from app.work.runtime_bridge import handle_reply; "
        "print(json.dumps(handle_reply(int(sys.argv[1]),sys.argv[2])))"
    )
    process = subprocess.run(
        [sys.executable, "-c", program, str(world.session.id), json.dumps(request)],
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    assert json.loads(process.stdout) == result
    assert _counts() == before and world.broadcasts == events


@pytest.mark.parametrize("changed", ["args", "kind", "reason"])
def test_same_key_different_request_never_writes(db, world, changed):
    request = _decision(world)
    result = _send(world, request)
    before = _counts()
    other = deepcopy(request)
    if changed == "args":
        other["decision"]["actions"][0]["args"]["title"] = "Different work"
    elif changed == "kind":
        other = _read(world, request["operation_key"])
    else:
        other["decision"]["reason"] = "Different intent"
    with pytest.raises(BridgeError) as error:
        _send(world, other)
    assert error.value.reason == "operation_conflict"
    assert error.value.operation_id == result["operation_id"]
    assert _counts() == before


def test_two_connections_racing_the_same_key_apply_each_action_once(world, monkeypatch):
    request = _decision(world)
    barrier = Barrier(2)
    calls = []
    original = executor.execute_tool

    def execute(*args, **kwargs):
        calls.append(kwargs["name"])
        return original(*args, **kwargs)

    monkeypatch.setattr(executor, "execute_tool", execute)

    def submit():
        factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        barrier.wait(timeout=5)
        return handle_reply(world.session.id, json.dumps(request), session_factory=factory)

    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = [future.result(timeout=20) for future in [pool.submit(submit), pool.submit(submit)]]
    assert a == b
    assert calls == ["create_task", "update_task"]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(RuntimeOperation)) == 1
        assert db.scalar(select(func.count()).select_from(DecisionRecord)) == 1
        assert db.scalar(select(func.count()).select_from(ToolAudit)) == 2


class SimulatedProcessExit(BaseException):
    pass


def test_crash_after_first_commit_resumes_only_missing_action(world, monkeypatch):
    request = _decision(world)
    original = RuntimeExecution.execute_action

    def crash(self, index, *args, **kwargs):
        result = original(self, index, *args, **kwargs)
        if index == 0:
            raise SimulatedProcessExit()
        return result

    monkeypatch.setattr(RuntimeExecution, "execute_action", crash)
    with pytest.raises(SimulatedProcessExit):
        _send(world, request)
    with SessionLocal() as db:
        op = db.scalar(select(RuntimeOperation))
        assert op.decision_id is not None
        assert db.get(DecisionRecord, op.decision_id).status == "EXECUTING"
        receipt = db.scalar(select(ToolAudit))
        assert receipt.action_index == 0
        first_audit_id = receipt.id
        assert db.get(Task, world.target.id).title == "Target task"
    before_events = list(world.broadcasts)
    monkeypatch.setattr(RuntimeExecution, "execute_action", original)
    calls = []
    execute = executor.execute_tool

    def tracked(*args, **kwargs):
        calls.append(kwargs["name"])
        return execute(*args, **kwargs)

    monkeypatch.setattr(executor, "execute_tool", tracked)
    result = _send(world, request)
    assert result["decision_status"] == "APPLIED"
    assert result["results"][0]["audit_id"] == first_audit_id
    assert calls == ["update_task"]
    assert world.broadcasts[: len(before_events)] == before_events
    assert len(world.broadcasts) == len(set(world.broadcasts))


def test_process_exit_after_first_commit_resumes_missing_action_in_another_process(world):
    request = json.dumps(_decision(world))
    program = """
import os, sys
from app.work.runtime_bridge import handle_reply
from app.work.runtime_operations import RuntimeExecution
original = RuntimeExecution.execute_action
def exit_after_commit(self, index, *args, **kwargs):
    result = original(self, index, *args, **kwargs)
    if index == 0:
        os._exit(23)
    return result
RuntimeExecution.execute_action = exit_after_commit
handle_reply(int(sys.argv[1]), sys.argv[2])
"""
    crashed = subprocess.run(
        [sys.executable, "-c", program, str(world.session.id), request],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert crashed.returncode == 23, crashed.stderr
    with SessionLocal() as db:
        operation = db.scalar(select(RuntimeOperation))
        assert db.get(DecisionRecord, operation.decision_id).status == "EXECUTING"
        receipt = db.scalar(select(ToolAudit))
        assert receipt.action_index == 0
        first_receipt_id = receipt.id
        new_task_id = db.scalar(select(Task.id).where(Task.title == "New work"))
        first_events = list(db.scalars(select(Event.id).where(Event.task_id == new_task_id)))
        assert first_events
        assert db.get(Task, world.target.id).title == "Target task"
    retry_program = (
        "import json,sys; from app.work.runtime_bridge import handle_reply; "
        "print(json.dumps(handle_reply(int(sys.argv[1]),sys.argv[2])))"
    )
    recovered = subprocess.run(
        [sys.executable, "-c", retry_program, str(world.session.id), request],
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    result = json.loads(recovered.stdout)
    assert result["decision_status"] == "APPLIED" and result["action_indices"] == [0, 1]
    assert result["results"][0]["audit_id"] == first_receipt_id
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(ToolAudit)) == 2
        assert db.get(Task, world.target.id).title == "Updated work"
        assert (
            db.scalar(select(func.count()).select_from(Task).where(Task.title == "New work")) == 1
        )
        assert (
            list(db.scalars(select(Event.id).where(Event.task_id == new_task_id))) == first_events
        )


def test_domain_denial_preserves_prior_action_and_replays_denial(world):
    request = _decision(world)
    request["decision"]["actions"][1]["args"]["status"] = "done"
    result = _send(world, request)
    assert result["status"] == "completed" and result["decision_status"] == "PARTIALLY_APPLIED"
    assert result["results"][0]["ok"]
    denied = result["results"][1]
    assert denied["reason"] == "domain_rejected" and denied["error"] and denied["audit_id"]
    with SessionLocal() as db:
        assert db.get(Task, world.target.id).title == "Target task"
        assert db.scalar(select(Task).where(Task.title == "New work")) is not None
    assert _send(world, request) == result


def test_revoked_position_is_checked_per_action_and_saved_denial_needs_new_operation(
    db, world, monkeypatch
):
    assignment = position_repo.active_primary_assignment(db, world.actor.id)
    assignment_id = assignment.id
    db.commit()
    original = RuntimeExecution.execute_action

    def revoke(self, index, *args, **kwargs):
        result = original(self, index, *args, **kwargs)
        if index == 0:
            with SessionLocal() as other:
                row = other.get(type(assignment), assignment_id)
                row.effective_to = utcnow()
                other.commit()
        return result

    monkeypatch.setattr(RuntimeExecution, "execute_action", revoke)
    request = _decision(world)
    result = _send(world, request)
    assert result["decision_status"] == "PARTIALLY_APPLIED"
    assert result["results"][1]["reason"] == "not_authorized"
    monkeypatch.setattr(RuntimeExecution, "execute_action", original)
    assignment.effective_to = None
    # The long-lived fixture had not observed the other connection's update.
    db.execute(
        update(type(assignment))
        .where(type(assignment).id == assignment_id)
        .values(effective_to=None)
    )
    db.commit()
    assert _send(world, request) == result
    fresh = _decision(
        world, key="new-permission-attempt", actions=[request["decision"]["actions"][1]]
    )
    assert _send(world, fresh)["decision_status"] == "APPLIED"


def test_invalid_actor_mid_envelope_stops_without_fabricating_denial(world, monkeypatch):
    original = RuntimeExecution.execute_action

    def suspend(self, index, *args, **kwargs):
        result = original(self, index, *args, **kwargs)
        if index == 0:
            with SessionLocal() as db:
                db.get(Employee, world.actor.id).lifecycle_status = "suspended"
                db.commit()
        return result

    monkeypatch.setattr(RuntimeExecution, "execute_action", suspend)
    result = _send(world, _decision(world))
    assert result["status"] == "interrupted" and result["reason"] == "employee_not_active"
    assert result["decision_status"] == "EXECUTING"
    assert result["action_indices"] == [0]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(ToolAudit)) == 1
        assert db.get(Task, world.target.id).title == "Target task"


@pytest.mark.parametrize("status", ["APPLIED", "FAILED", "PARTIALLY_APPLIED", "SUPERSEDED"])
def test_terminal_or_superseded_decision_never_executes_a_missing_action(
    world, monkeypatch, status
):
    original = RuntimeExecution.execute_action

    def close_decision(self, index, *args, **kwargs):
        result = original(self, index, *args, **kwargs)
        if index == 0:
            with SessionLocal() as db:
                decision = db.get(DecisionRecord, kwargs["decision_id"])
                decision.status = status
                decision.resolved_at = utcnow()
                db.commit()
        return result

    monkeypatch.setattr(RuntimeExecution, "execute_action", close_decision)
    result = _send(world, _decision(world))
    assert result["status"] == "interrupted" and result["reason"] == "decision_not_resumable"
    assert result["decision_status"] == status and result["action_indices"] == [0]


def test_no_position_allows_read_and_returns_honest_write_denial(db, world):
    position_repo.active_primary_assignment(db, world.actor.id).effective_to = utcnow()
    db.commit()
    assert _send(world, _read(world))["results"][0]["ok"]
    result = _send(world, _decision(world))
    assert result["decision_status"] == "FAILED"
    assert all(row["reason"] == "not_authorized" for row in result["results"])


def test_every_identity_gate_runs_after_a_physical_writer_transaction(world, monkeypatch):
    original = executor.context_for_work_session
    seen = []

    def gate(db, work_session_id):
        driver = db.connection().connection.driver_connection
        assert driver.in_transaction
        seen.append(db)
        return original(db, work_session_id)

    monkeypatch.setattr(executor, "context_for_work_session", gate)
    assert _send(world, _decision(world))["decision_status"] == "APPLIED"
    assert len(seen) == 3 and len({id(db) for db in seen}) == 3


def test_target_employee_id_is_a_legitimate_argument(world):
    result = _send(
        world,
        _decision(
            world,
            actions=[
                {
                    "tool": "assign_task",
                    "args": {"task_id": world.target.id, "employee_id": world.colleague.id},
                }
            ],
        ),
    )
    assert result["results"][0]["ok"], result
    assert result["results"][0]["actor"]["employee_id"] == world.actor.id


@pytest.mark.parametrize(
    "raw",
    [
        "All done",
        "```json\n{}\n```",
        "null",
        "[]",
        "true",
        "42",
        '"done"',
        '{"version":1,"version":1,"kind":"finish","operation_key":"k"}',
        '{"version":1,"kind":"finish","operation_key":"k","summary":NaN}',
        '{"version":1,"kind":"finish","operation_key":"k","summary":Infinity}',
        '{"version":1,"kind":"finish","operation_key":"k","summary":1e999}',
        '{"version":true,"kind":"finish","operation_key":"k"}',
        '{"version":2,"kind":"finish","operation_key":"k"}',
        '{"version":1,"kind":"finish","operation_key":"unsafe/key"}',
        '{"version":1,"kind":"finish","operation_key":"伪造"}',
        '{"version":1,"kind":"finish","operation_key":"k","employee_id":1}',
        '{"version":1,"kind":"finish","operation_key":"k","company_id":1}',
        '{"version":1,"kind":"finish","operation_key":"k","summary":null}',
        '{"version":1,"kind":"finish","operation_key":"k","summary":"\\ud800"}',
        "[" * 40 + "0" + "]" * 40,
        '{"version":1,"kind":"finish","operation_key":"' + "x" * 129 + '"}',
        " " * (64 * 1024 + 1),
    ],
)
def test_invalid_text_has_zero_business_writes(world, raw):
    before = _counts()
    with pytest.raises(BridgeError) as error:
        handle_reply(world.session.id, raw)
    assert error.value.reason == "invalid_request"
    assert _counts() == before


@pytest.mark.parametrize(
    "change",
    [
        "unknown_top",
        "unknown_decision",
        "unknown_action",
        "unknown_args",
        "unknown_context",
        "forged_actor",
        "forged_company",
        "forged_authority",
        "unknown_tool",
        "read_action",
        "empty_actions",
        "too_many_actions",
        "bad_scope",
        "bad_parent",
        "bad_reason",
        "bad_type",
        "boolean_array_id",
        "nested_load_key",
        "bad_reference_encoding",
    ],
)
def test_invalid_decision_structure_has_zero_business_writes(world, change):
    request = _decision(world)
    decision = request["decision"]
    action = decision["actions"][0]
    if change == "unknown_top":
        request["extra"] = 1
    elif change == "unknown_decision":
        decision["extra"] = 1
    elif change == "unknown_action":
        action["extra"] = 1
    elif change == "unknown_args":
        action["args"]["extra"] = 1
    elif change == "unknown_context":
        decision["context"]["extra"] = 1
    elif change == "forged_actor":
        action["args"]["actor_employee_id"] = world.actor.id
    elif change == "forged_company":
        action["args"]["company_id"] = world.project.company_id
    elif change == "forged_authority":
        decision["context"]["authority"] = {"allowed": True}
    elif change == "unknown_tool":
        action["tool"] = "auto_recruit_best_person"
    elif change == "read_action":
        decision["actions"] = [
            {"tool": "inspect_project", "args": {"project_id": world.project.id}}
        ]
    elif change == "empty_actions":
        decision["actions"] = []
    elif change == "too_many_actions":
        decision["actions"] *= 9
    elif change == "bad_scope":
        decision["scope"] = "galaxy:1"
    elif change == "bad_parent":
        decision["parent_decision_id"] = True
    elif change == "bad_reason":
        decision["reason"] = "  "
    elif change == "bad_type":
        decision["decision_type"] = "invented"
    elif change == "boolean_array_id":
        action["args"]["depends_on"] = [True]
    elif change == "nested_load_key":
        decision["context"]["load_summary"] = {
            "load": [{"employee_id": world.actor.id, "actor": {}}]
        }
    elif change == "bad_reference_encoding":
        decision["context"]["artifact_refs"] = [{"id": 1, "company_id": 1}]
    before = _counts()
    with pytest.raises(BridgeError) as error:
        _send(world, request)
    assert error.value.reason == "invalid_request"
    assert _counts() == before


def test_read_cannot_disguise_a_write_or_repeat_nested_keys(world):
    request = _read(world)
    request.update(tool="update_task", args={"task_id": world.target.id})
    before = _counts()
    with pytest.raises(BridgeError):
        _send(world, request)
    raw = json.dumps(_read(world)).replace('"project_id":', '"project_id": 1, "project_id":')
    with pytest.raises(BridgeError):
        handle_reply(world.session.id, raw)
    assert _counts() == before


@pytest.mark.parametrize("field", ["reason", "argument", "key"])
def test_escaped_surrogates_are_typed_parser_rejections_before_any_writes(world, field):
    request = _decision(world)
    if field == "reason":
        request["decision"]["reason"] = "\ud800"
    elif field == "argument":
        request["decision"]["actions"][0]["args"]["title"] = "\udfff"
    else:
        request["\ud800"] = 1
    before = _counts()
    # ensure_ascii=True produces valid ASCII transport text containing escapes.
    with pytest.raises(BridgeError) as error:
        handle_reply(world.session.id, json.dumps(request, ensure_ascii=True))
    assert error.value.reason == "invalid_request"
    assert _counts() == before


def test_normal_non_ascii_text_is_preserved_in_intent_and_receipt(world):
    request = _decision(
        world,
        actions=[
            {"tool": "create_task", "args": {"project_id": world.project.id, "title": "中文任务"}}
        ],
    )
    request["decision"]["reason"] = "根据已提供的事实创建任务"
    result = handle_reply(world.session.id, json.dumps(request, ensure_ascii=False))
    assert result["decision_status"] == "APPLIED"
    with SessionLocal() as db:
        decision = db.get(DecisionRecord, result["decision_id"])
        assert decision.reason == request["decision"]["reason"]
        audit = db.get(ToolAudit, result["results"][0]["audit_id"])
        assert audit.arguments_json["title"] == "中文任务"


@pytest.mark.parametrize("field", ["tool_id", "context_id", "huge_scope", "overflow_scope"])
def test_unstorable_integer_ids_are_typed_rejections_before_any_writes(world, field):
    request = _decision(world)
    if field == "tool_id":
        request["decision"]["actions"][0]["args"]["project_id"] = 2**63
    elif field == "context_id":
        request["decision"]["context"]["project_id"] = 2**63
    elif field == "huge_scope":
        request["decision"]["scope"] = "project:" + "9" * 5000
    else:
        request["decision"]["scope"] = f"project:{2**63}"
    before = _counts()
    with pytest.raises(BridgeError) as error:
        _send(world, request)
    assert error.value.reason == "invalid_request"
    assert _counts() == before


@pytest.mark.parametrize("state", ["ended", "offboarded", "reassigned", "completed_project"])
def test_invalid_initial_session_never_creates_intent(db, world, state):
    if state == "ended":
        world.session.ended_at = utcnow()
    elif state == "offboarded":
        world.actor.lifecycle_status = "offboarded"
    elif state == "reassigned":
        world.task.assignee_id = world.colleague.id
    else:
        world.project.status = "completed"
    db.commit()
    before = _counts()
    with pytest.raises(BridgeError):
        _send(world, _decision(world))
    assert _counts() == before


@pytest.fixture()
def foreign(db):
    company = Company(name="Foreign", slug="u7-foreign")
    person = Person(name="Foreign", slug="u7-foreign-person")
    db.add_all([company, person])
    db.flush()
    employee = Employee(
        company_id=company.id,
        person_id=person.id,
        name="Foreign",
        slug="u7-foreign",
        workspace_path="",
        memory_namespace="",
    )
    project = Project(company_id=company.id, name="Foreign", status="in_progress")
    position = PositionDefinition(company_id=company.id, code="u7-foreign", name="Foreign")
    db.add_all([employee, project, position])
    db.flush()
    task = Task(project_id=project.id, title="Foreign", assignee_id=employee.id)
    artifact = Artifact(company_id=company.id, project_id=project.id, title="Foreign")
    knowledge = KnowledgeItem(
        owner_employee_id=employee.id, owner_person_id=person.id, scope="company", title="Foreign"
    )
    parent = DecisionRecord(
        company_id=company.id,
        actor_employee_id=employee.id,
        actor_person_id=person.id,
        decision_type="decompose_project",
    )
    db.add_all([task, artifact, knowledge, parent])
    db.commit()
    return SimpleNamespace(
        company=company,
        person=person,
        employee=employee,
        project=project,
        position=position,
        task=task,
        artifact=artifact,
        knowledge=knowledge,
        parent=parent,
    )


@pytest.mark.parametrize(
    "reference",
    [
        "company",
        "project",
        "task",
        "employee",
        "person",
        "position",
        "parent",
        "context_project",
        "context_task",
        "context_employee",
        "context_artifact",
        "context_knowledge",
        "context_load_employee",
        "context_load_task",
    ],
)
def test_foreign_scope_parent_and_context_rejected_before_any_writes(world, foreign, reference):
    request = _decision(world)
    decision = request["decision"]
    context = decision["context"]
    if reference in {"company", "project", "task", "employee", "person", "position"}:
        decision["scope"] = f"{reference}:{getattr(foreign, reference).id}"
    elif reference == "parent":
        decision["parent_decision_id"] = foreign.parent.id
    elif reference == "context_project":
        context["project_id"] = foreign.project.id
    elif reference == "context_load_employee":
        context["load_summary"] = {"load": [{"employee_id": foreign.employee.id}]}
    elif reference == "context_load_task":
        context["load_summary"] = {
            "load": [{"employee_id": world.actor.id, "current_task_id": foreign.task.id}]
        }
    else:
        name = reference.removeprefix("context_")
        key = {
            "task": "task_ids",
            "employee": "candidate_employee_ids",
            "artifact": "artifact_refs",
            "knowledge": "knowledge_refs",
        }[name]
        context[key] = [getattr(foreign, name).id]
    before = _counts()
    with pytest.raises(BridgeError) as error:
        _send(world, request)
    assert error.value.reason == "invalid_reference"
    assert _counts() == before


@pytest.mark.parametrize("other_project", [False, True])
def test_project_read_artifact_ids_are_valid_decision_references(db, world, other_project):
    project = world.project
    if other_project:
        project = Project(company_id=world.project.company_id, name="Visible supporting project")
        db.add(project)
        db.flush()
    node = artifact_service.record_project_artifact(
        db,
        project,
        artifact_type="source_code",
        title="Current Drive artifact",
        content="Actual project document",
    )
    db.commit()
    read = _read(world)
    read["args"]["project_id"] = project.id
    read_result = _send(world, read)
    refs = [item["artifact_id"] for item in read_result["results"][0]["data"]["artifacts"]]
    assert refs == [node.id]
    request = _decision(world)
    request["decision"]["context"]["artifact_refs"] = refs

    result = _send(world, request)

    assert result["decision_status"] == "APPLIED"
    assert all(row["ok"] for row in result["results"])
    saved = db.get(DecisionRecord, result["decision_id"])
    assert saved.context_json["artifact_refs"] == refs
    assert _send(world, request) == result


@pytest.mark.parametrize(
    "invalid",
    ["foreign", "foreign_project", "folder", "no_project", "no_company", "private", "legacy"],
)
def test_legacy_id_collision_cannot_authorize_invalid_drive_reference(db, world, foreign, invalid):
    reference_id = 500_001
    if invalid != "legacy":
        db.add(
            DriveNode(
                id=reference_id,
                company_id=(
                    foreign.company.id
                    if invalid == "foreign"
                    else None
                    if invalid == "no_company"
                    else world.project.company_id
                ),
                project_id=(
                    foreign.project.id
                    if invalid == "foreign_project"
                    else None
                    if invalid == "no_project"
                    else world.project.id
                ),
                kind="folder" if invalid == "folder" else "document",
                zone="personal" if invalid == "private" else "projects",
                path=f"u8-reference-{invalid}",
                name="Not a visible project artifact",
            )
        )
    # The deprecated namespace must not authorize a same-numbered Drive reference.
    db.add(
        Artifact(
            id=reference_id,
            company_id=world.project.company_id,
            project_id=world.project.id,
            title="Legacy identity collision",
        )
    )
    db.commit()
    request = _decision(world)
    request["decision"]["context"]["artifact_refs"] = [reference_id]
    before = _counts()

    with pytest.raises(BridgeError) as error:
        _send(world, request)

    assert error.value.reason == "invalid_reference"
    assert _counts() == before


def test_knowledge_references_do_not_hydrate_the_corpus_for_each_action(db, world):
    items = [
        KnowledgeItem(
            owner_employee_id=world.actor.id,
            owner_person_id=world.actor.person_id,
            scope="company",
            title=f"Reference corpus {index}",
            content="x" * 4096,
        )
        for index in range(128)
    ]
    db.add_all(items)
    db.commit()
    request = _decision(world)
    request["decision"]["context"]["knowledge_refs"] = [items[0].id]
    loaded = []

    def record_load(item, context):
        loaded.append(item.id)

    event.listen(KnowledgeItem, "load", record_load)
    try:
        result = _send(world, request)
    finally:
        event.remove(KnowledgeItem, "load", record_load)

    assert result["decision_status"] == "APPLIED"
    assert len(result["results"]) == 2 and all(row["ok"] for row in result["results"])
    assert not loaded, f"Reference checks hydrated {len(loaded)} full knowledge rows"


@pytest.mark.parametrize(
    "scope,owner,person_only,allowed",
    [
        ("company", "colleague", False, True),
        ("department", "colleague", False, True),
        ("private", "actor", False, True),
        ("private", "actor", True, True),
        ("private", "colleague", False, False),
        ("private", "colleague", True, False),
    ],
)
def test_knowledge_references_preserve_scope_and_person_ownership(
    db, world, scope, owner, person_only, allowed
):
    employee = getattr(world, owner)
    item = KnowledgeItem(
        owner_employee_id=None if person_only else employee.id,
        owner_person_id=employee.person_id,
        department_id=employee.department_id,
        scope=scope,
        title="Knowledge reference visibility",
    )
    db.add(item)
    db.commit()
    request = _decision(world)
    request["decision"]["context"]["knowledge_refs"] = [item.id, item.id]
    if allowed:
        assert _send(world, request)["decision_status"] == "APPLIED"
    else:
        before = _counts()
        with pytest.raises(BridgeError) as error:
            _send(world, request)
        assert error.value.reason == "invalid_reference"
        assert _counts() == before


def test_scope_moving_to_another_company_mid_envelope_interrupts_missing_work(
    world, foreign, monkeypatch
):
    original = RuntimeExecution.execute_action
    request = _decision(world)
    request["decision"]["scope"] = f"task:{world.target.id}"

    def move_scope(self, index, *args, **kwargs):
        result = original(self, index, *args, **kwargs)
        if index == 0:
            with SessionLocal() as db:
                db.get(Task, world.target.id).project_id = foreign.project.id
                db.commit()
        return result

    monkeypatch.setattr(RuntimeExecution, "execute_action", move_scope)
    result = _send(world, request)
    assert result["status"] == "interrupted" and result["reason"] == "invalid_reference"
    assert result["action_indices"] == [0]


def test_operation_and_intent_binding_failure_rolls_back_both(world):
    before = _counts()
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TRIGGER reject_runtime_binding BEFORE UPDATE OF decision_id "
            "ON runtime_operations "
            "BEGIN SELECT RAISE(ABORT, 'binding storage unavailable'); END"
        )
    try:
        with pytest.raises(BridgeError) as error:
            _send(world, _decision(world))
        assert error.value.reason == "storage_failed"
        assert _counts() == before and world.broadcasts == []
    finally:
        with engine.begin() as connection:
            connection.exec_driver_sql("DROP TRIGGER reject_runtime_binding")


@pytest.mark.parametrize("failure_stage", ["audit_insert", "correlation_update"])
def test_receipt_storage_failure_rolls_back_effect_and_events_but_keeps_intent(
    world, failure_stage
):
    timing = "INSERT" if failure_stage == "audit_insert" else "UPDATE OF runtime_operation_id"
    with engine.begin() as connection:
        connection.exec_driver_sql(
            f"CREATE TRIGGER reject_runtime_receipt BEFORE {timing} ON tool_audits "
            "BEGIN SELECT RAISE(ABORT, 'receipt storage unavailable'); END"
        )
    try:
        with pytest.raises(BridgeError) as error:
            _send(world, _decision(world))
        assert error.value.reason == "storage_failed" and error.value.operation_id is not None
        with SessionLocal() as db:
            op = db.scalar(select(RuntimeOperation))
            assert op.decision_id is not None
            assert db.get(DecisionRecord, op.decision_id).status == "EXECUTING"
            assert db.scalar(select(func.count()).select_from(ToolAudit)) == 0
            assert db.scalar(select(Task).where(Task.title == "New work")) is None
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(Event)
                    .where(Event.project_id == world.project.id)
                )
                == 0
            )
        assert world.broadcasts == []
    finally:
        with engine.begin() as connection:
            connection.exec_driver_sql("DROP TRIGGER reject_runtime_receipt")
    assert _send(world, _decision(world))["decision_status"] == "APPLIED"


def test_parser_help_documents_closed_context_and_reference_encoding():
    contract = reply_contract()
    assert set(contract["decision"]["context_schema"]["properties"]) == {
        "project_id",
        "task_ids",
        "candidate_employee_ids",
        "requirement_codes",
        "deliverable_count",
        "open_task_count",
        "load_summary",
        "knowledge_refs",
        "artifact_refs",
        "note",
    }
    assert "authority" not in contract["decision"]["context_schema"]["properties"]
    assert any("project DriveNode" in note and "artifact_id" in note for note in contract["notes"])
    json.dumps(contract, allow_nan=False)
