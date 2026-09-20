"""U9/U12 capability facts and explicit actions through the actual DB/runtime bridge."""

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier

import pytest
from factories import make_employee
from sqlalchemy import func, select
from test_m3_management_runtime import session_for
from test_m3_management_runtime import world as world
from test_market_core import _forbidden_keys, _ready_character

from app.models.action_confirmation import ActionConfirmation
from app.models.base import utcnow
from app.models.competency import CompetencyDefinition, CompetencyEvidence, EmployeeCompetency
from app.models.decision import DecisionRecord, ToolAudit
from app.models.enums import AuthorityScopeKind
from app.models.event import Event
from app.models.knowledge import LearningPriority, Skill
from app.models.learning import LearningSession
from app.models.organization import Company, Employee
from app.models.position import PositionAuthorityGrant, PositionDefinition
from app.models.project import Project, Task
from app.models.runtime_operation import RuntimeOperation
from app.repositories import knowledge as knowledge_repo
from app.repositories import project as project_repo
from app.schemas.learning import LearningSessionOut
from app.schemas.market import MarketCandidateOut, MarketListingPageOut
from app.work import authority, decisions, readiness
from app.work import contracts as C
from app.work import production as P
from app.work import tool_executor as executor
from app.work.runtime_bridge import BridgeError, handle_reply

LEARNING_TOOLS = ("create_learning_priority", "request_training")


def capability_request(world, kind, *actions, key="capability-action"):
    return {
        "version": 1,
        "kind": "decision",
        "operation_key": key,
        "decision": {
            "decision_type": kind,
            "reason": "Apply the explicitly selected capability response",
            "scope": f"project:{world.project.id}",
            "actions": list(actions),
        },
    }


def test_runtime_catalog_exposes_the_seven_live_capability_actions():
    catalog = executor.catalog()
    actions = {action for tool in catalog for action in tool.get("capability_actions", [])}
    assert actions == {action.value for action in P.CapabilityGapAction}
    for tool in catalog:
        expected = [
            action.value
            for action, names in P.CAPABILITY_GAP_ACTION_TOOLS.items()
            if tool["name"] in names
        ]
        assert tool["capability_actions"] == expected
        assert tool["input_schema"] and tool["output_schema"]
    replan = next(tool for tool in catalog if tool["name"] == "replan_project")
    assert replan["capability_actions"] == ["revise_task"]


@pytest.mark.parametrize("status", ["done", "failed", "cancelled"])
def test_terminal_task_reassignment_preserves_history(db, world, status):
    task = project_repo.create_task(
        db,
        project_id=world.project.id,
        title="Closed work keeps its original assignee",
        status=status,
        assignee_id=world.worker.id,
    )
    work = session_for(db, world, "management")
    result = send(
        work,
        capability_request(
            world,
            "reassign_task",
            {"tool": "assign_task", "args": {"task_id": task.id, "employee_id": world.reviewer.id}},
        ),
    )
    assert result["decision_status"] == "FAILED", result
    assert result["results"][0]["reason"] == "domain_rejected"
    assert "terminal state" in result["results"][0]["error"]
    db.refresh(task)
    assert task.status == status and task.assignee_id == world.worker.id
    assert not db.scalar(
        select(Event).where(Event.type == "task.assigned", Event.task_id == task.id)
    )


@pytest.mark.parametrize(
    "status,previous", [("backlog", None), ("todo", "worker"), ("blocked", "worker")]
)
def test_internal_choice_and_reassignment_apply_only_the_selected_person(
    db, world, status, previous
):
    previous_id = getattr(world, previous).id if previous else None
    task = project_repo.create_task(
        db,
        project_id=world.project.id,
        title="Explicit selection",
        status=status,
        assignee_id=previous_id,
    )
    work = session_for(db, world, "management")
    payload = capability_request(
        world,
        "reassign_task" if previous else "assign_task",
        {"tool": "assign_task", "args": {"task_id": task.id, "employee_id": world.reviewer.id}},
    )
    result = send(work, payload)
    assert result["decision_status"] == "APPLIED", result
    fact = result["results"][0]
    assert fact["data"]["previous_assignee_id"] == previous_id
    assert fact["data"]["assignee"]["person_id"] == world.reviewer.person_id
    db.refresh(task)
    assert task.assignee_id == world.reviewer.id
    assert task.status == ("todo" if status == "backlog" else status)
    decision = db.get(DecisionRecord, result["decision_id"])
    assert decision.actor_person_id == world.manager.person_id
    event = db.scalar(select(Event).where(Event.type == "task.assigned", Event.task_id == task.id))
    assert event.payload["previous_assignee_id"] == previous_id
    assert send(work, payload) == result
    assert (
        db.scalar(
            select(func.count())
            .select_from(Event)
            .where(Event.type == "task.assigned", Event.task_id == task.id)
        )
        == 1
    )


def _domain_snapshot(db):
    db.expire_all()
    return (
        list(
            db.execute(select(Task.id, Task.assignee_id, Task.status, Task.title).order_by(Task.id))
        ),
        list(
            db.execute(
                select(Project.id, Project.status, Project.management_employee_id).order_by(
                    Project.id
                )
            )
        ),
        *(
            db.scalar(select(func.count()).select_from(model))
            for model in (Employee, LearningPriority, LearningSession, ActionConfirmation, Event)
        ),
    )


@pytest.mark.parametrize(
    "name",
    [
        "create_learning_priority",
        "request_training",
        "recruit_from_market",
        "assign_task",
        "update_task",
        "replan_project",
        "create_task",
        "create_dependency",
        "delegate_project",
    ],
)
@pytest.mark.parametrize("violation", ["missing_arguments", "revoked_authority"])
def test_each_capability_tool_rejects_invalid_input_and_missing_authority(
    db, world, name, violation
):
    cases = {
        "create_learning_priority": (
            "enroll_learning",
            dict(
                project_id=world.project.id,
                employee_id=world.worker.id,
                topic="Practice",
                reason="Explicit choice",
                score=80,
            ),
        ),
        "request_training": (
            "enroll_learning",
            dict(
                project_id=world.project.id,
                employee_id=world.worker.id,
                topic="Practice",
                reason="Explicit choice",
            ),
        ),
        "recruit_from_market": (
            "recruit",
            dict(project_id=world.project.id, listing_id=999, amount=1200),
        ),
        "assign_task": ("assign_task", dict(task_id=world.task.id, employee_id=world.worker.id)),
        "update_task": ("replan", dict(task_id=world.task.id, title="Revised")),
        "replan_project": ("replan", dict(project_id=world.project.id, reason="Revised scope")),
        "create_task": ("decompose_project", dict(project_id=world.project.id, title="New task")),
        "create_dependency": (
            "create_dependency",
            dict(task_id=world.task.id, depends_on_id=world.task.id),
        ),
        "delegate_project": (
            "delegate_management",
            dict(project_id=world.project.id, to_employee_id=world.reviewer.id),
        ),
    }
    kind, args = cases[name]
    if violation == "revoked_authority":
        actor = authority.resolve_actor_authority(db, world.manager.id)
        for grant in db.scalars(
            select(PositionAuthorityGrant).where(
                PositionAuthorityGrant.position_definition_id == actor.position_definition_id,
                PositionAuthorityGrant.authority_kind
                == executor.registry.get(name).required_authority.value,
                PositionAuthorityGrant.effective_to.is_(None),
            )
        ):
            grant.effective_to = utcnow()
        db.commit()
    else:
        args = {}
    work = session_for(db, world, "management")
    before = _domain_snapshot(db)
    payload = capability_request(world, kind, dict(tool=name, args=args))
    if violation == "missing_arguments":
        with pytest.raises(BridgeError) as error:
            send(work, payload)
        assert error.value.reason == "invalid_request"
        assert db.scalar(select(func.count()).select_from(DecisionRecord)) == 0
        assert db.scalar(select(func.count()).select_from(ToolAudit)) == 0
    else:
        result = send(work, payload)
        assert result["decision_status"] == "FAILED", result
        denied = result["results"][0]
        assert denied["reason"] == "not_authorized" and not denied["ok"]
        db.expire_all()
        assert db.get(ToolAudit, denied["audit_id"]).decision_id == result["decision_id"]
        assert send(work, payload) == result
    assert _domain_snapshot(db) == before


def test_gap_observation_never_selects_or_performs_an_action(db, world):
    work = session_for(db, world, "management")
    before = _domain_snapshot(db)
    result = read(work, "inspect_gap", {"task_id": world.task.id})
    assert result["ok"] and result["data"]["candidates"]
    assert _domain_snapshot(db) == before
    assert db.scalar(select(func.count()).select_from(DecisionRecord)) == 0


def test_split_nodes_and_edges_use_actual_receipt_ids(db, world):
    work = session_for(db, world, "management")
    nodes = capability_request(
        world,
        "decompose_project",
        *[
            dict(tool="create_task", args=dict(project_id=world.project.id, title=title))
            for title in ("Selected implementation", "Selected verification")
        ],
        key="split-nodes",
    )
    created = send(work, nodes)
    assert created["decision_status"] == "APPLIED"
    first, second = [r["data"]["task"]["task_id"] for r in created["results"]]
    assert first != second
    edges = capability_request(
        world,
        "create_dependency",
        dict(tool="create_dependency", args=dict(task_id=second, depends_on_id=first)),
        key="split-edge",
    )
    edges["decision"]["parent_decision_id"] = created["decision_id"]
    linked = send(work, edges)
    assert linked["decision_status"] == "APPLIED"
    assert linked["results"][0]["data"]["dependencies"] == [first]
    db.expire_all()
    assert (
        db.get(DecisionRecord, linked["decision_id"]).parent_decision_id == created["decision_id"]
    )
    assert send(work, nodes) == created and send(work, edges) == linked
    assert len(project_repo.list_tasks(db, world.project.id)) == 3
    assert len(project_repo.list_dependencies(db, world.project.id)) == 1


@pytest.mark.parametrize("crash_after_first_commit", [False, True])
def test_split_cycle_partial_result_replays_and_recovers_without_duplicate_nodes(
    db, world, crash_after_first_commit
):
    child = project_repo.create_task(
        db, project_id=world.project.id, title="Earlier split child", status="backlog"
    )
    project_repo.add_dependency(db, task_id=child.id, depends_on_id=world.task.id)
    work = session_for(db, world, "management")
    payload = capability_request(
        world,
        "decompose_project",
        dict(
            tool="create_task",
            args=dict(project_id=world.project.id, title="New split child", depends_on=[child.id]),
        ),
        dict(tool="create_dependency", args=dict(task_id=world.task.id, depends_on_id=child.id)),
    )
    first_audit = None
    if crash_after_first_commit:
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
            [sys.executable, "-c", program, str(work.id), json.dumps(payload)],
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert crashed.returncode == 23, crashed.stderr
        db.expire_all()
        first_audit = db.scalar(select(ToolAudit.id))
        assert db.scalar(select(DecisionRecord.status)) == "EXECUTING"
        assert len(project_repo.list_tasks(db, world.project.id)) == 3
        db.commit()
        recovery = subprocess.run(
            [
                sys.executable,
                "-c",
                "import json,sys; from app.work.runtime_bridge import handle_reply; "
                "print(json.dumps(handle_reply(int(sys.argv[1]), sys.argv[2])))",
                str(work.id),
                json.dumps(payload),
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
        )
        result = json.loads(recovery.stdout)
    else:
        result = send(work, payload)
    assert result["decision_status"] == "PARTIALLY_APPLIED" and result["action_indices"] == [0, 1]
    applied, denied = result["results"]
    assert applied["ok"] and denied["reason"] == "domain_rejected"
    assert "cycles=" in denied["error"]
    if first_audit is not None:
        assert applied["audit_id"] == first_audit
    db.expire_all()
    assert decisions.action_summary(db, result["decision_id"])["applied"] == 1
    assert len(project_repo.list_tasks(db, world.project.id)) == 3
    assert len(project_repo.list_dependencies(db, world.project.id)) == 2
    before_events = list(db.scalars(select(Event.id).order_by(Event.id)))
    assert send(work, payload) == result
    db.expire_all()
    assert len(project_repo.list_tasks(db, world.project.id)) == 3
    assert db.scalar(select(func.count()).select_from(ToolAudit)) == 2
    assert list(db.scalars(select(Event.id).order_by(Event.id))) == before_events


def test_revision_and_delegation_preserve_original_tasks_and_decision_actor(db, world):
    work = session_for(db, world, "management")
    revised = send(
        work,
        capability_request(
            world,
            "replan",
            dict(
                tool="update_task",
                args=dict(
                    task_id=world.task.id,
                    description="Explicit new approach",
                    acceptance_criteria="Selected acceptance",
                ),
            ),
            dict(
                tool="replan_project",
                args=dict(project_id=world.project.id, reason="Manager chose another approach"),
            ),
            key="revise",
        ),
    )
    assert revised["decision_status"] == "APPLIED"
    db.expire_all()
    assert world.project.status == "planning" and world.task.description == "Explicit new approach"
    assert world.task.rework_count == 0 and world.request.status == "open"
    assert len(project_repo.list_tasks(db, world.project.id)) == 1
    delegated = send(
        work,
        capability_request(
            world,
            "delegate_management",
            dict(
                tool="delegate_project",
                args=dict(project_id=world.project.id, to_employee_id=world.reviewer.id),
            ),
            key="delegate",
        ),
    )
    assert delegated["decision_status"] == "APPLIED", delegated
    db.expire_all()
    assert world.project.management_employee_id == world.reviewer.id
    assert world.project.management_person_id == world.reviewer.person_id
    assert (
        db.get(DecisionRecord, delegated["decision_id"]).actor_person_id == world.manager.person_id
    )
    assert (
        db.get(ToolAudit, delegated["results"][0]["audit_id"]).actor_employee_id == world.manager.id
    )
    assert world.task.assignee_id == world.worker.id and world.task.rework_count == 0
    assert world.request.status == "open"


def read(work, name, args, *, key="read"):
    return handle_reply(
        work.id,
        json.dumps(
            {"version": 1, "kind": "read", "operation_key": key, "tool": name, "args": args}
        ),
    )["results"][0]


def request(world, name="request_training", *, key="learn", **overrides):
    args = {
        "project_id": world.project.id,
        "employee_id": world.worker.id,
        "topic": "Database transaction practice",
        "reason": "The project needs explicit rollback evidence",
    }
    args.update({"score": 75} if name == "create_learning_priority" else {"minutes": 5})
    args.update(overrides)
    return {
        "version": 1,
        "kind": "decision",
        "operation_key": key,
        "decision": {
            "decision_type": "enroll_learning",
            "reason": "Develop the selected person's capability",
            "scope": f"project:{world.project.id}",
            "actions": [{"tool": name, "args": args}],
        },
    }


def send(work, payload):
    return handle_reply(work.id, json.dumps(payload))


def counts(db):
    return (
        list(
            db.execute(
                select(
                    EmployeeCompetency.id,
                    EmployeeCompetency.person_id,
                    EmployeeCompetency.score,
                    EmployeeCompetency.confidence,
                    EmployeeCompetency.evidence_count,
                ).order_by(EmployeeCompetency.id)
            )
        ),
        *(
            db.scalar(select(func.count()).select_from(model))
            for model in (CompetencyEvidence, Skill)
        ),
    )


def prime_competency(db, world):
    definition = db.scalar(
        select(CompetencyDefinition).where(CompetencyDefinition.code == "execution")
    )
    db.add(
        EmployeeCompetency(
            person_id=world.worker.person_id,
            employee_id=None,
            competency_definition_id=definition.id,
            score=54,
            confidence=0.63,
            evidence_count=2,
        )
    )
    db.commit()


def test_gap_is_shared_with_api_preserves_unknown_person_facts_and_readiness(
    client, db, world, monkeypatch
):
    # Only the observation clock is fixed; both calls use real DB queries and readiness.
    observed_at = utcnow()
    monkeypatch.setattr(readiness, "utcnow", lambda: observed_at)
    world.task.kind = "development"
    definition = db.scalar(
        select(CompetencyDefinition).where(CompetencyDefinition.code == "execution")
    )
    # Person-owned capability acquired before employment has no employee mirror.
    db.add(
        EmployeeCompetency(
            person_id=world.worker.person_id,
            employee_id=None,
            competency_definition_id=definition.id,
            score=68,
            confidence=0.7,
        )
    )
    db.commit()
    work = session_for(db, world, "management")
    args = {"task_id": world.task.id, "candidate_employee_ids": [world.worker.id]}
    tool = read(work, "inspect_gap", args)
    assert tool["ok"], tool
    response = client.get(
        f"/api/v1/tasks/{world.task.id}/gap",
        params={"candidate_employee_ids": world.worker.id},
    )
    assert response.status_code == 200, response.text
    assert tool["data"] == response.json()
    row = tool["data"]["candidates"][0]
    assert row["person_id"] == world.worker.person_id
    assert row["requirements_known"] == 1
    assert row["known_weighted_score"] == 68
    assert any(item["score"] is None and not item["known"] for item in row["competencies"])
    assert row["load"]["open_task_count"] == 1
    assert row["readiness"]["employee_id"] == world.worker.id
    assert not (set(row) & {"rank", "recommended", "best", "action"})
    fit = read(work, "calculate_task_fit", args, key="fit")
    assert all(row[key] == value for key, value in fit["data"]["candidates"][0].items())


def test_gap_rejects_foreign_task_and_candidate_without_disclosing_facts(client, db, world):
    other = Company(name="Foreign", slug="u9-foreign")
    db.add(other)
    db.flush()
    outsider = make_employee(db, company_id=other.id, slug="u9-outsider")
    project = project_repo.create_project(db, company_id=other.id, name="Private")
    task = project_repo.create_task(db, project_id=project.id, title="Private task")
    db.commit()
    work = session_for(db, world, "management")
    for index, args in enumerate(
        [
            {"task_id": task.id},
            {"task_id": world.task.id, "candidate_employee_ids": [outsider.id]},
        ]
    ):
        result = read(work, "inspect_gap", args, key=f"foreign-{index}")
        assert not result["ok"] and result["reason"] == "domain_rejected"
        assert result["data"] == {}
        params = {"candidate_employee_ids": args["candidate_employee_ids"]} if index else {}
        assert client.get(f"/api/v1/tasks/{args['task_id']}/gap", params=params).status_code == 404


def test_gap_all_company_candidates_is_not_limited_as_explicit_user_ids(db, world):
    for index in range(201):
        make_employee(db, company_id=world.project.company_id, slug=f"u9-large-roster-{index}")
    db.commit()
    expected = db.scalar(
        select(func.count())
        .select_from(Employee)
        .where(Employee.company_id == world.project.company_id)
    )
    work = session_for(db, world, "management")
    result = read(work, "inspect_gap", {"task_id": world.task.id})
    assert result["ok"], result
    assert len(result["data"]["candidates"]) == expected


def test_market_tools_match_public_api_and_do_not_select_candidates(client, db, world):
    candidate = _ready_character(client, name="U9 candidate")
    listing = client.post(
        "/api/v1/market/listings", json={"person_id": candidate["person_id"]}
    ).json()
    position = db.get(
        PositionDefinition,
        authority.resolve_actor_authority(db, world.worker.id).position_definition_id,
    )
    work = session_for(db, world, "management")
    args = {"text": "U9 candidate", "position_definition_id": position.id}
    result = read(work, "search_market", args)
    assert result["ok"], result
    api = client.get("/api/v1/market/listings", params=args)
    assert api.status_code == 200
    assert MarketListingPageOut.model_validate(result["data"]).model_dump(mode="json") == api.json()
    assert result["data"]["items"] and result["data"]["items"][0]["fit"]["known_fit_score"] is None
    defaults = read(work, "search_market", {**args, "limit": None, "offset": None}, key="null-page")
    assert defaults["ok"] and defaults["data"] == result["data"]
    profile = read(
        work, "inspect_candidate", {"listing_id": listing["listing_id"]}, key="candidate"
    )
    assert profile["ok"], profile
    assert (
        MarketCandidateOut.model_validate(profile["data"]).model_dump(mode="json")
        == client.get(f"/api/v1/market/listings/{listing['listing_id']}").json()
    )
    assert _forbidden_keys(profile["data"]) == []
    defaults = read(
        work,
        "inspect_candidate",
        {"listing_id": listing["listing_id"], "timeline_limit": None, "evidence_limit": None},
        key="null-limits",
    )
    assert defaults["ok"] and defaults["data"] == profile["data"]
    assert not (set(result["data"]) & {"selected", "recommended", "best"})
    client.delete(f"/api/v1/market/listings/{listing['listing_id']}")
    closed = read(work, "inspect_candidate", {"listing_id": listing["listing_id"]}, key="closed")
    assert not closed["ok"] and closed["error"] == "listing_not_found"


def test_market_rejects_foreign_position_and_bounds(client, db, world):
    other = Company(name="Other market", slug="u9-market-other")
    db.add(other)
    db.flush()
    position = PositionDefinition(company_id=other.id, code="private", name="Private")
    db.add(position)
    db.commit()
    work = session_for(db, world, "management")
    args = {"position_definition_id": position.id}
    result = read(work, "search_market", args)
    assert not result["ok"] and result["reason"] == "domain_rejected"
    assert client.get("/api/v1/market/listings", params=args).status_code == 404
    too_many = read(work, "search_market", {"limit": 201}, key="large")
    assert not too_many["ok"] and too_many["reason"] == "domain_rejected"


@pytest.mark.parametrize("name", LEARNING_TOOLS)
def test_learning_writes_require_decisions_and_catalog_has_schemas(db, world, name):
    spec = executor.registry.get(name)
    assert spec.decision_semantics.value == "required"
    assert spec.required_authority == C.AuthorityKind.assign_task
    assert spec.input_schema["required"] and spec.output_schema["properties"]
    payload = request(world, name)
    result = executor.execute_tool(
        db,
        name=name,
        args=payload["decision"]["actions"][0]["args"],
        context=executor.context_for_employee(db, world.manager, project_id=world.project.id),
    )
    assert not result.ok and result.reason == "decision_required"


def test_training_is_planned_audited_replayed_and_never_spends_or_teaches(client, db, world):
    prime_competency(db, world)
    before = counts(db)
    company_settings = deepcopy(db.get(Company, world.manager.company_id).settings)
    work = session_for(db, world, "management")
    payload = request(world)
    receipt = send(work, payload)
    assert receipt["decision_status"] == "APPLIED", receipt
    result = receipt["results"][0]
    planned = result["data"]["learning_session"]
    assert result["data"]["created"] is True and planned["status"] == "planned"
    assert planned["tokens_used"] == 0 and planned["cost_used"] == 0 and planned["outputs"] == {}
    assert send(work, payload) == receipt
    repeated = send(work, request(world, key="same-training-new-operation"))
    assert repeated["results"][0]["data"]["created"] is False
    assert repeated["results"][0]["data"]["learning_session"]["id"] == planned["id"]
    db.expire_all()
    session = db.get(LearningSession, planned["id"])
    assert session.person_id == world.worker.person_id and session.source_id == world.project.id
    assert counts(db) == before
    assert db.get(Company, world.manager.company_id).settings == company_settings
    assert db.scalar(select(func.count()).select_from(LearningSession)) == 1
    operation = db.get(RuntimeOperation, receipt["operation_id"])
    decision = db.get(DecisionRecord, receipt["decision_id"])
    audit = db.get(ToolAudit, result["audit_id"])
    assert decision.decision_type == "enroll_learning" and operation.decision_id == decision.id
    assert (
        audit.runtime_operation_id == operation.id
        and audit.actor_person_id == world.manager.person_id
    )
    assert (
        LearningSessionOut.model_validate(planned).model_dump(mode="json")
        == client.get(f"/api/v1/learning-sessions/{session.id}").json()
    )
    session.status, session.error = "failed", "Execution failed before producing evidence"
    db.commit()
    observed = client.get(f"/api/v1/learning-sessions/{session.id}").json()
    assert observed["status"] == "failed" and observed["outputs"] == {}
    assert counts(db) == before
    altered = deepcopy(payload)
    altered["decision"]["actions"][0]["args"]["topic"] = "Another topic"
    with pytest.raises(BridgeError, match="operation_conflict"):
        send(work, altered)


def test_concurrent_distinct_operations_reuse_one_active_training(db, world):
    work = session_for(db, world, "management")
    requests = [request(world, key=f"concurrent-{index}") for index in range(2)]
    barrier = Barrier(2)

    def invoke(payload):
        barrier.wait()
        return send(work, payload)

    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(pool.map(invoke, requests))
    assert all(receipt["decision_status"] == "APPLIED" for receipt in receipts), receipts
    rows = [receipt["results"][0]["data"] for receipt in receipts]
    assert len({row["learning_session"]["id"] for row in rows}) == 1
    assert sorted(row["created"] for row in rows) == [False, True]


@pytest.mark.parametrize("status", ["running", "waiting_budget", "paused"])
def test_unfinished_training_reuses_its_current_state(db, world, status):
    prime_competency(db, world)
    before = counts(db)
    work = session_for(db, world, "management")
    first = send(work, request(world))
    assert first["decision_status"] == "APPLIED", first
    identifier = first["results"][0]["data"]["learning_session"]["id"]
    db.get(LearningSession, identifier).status = status
    db.commit()
    receipt = send(work, request(world, key="repeat-unfinished"))
    data = receipt["results"][0]["data"]
    assert data["created"] is False
    assert data["learning_session"]["id"] == identifier
    assert data["learning_session"]["status"] == status
    assert db.scalar(select(func.count()).select_from(LearningSession)) == 1
    assert counts(db) == before


@pytest.mark.parametrize("source", ["", "failure", "behavior-extension", "agent_request"])
def test_learning_priority_preserves_existing_person_owned_intent(db, world, source):
    prior = knowledge_repo.create_learning_priority(
        db,
        person_id=world.worker.person_id,
        topic="Database transaction practice",
        score=90,
        reason="Original intent",
        source=source,
    )
    db.commit()
    work = session_for(db, world, "management")
    receipt = send(work, request(world, "create_learning_priority"))
    assert receipt["decision_status"] == "APPLIED", receipt
    result = receipt["results"][0]["data"]
    assert result["created"] is False and result["learning_priority"]["id"] == prior.id
    db.refresh(prior)
    assert (prior.score, prior.reason, prior.source, prior.employee_id) == (
        90,
        "Original intent",
        source,
        None,
    )
    assert db.scalar(select(func.count()).select_from(LearningPriority)) == 1


def test_new_priority_has_person_ownership_and_replay_has_no_extra_effect(db, world):
    prime_competency(db, world)
    work = session_for(db, world, "management")
    payload = request(world, "create_learning_priority")
    before = counts(db)
    receipt = send(work, payload)
    assert receipt["decision_status"] == "APPLIED", receipt
    assert send(work, payload) == receipt
    row = db.get(LearningPriority, receipt["results"][0]["data"]["learning_priority"]["id"])
    assert row.person_id == world.worker.person_id and row.employee_id == world.worker.id
    assert counts(db) == before


@pytest.mark.parametrize("name", LEARNING_TOOLS)
@pytest.mark.parametrize("target", ["review", "other_project", "foreign_employee", "inactive"])
def test_learning_rejects_wrong_purpose_project_or_employee(db, world, name, target):
    args = {}
    if target == "other_project":
        other = project_repo.create_project(db, company_id=world.project.company_id, name="Other")
        args["project_id"] = other.id
    elif target == "foreign_employee":
        other = Company(name="Other learning", slug="u9-learning-other")
        db.add(other)
        db.flush()
        args["employee_id"] = make_employee(
            db, company_id=other.id, slug="u9-learning-target", lifecycle_status="active"
        ).id
    elif target == "inactive":
        world.worker.lifecycle_status = "suspended"
    db.commit()
    work = session_for(db, world, "review" if target == "review" else "management")
    receipt = send(work, request(world, name, **args))
    assert receipt["decision_status"] == "FAILED", receipt
    assert not receipt["results"][0]["ok"]
    assert db.scalar(select(func.count()).select_from(LearningPriority)) == 0
    assert db.scalar(select(func.count()).select_from(LearningSession)) == 0


@pytest.mark.parametrize("name", LEARNING_TOOLS)
def test_learning_honors_department_authority_for_actual_target(db, world, name):
    actor = authority.resolve_actor_authority(db, world.manager.id)
    for row in db.scalars(
        select(PositionAuthorityGrant).where(
            PositionAuthorityGrant.position_definition_id == actor.position_definition_id,
            PositionAuthorityGrant.authority_kind == C.AuthorityKind.assign_task.value,
        )
    ):
        row.effective_to = utcnow()
    db.commit()
    assert world.worker.department_id != world.reviewer.department_id
    authority.grant_authority(
        db,
        position_definition_id=actor.position_definition_id,
        kind=C.AuthorityKind.assign_task,
        scope_kind=AuthorityScopeKind.department,
        scope_ref=world.worker.department_id,
    )
    work = session_for(db, world, "management")
    allowed = send(work, request(world, name))
    assert allowed["decision_status"] == "APPLIED", allowed
    denied = send(work, request(world, name, key="wrong-department", employee_id=world.reviewer.id))
    assert denied["results"][0]["reason"] == "not_authorized"


@pytest.mark.parametrize("name", LEARNING_TOOLS)
def test_learning_effect_rolls_back_when_audit_insert_fails(db, world, name):
    work = session_for(db, world, "management")
    with db.get_bind().begin() as connection:
        connection.exec_driver_sql(
            "CREATE TRIGGER u9_reject_audit BEFORE INSERT ON tool_audits "
            "BEGIN SELECT RAISE(ABORT, 'u9 audit unavailable'); END"
        )
    try:
        with pytest.raises(BridgeError) as error:
            send(work, request(world, name))
        assert error.value.reason == "storage_failed"
        db.expire_all()
        assert db.scalar(select(func.count()).select_from(LearningPriority)) == 0
        assert db.scalar(select(func.count()).select_from(LearningSession)) == 0
        assert db.scalar(select(func.count()).select_from(ToolAudit)) == 0
    finally:
        with db.get_bind().begin() as connection:
            connection.exec_driver_sql("DROP TRIGGER u9_reject_audit")
    receipt = send(work, request(world, name))
    assert receipt["decision_status"] == "APPLIED", receipt


@pytest.mark.parametrize(
    "name,change",
    [
        ("create_learning_priority", {"score": 101}),
        ("create_learning_priority", {"topic": " "}),
        ("request_training", {"minutes": 121}),
        ("request_training", {"reason": " "}),
    ],
)
def test_learning_rejects_unbounded_or_empty_intent(db, world, name, change):
    work = session_for(db, world, "management")
    receipt = send(work, request(world, name, **change))
    assert receipt["decision_status"] == "FAILED", receipt
    assert receipt["results"][0]["reason"] == "domain_rejected"


def test_training_rejects_conflicting_active_intent_and_other_decision_kind(db, world):
    work = session_for(db, world, "management")
    assert send(work, request(world))["decision_status"] == "APPLIED"
    conflict = send(work, request(world, key="conflict", minutes=6))
    assert conflict["decision_status"] == "FAILED"
    wrong_kind = request(world, key="wrong-kind", topic="Another plan")
    wrong_kind["decision"]["decision_type"] = "decompose_project"
    rejected = send(work, wrong_kind)
    assert rejected["decision_status"] == "FAILED"
    assert rejected["results"][0]["reason"] == "invalid_learning_decision"


@pytest.mark.parametrize("actor", ["manager", "worker"])
def test_unbound_or_other_actors_decision_cannot_authorize_learning(db, world, actor):
    work = session_for(db, world, "management")
    decision = decisions.open_decision(
        db,
        executor.context_for_employee(db, getattr(world, actor), project_id=world.project.id),
        decision_type=C.DecisionKind.enroll_learning,
        reason="A record alone is not runtime execution authority",
        scope=f"project:{world.project.id}",
    )
    result = executor.execute_tool(
        db,
        name="request_training",
        args=request(world)["decision"]["actions"][0]["args"],
        context=executor.context_for_work_session(db, work.id),
        decision_id=decision.id,
    )
    assert not result.ok and result.reason == "invalid_learning_decision"
    assert db.scalar(select(func.count()).select_from(LearningSession)) == 0
