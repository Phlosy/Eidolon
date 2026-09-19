"""U8 purpose/scope integration; scripted external I/O is not real-runtime evidence."""

import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, update

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.base import utcnow
from app.models.decision import DecisionRecord, ToolAudit
from app.models.enums import AutonomyLevel
from app.models.organization import Employee
from app.models.person import Person
from app.models.project import Project, Task, WorkSession
from app.models.provider import Provider
from app.models.review import ReviewRequest
from app.models.runtime_operation import RuntimeOperation
from app.providers.secrets.store import get_secret_store
from app.repositories import position as position_repo
from app.repositories import project as project_repo
from app.repositories import runtimes as runtime_repo
from app.runtimes import probe
from app.runtimes.base import RuntimeReply, RuntimeSession
from app.runtimes.container_common import resolve_container_instance
from app.services import artifacts, tasks
from app.work import authority, reviews
from app.work import contracts as C
from app.work import tool_executor as executor
from app.work.runtime_bridge import BridgeError, handle_reply
from app.work.runtime_conversation import run_conversation


@pytest.fixture()
def world(db, default_company_id, monkeypatch):
    monkeypatch.setattr(settings, "allow_planning_fixtures", False)
    manager = db.scalar(select(Employee).where(Employee.slug == "alice"))
    reviewer = db.scalar(select(Employee).where(Employee.slug == "dana"))
    worker = db.scalar(select(Employee).where(Employee.slug == "charlie"))
    # Explicit policy grants make out-of-scope rejections distinguishable from
    # accidental no-authority failures. These are real time-windowed grants.
    position = authority.resolve_actor_authority(db, reviewer.id)
    for kind in (C.AuthorityKind.accept_delivery, C.AuthorityKind.plan_project_work):
        authority.grant_authority(
            db, position_definition_id=position.position_definition_id, kind=kind
        )
    project = project_repo.create_project(
        db,
        company_id=default_company_id,
        name="Management runtime target",
        status="in_progress",
        work_mode="managed",
        planning_fixture="none",
        management_employee_id=manager.id,
        management_person_id=manager.person_id,
    )
    task = project_repo.create_task(
        db,
        project_id=project.id,
        title="Worker output awaiting review",
        status="in_review",
        assignee_id=worker.id,
    )
    request = reviews.open_review_request(
        db, task=task, requester_employee_id=manager.id, reviewer_employee_id=reviewer.id
    )
    return SimpleNamespace(
        manager=manager,
        reviewer=reviewer,
        worker=worker,
        project=project,
        task=task,
        request=request,
    )


def session_for(db, world, purpose, *, bound=False):
    actor = world.reviewer if purpose == "review" else world.manager
    fields = {"purpose": purpose, "project_id": world.project.id}
    if purpose == "review":
        fields["review_request_id"] = world.request.id
    if bound:
        provider = Provider(
            company_id=actor.company_id,
            name="Isolated test provider",
            provider_type="custom",
            base_url="https://provider.invalid",
        )
        db.add(provider)
        db.flush()
        row = runtime_repo.get_instance_for_employee(db, actor.id)
        if row is None:
            row = runtime_repo.create_instance(db, employee_id=actor.id)
        row.runtime_type, row.deployment_mode, row.status = "hermes", "docker", "running"
        row.container_name, row.resource_key = "scope-test-runtime", "scope-test-resource"
        row.runtime_version, row.image_digest = "scope-test-version", "sha256:scope-test"
        row.internal_host, row.internal_port = "control.invalid", 8765
        row.metadata_json = {
            "api_key_ref": get_secret_store().store(db, "scope-test-control-token"),
            "device_key_ref": get_secret_store().store(db, "scope-test-device-key"),
            "applied_model_fingerprint": probe.model_fingerprint(provider, "scope-test-model"),
        }
        db.flush()
        fields.update(runtime_instance_id=row.id, provider_id=provider.id)
    work = project_repo.create_work_session(
        db,
        employee_id=actor.id,
        runtime_type="hermes",
        model="scope-test-model",
        started_at=utcnow(),
        **fields,
    )
    db.commit()
    return work


def decision(world, *, key="decision", actions=None):
    return {
        "version": 1,
        "kind": "decision",
        "operation_key": key,
        "decision": {
            "decision_type": "accept_delivery",
            "reason": "Apply the chosen action",
            "scope": f"project:{world.project.id}",
            "actions": actions
            or [
                {
                    "tool": "submit_review_verdict",
                    "args": {
                        "review_request_id": world.request.id,
                        "verdict": "PASS",
                        "notes": "Inspected",
                    },
                }
            ],
        },
    }


def read(world, key="read"):
    return {
        "version": 1,
        "kind": "read",
        "operation_key": key,
        "tool": "inspect_project",
        "args": {"project_id": world.project.id},
    }


def finish():
    return {"version": 1, "kind": "finish", "operation_key": "finish", "summary": "Acknowledged"}


def send(work, request):
    return handle_reply(work.id, json.dumps(request))


def test_management_can_read_and_apply_a_real_decision_without_a_worker_task(db, world):
    world.project.status = "waiting_for_management"
    db.commit()
    work = session_for(db, world, "management")
    ctx = executor.context_for_work_session(db, work.id)
    assert ctx.task_id is None and ctx.project_id == world.project.id
    assert ctx.as_dict()["purpose"] == "management"
    assert send(work, read(world))["results"][0]["ok"]
    request = decision(
        world,
        actions=[
            {
                "tool": "create_task",
                "args": {"project_id": world.project.id, "title": "Manager selected work"},
            }
        ],
    )
    receipt = send(work, request)
    assert receipt["decision_status"] == "APPLIED"
    assert send(work, request) == receipt
    assert receipt["results"][0]["actor"] == ctx.as_dict()
    db.expire_all()
    assert work.task_id is None and work.status == "running"
    # Creating the real task uses the existing domain's project progression.
    assert world.project.status == "in_progress"
    assert db.scalar(select(func.count()).select_from(Task)) == 2


@pytest.mark.parametrize(
    "verdict,task_status",
    [("PASS", "done"), ("REWORK", "todo"), ("REJECT", "rejected"), ("ESCALATE", "in_review")],
)
def test_own_verdict_allows_actual_receipt_replay_read_and_finish(db, world, verdict, task_status):
    work = session_for(db, world, "review")
    ctx = executor.context_for_work_session(db, work.id)
    assert ctx.task_id is None
    assert ctx.as_dict()["review_request_id"] == world.request.id
    request = decision(world)
    request["decision"]["actions"][0]["args"]["verdict"] = verdict
    result = send(work, request)
    assert result["decision_status"] == "APPLIED"
    assert result["results"][0]["actor"] == ctx.as_dict()
    assert send(work, request) == result
    assert send(work, read(world))["results"][0]["ok"]
    assert send(work, finish())["status"] == "finished"
    db.expire_all()
    assert world.request.status == "decided" and world.request.verdict == verdict
    assert world.task.status == task_status
    assert work.task_id is None and work.status == "running" and work.ended_at is None


def test_review_rejects_unrelated_write_with_durable_actual_receipt(db, world):
    work = session_for(db, world, "review")
    request = decision(
        world,
        actions=[
            {
                "tool": "create_task",
                "args": {"project_id": world.project.id, "title": "Outside reviewer scope"},
            }
        ],
    )
    result = send(work, request)
    receipt = result["results"][0]
    assert not receipt["ok"] and receipt["reason"] == "work_session_write_forbidden"
    assert db.get(ToolAudit, receipt["audit_id"]).outcome == receipt["reason"]
    assert send(work, request) == result
    assert db.scalar(select(func.count()).select_from(Task)) == 1
    assert db.get(ReviewRequest, world.request.id).status == "open"


class ScriptedAdapter:
    type = "hermes"
    supports_controlled_turn = True

    def __init__(self, *replies):
        self.replies, self.prompts = iter(replies), []

    async def complete_turn(self, session, prompt, *, request_id, timeout):
        self.prompts.append(json.loads(prompt))
        session.remote_run_id = f"scope-test-run-{len(self.prompts)}"
        reply = next(self.replies)
        if callable(reply):
            reply = await reply(session, self.prompts[-1])
        return RuntimeReply(
            session.id,
            request_id,
            session.remote_run_id,
            reply if isinstance(reply, str) else json.dumps(reply),
        )


async def admitted(work_session_id, request_id):
    return True


async def run(work, adapter, **kwargs):
    runtime = RuntimeSession(
        id=f"scope-test-{work.id}",
        task_id=None,
        instance=resolve_container_instance(
            work.employee_id, "hermes", session_factory=SessionLocal
        ),
    )
    return await run_conversation(
        work.id,
        adapter=adapter,
        runtime_session=runtime,
        goal="Use facts, apply a decision, acknowledge actual receipts.",
        budget_check=kwargs.pop("budget_check", admitted),
        session_factory=SessionLocal,
        **kwargs,
    )


async def test_review_conversation_delivers_own_verdict_before_finish(db, world):
    work = session_for(db, world, "review", bound=True)
    adapter = ScriptedAdapter(decision(world), finish())
    result = await run(work, adapter)
    assert result["status"] == "finished", result
    assert len(adapter.prompts) == 2
    receipt = result["turns"][0]["result"]
    assert receipt["decision_status"] == "APPLIED"
    assert adapter.prompts[1]["feedback"]["result"] == receipt
    assert adapter.prompts[0]["scope"]["task_id"] is None
    assert adapter.prompts[0]["scope"]["purpose"] == "review"
    assert adapter.prompts[0]["scope"]["review_request_id"] == world.request.id
    db.expire_all()
    assert world.task.status == "done" and world.request.verdict == "PASS"
    assert work.status == "running" and work.ended_at is None
    assert work.task_id is None and world.project.status == "in_progress"


@pytest.mark.parametrize("purpose", ["management", "review"])
@pytest.mark.parametrize(
    "change,reason",
    [
        ("person", "work_session_identity_mismatch"),
        ("company", "work_session_identity_mismatch"),
        ("suspended", "employee_not_active"),
        ("offboarded", "employee_not_active"),
        ("project_company", "work_session_project_invalid"),
        ("cancelled", "work_session_project_invalid"),
        ("rejected", "work_session_project_invalid"),
        ("designation", None),
    ],
)
def test_context_refreshes_cached_actor_and_target(db, world, purpose, change, reason):
    work = session_for(db, world, purpose)
    actor = world.reviewer if purpose == "review" else world.manager
    executor.context_for_work_session(db, work.id)
    if change in {"person", "company"}:
        row, values = actor, {f"{change}_id": 999999}
    elif change in {"suspended", "offboarded"}:
        row, values = actor, {"lifecycle_status": change}
    elif change == "project_company":
        row, values = world.project, {"company_id": 999999}
    elif change == "designation":
        row = world.request if purpose == "review" else world.project
        field = "reviewer_employee_id" if purpose == "review" else "management_employee_id"
        values = {field: world.worker.id}
        reason = (
            "work_session_review_invalid" if purpose == "review" else "work_session_manager_invalid"
        )
    else:
        row, values = world.project, {"status": change}
    with SessionLocal() as concurrent:
        concurrent.execute(update(type(row)).where(type(row).id == row.id).values(**values))
        concurrent.commit()
    assert any(getattr(row, key) != value for key, value in values.items())
    with pytest.raises(executor.ToolExecutionError) as error:
        executor.context_for_work_session(db, work.id)
    assert error.value.reason == reason


@pytest.mark.parametrize("purpose", ["management", "review"])
@pytest.mark.parametrize("fact", ["purpose", "employee", "person", "company", "project", "missing"])
def test_nonworker_history_is_never_rebuilt_or_rebound(db, world, purpose, fact):
    work = session_for(db, world, purpose)
    original = deepcopy(work.provenance_snapshot_json)
    snapshot = deepcopy(original)
    if fact == "missing":
        snapshot = None
    elif fact == "purpose":
        snapshot.pop("purpose")
    else:
        snapshot[fact]["id"] += 9999
    historical = WorkSession(
        purpose=purpose,
        task_id=None,
        project_id=work.project_id,
        review_request_id=work.review_request_id,
        employee_id=work.employee_id,
        company_id=work.company_id,
        person_id=work.person_id,
        runtime_type="hermes",
        provenance_snapshot_json=snapshot,
    )
    db.add(historical)
    db.commit()
    with pytest.raises(executor.ToolExecutionError):
        executor.context_for_work_session(db, historical.id)
    assert historical.provenance_snapshot_json == snapshot
    assert work.provenance_snapshot_json == original


@pytest.mark.parametrize(
    "change", ["task", "project", "reviewer", "requester", "requested_at", "snapshot_task"]
)
def test_review_requires_its_original_target_facts(db, world, change):
    work = session_for(db, world, "review")
    snapshot = deepcopy(work.provenance_snapshot_json)
    if change == "snapshot_task":
        snapshot["task"]["id"] += 1000
    else:
        field = {
            "task": "task_id",
            "project": "project_id",
            "reviewer": "reviewer_employee_id",
            "requester": "requested_by_employee_id",
            "requested_at": "requested_at",
        }[change]
        snapshot["review_request"][field] = "unknown-time" if change == "requested_at" else 9999
    historical = WorkSession(
        purpose="review",
        task_id=None,
        project_id=work.project_id,
        review_request_id=work.review_request_id,
        employee_id=work.employee_id,
        company_id=work.company_id,
        person_id=work.person_id,
        runtime_type="hermes",
        provenance_snapshot_json=snapshot,
    )
    db.add(historical)
    db.commit()
    with pytest.raises(executor.ToolExecutionError):
        executor.context_for_work_session(db, historical.id)


@pytest.mark.parametrize(
    "change", ["task_status", "task_project", "request_company", "newer_request"]
)
def test_open_review_rechecks_fresh_task_company_and_latest_request(db, world, change):
    work = session_for(db, world, "review")
    executor.context_for_work_session(db, work.id)
    with SessionLocal() as concurrent:
        if change == "newer_request":
            concurrent.add(
                ReviewRequest(
                    company_id=work.company_id,
                    project_id=world.project.id,
                    task_id=world.task.id,
                    reviewer_employee_id=world.reviewer.id,
                    requested_by_employee_id=world.manager.id,
                    requested_at=utcnow(),
                )
            )
        elif change == "request_company":
            concurrent.execute(
                update(ReviewRequest)
                .where(ReviewRequest.id == world.request.id)
                .values(company_id=99999)
            )
        else:
            values = {"status": "cancelled"} if change == "task_status" else {"project_id": 99999}
            concurrent.execute(update(Task).where(Task.id == world.task.id).values(**values))
        concurrent.commit()
    with pytest.raises(executor.ToolExecutionError) as error:
        executor.context_for_work_session(db, work.id)
    assert error.value.reason == "work_session_review_invalid"


def test_management_keeps_bound_project_for_task_dependency_and_review_targets(db, world):
    work = session_for(db, world, "management")
    other = project_repo.create_project(
        db, company_id=work.company_id, name="Another project", status="in_progress"
    )
    other_task = project_repo.create_task(
        db, project_id=other.id, title="Other work", status="in_review"
    )
    other_review = reviews.open_review_request(
        db,
        task=other_task,
        requester_employee_id=world.manager.id,
        reviewer_employee_id=world.manager.id,
    )
    attempts = [
        {"tool": "create_task", "args": {"project_id": other.id, "title": "Foreign work"}},
        {"tool": "update_task", "args": {"task_id": other_task.id, "title": "Mutated"}},
        {
            "tool": "create_dependency",
            "args": {"task_id": world.task.id, "depends_on_id": other_task.id},
        },
        {
            "tool": "create_task",
            "args": {
                "project_id": world.project.id,
                "title": "Invalid dependency",
                "depends_on": [other_task.id],
            },
        },
        {
            "tool": "create_task",
            "args": {
                "project_id": world.project.id,
                "title": "Invalid input",
                "consumes": [other_task.id],
            },
        },
        {
            "tool": "submit_review_verdict",
            "args": {"review_request_id": other_review.id, "verdict": "PASS"},
        },
    ]
    result = send(work, decision(world, actions=attempts))
    assert len(result["results"]) == len(attempts)
    assert all(item["reason"] == "work_session_write_forbidden" for item in result["results"])
    assert send(work, decision(world, actions=attempts)) == result
    # Reads retain company visibility even when write scope is narrower.
    other_read = read(world)
    other_read["args"]["project_id"] = other.id
    assert send(work, other_read)["results"][0]["ok"]
    db.expire_all()
    assert other_review.status == "open" and other_task.title == "Other work"
    assert db.scalar(select(func.count()).select_from(Task)) == 2


def test_management_consumes_real_drive_artifact_within_its_project(db, world):
    work = session_for(db, world, "management")
    producer = project_repo.create_task(
        db, project_id=world.project.id, title="Finished producer", status="done"
    )
    artifact = artifacts.record_project_artifact(
        db,
        world.project,
        artifact_type="source_code",
        title="Actual source",
        content="print('tested')",
        task_id=producer.id,
    )
    db.commit()
    request = decision(
        world,
        actions=[
            {
                "tool": "consume_artifact",
                "args": {"task_id": world.task.id, "artifact_id": artifact.id},
            }
        ],
    )
    receipt = send(work, request)["results"][0]
    assert receipt["ok"], receipt
    assert receipt["data"]["artifact_id"] == artifact.id


def test_reviewer_cannot_write_another_request_even_for_the_same_project(db, world):
    work = session_for(db, world, "review")
    other_task = project_repo.create_task(
        db, project_id=world.project.id, title="Another review", status="in_review"
    )
    other = reviews.open_review_request(
        db,
        task=other_task,
        requester_employee_id=world.manager.id,
        reviewer_employee_id=world.reviewer.id,
    )
    request = decision(world)
    request["decision"]["actions"][0]["args"]["review_request_id"] = other.id
    result = send(work, request)
    assert result["results"][0]["reason"] == "work_session_write_forbidden"
    assert send(work, request) == result
    db.expire_all()
    assert other.status == world.request.status == "open"


def test_own_verdict_refuses_all_fresh_writes_including_same_envelope(db, world):
    work = session_for(db, world, "review")
    request = decision(world)
    request["decision"]["actions"].extend(
        [
            {
                "tool": "submit_review_verdict",
                "args": {
                    "review_request_id": world.request.id,
                    "verdict": "REWORK",
                    "notes": "Changed mind",
                },
            },
            {
                "tool": "update_task",
                "args": {"task_id": world.task.id, "title": "Changed after verdict"},
            },
        ]
    )
    result = send(work, request)
    assert result["decision_status"] == "PARTIALLY_APPLIED"
    assert [r["reason"] for r in result["results"]] == [
        "applied",
        "work_session_write_forbidden",
        "work_session_write_forbidden",
    ]
    assert send(work, request) == result
    another = send(work, decision(world, key="new-write"))
    assert another["results"][0]["reason"] == "work_session_write_forbidden"
    assert send(work, finish())["status"] == "finished"
    db.expire_all()
    assert world.request.verdict == "PASS" and world.task.title == "Worker output awaiting review"


@pytest.mark.parametrize("source", ["service", "other_session", "uncorrelated_tool"])
def test_other_caller_verdict_never_grants_receipt_phase(db, world, source):
    work = session_for(db, world, "review")
    if source == "service":
        reviews.submit_verdict(
            db, request=world.request, reviewer_employee_id=world.reviewer.id, verdict="PASS"
        )
    elif source == "other_session":
        other = session_for(db, world, "review")
        assert send(other, decision(world))["results"][0]["ok"]
    else:
        from app.work import decisions

        # Even naming this WorkSession in an ordinary ToolAudit cannot impersonate
        # the bridge's correlated receipt evidence.
        ctx = executor.context_for_work_session(db, work.id)
        decisions.submit_envelope(db, ctx, **decision(world)["decision"])
    with pytest.raises(BridgeError) as error:
        send(work, finish())
    assert error.value.reason == "work_session_review_invalid"


@pytest.mark.parametrize(
    "change", ["new_request", "suspended", "actor_rebound", "project_cancelled", "reviewer_rebound"]
)
def test_own_verdict_receipt_phase_revalidates_hard_fences(db, world, change):
    work = session_for(db, world, "review")
    request = decision(world)
    request["decision"]["actions"][0]["args"]["verdict"] = "REWORK"
    assert send(work, request)["results"][0]["ok"]
    db.expire_all()
    if change == "new_request":
        tasks.transition_task(db, world.task, "in_progress")
        tasks.transition_task(db, world.task, "in_review")
        reviews.open_review_request(
            db,
            task=world.task,
            requester_employee_id=world.manager.id,
            reviewer_employee_id=world.reviewer.id,
        )
    elif change == "suspended":
        world.reviewer.lifecycle_status = "suspended"
    elif change == "actor_rebound":
        replacement = Person(slug="scope-replacement", name="Replacement person")
        db.add(replacement)
        db.flush()
        world.reviewer.person_id = replacement.id
    elif change == "project_cancelled":
        world.project.status = "cancelled"
    else:
        world.request.reviewer_employee_id = world.worker.id
    db.commit()
    with pytest.raises(BridgeError):
        send(work, request)
    with pytest.raises(BridgeError):
        send(work, finish())


@pytest.mark.parametrize("separate_sessions", [False, True])
def test_concurrent_verdicts_apply_once_and_never_misattributed(db, world, separate_sessions):
    work = session_for(db, world, "review")
    other = session_for(db, world, "review") if separate_sessions else work
    request, barrier = decision(world), Barrier(2)

    def submit(work):
        barrier.wait(timeout=5)
        try:
            return send(work, request)
        except BridgeError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = [
            future.result(timeout=20)
            for future in (pool.submit(submit, work), pool.submit(submit, other))
        ]
    if not separate_sessions:
        assert a == b
    else:
        successes = [r for r in (a, b) if isinstance(r, dict) and r["status"] == "completed"]
        assert len(successes) == 1
    db.expire_all()
    applied = list(db.scalars(select(ToolAudit).where(ToolAudit.outcome == "applied")))
    assert len(applied) == 1 and applied[0].tool_name == "submit_review_verdict"
    assert applied[0].work_session_id in {work.id, other.id}
    assert world.request.verdict == "PASS" and world.task.status == "done"


@pytest.mark.parametrize("purpose", ["management", "review"])
async def test_cancellation_during_model_await_prevents_late_decision(db, world, purpose):
    work = session_for(db, world, purpose, bound=True)

    async def cancel_then_reply(session, prompt):
        with SessionLocal() as concurrent:
            concurrent.execute(
                update(Project).where(Project.id == world.project.id).values(status="cancelled")
            )
            concurrent.commit()
        return decision(world)

    adapter = ScriptedAdapter(cancel_then_reply)
    result = await run(work, adapter)
    assert result["reason"] == "work_session_project_invalid"
    assert len(adapter.prompts) == 1
    db.expire_all()
    assert world.task.status == "in_review" and world.request.status == "open"
    assert db.scalar(select(func.count()).select_from(DecisionRecord)) == 0
    assert db.scalar(select(func.count()).select_from(ToolAudit)) == 0
    assert work.status == "running" and work.ended_at is None


@pytest.mark.parametrize("purpose", ["management", "review"])
async def test_natural_language_and_finish_never_complete_business_targets(db, world, purpose):
    work = session_for(db, world, purpose, bound=True)
    adapter = ScriptedAdapter("I have approved and completed everything.", finish())
    result = await run(work, adapter)
    assert result["status"] == "finished"
    assert adapter.prompts[1]["feedback"]["error"]["reason"] == "invalid_request"
    db.expire_all()
    assert world.project.status == "in_progress" and world.task.status == "in_review"
    assert world.request.verdict is None and world.request.status == "open"
    assert work.status == "running" and work.ended_at is None
    assert db.scalar(select(func.count()).select_from(DecisionRecord)) == 0
    assert db.scalar(select(func.count()).select_from(ToolAudit)) == 0
    assert db.scalar(select(func.count()).select_from(RuntimeOperation)) == 1


@pytest.mark.parametrize("purpose", ["management", "review"])
def test_primary_assignment_loss_still_allows_reads_but_refuses_writes(db, world, purpose):
    work = session_for(db, world, purpose)
    assignment = position_repo.active_primary_assignment(db, work.employee_id)
    with SessionLocal() as concurrent:
        concurrent.execute(
            update(type(assignment))
            .where(type(assignment).id == assignment.id)
            .values(effective_to=utcnow())
        )
        concurrent.commit()
    assert send(work, read(world))["results"][0]["ok"]
    actions = (
        None
        if purpose == "review"
        else [
            {
                "tool": "create_task",
                "args": {"project_id": world.project.id, "title": "No permission"},
            }
        ]
    )
    result = send(work, decision(world, actions=actions))
    receipt = result["results"][0]
    assert not receipt["ok"] and receipt["reason"] == "not_authorized"
    assert receipt["authority"]["reason"] == "no_active_assignment"


def test_receipt_acknowledgement_after_normal_project_completion(db, world):
    from app.workflow.orchestrator import Orchestrator

    work = session_for(db, world, "review")
    request = decision(world)
    result = send(work, request)
    # Exercise the existing progression, not a special session completion path.
    Orchestrator()._advance(world.task.id, True)
    db.expire_all()
    assert world.project.status == "completed"
    assignment = position_repo.active_primary_assignment(db, work.employee_id)
    assignment.effective_to = utcnow()
    db.commit()
    assert send(work, request) == result
    assert send(work, read(world))["results"][0]["ok"]
    assert (
        send(work, decision(world, key="late"))["results"][0]["reason"]
        == "work_session_write_forbidden"
    )
    assert send(work, finish())["status"] == "finished"


def test_legacy_worker_snapshot_without_purpose_keeps_existing_gates(db, world):
    world.task.status, world.task.assignee_id = "in_progress", world.worker.id
    db.commit()
    work = project_repo.create_work_session(
        db, task_id=world.task.id, employee_id=world.worker.id, runtime_type="mock"
    )
    snapshot = deepcopy(work.provenance_snapshot_json)
    snapshot.pop("purpose")
    snapshot.pop("review_request")
    legacy = WorkSession(
        task_id=work.task_id,
        employee_id=work.employee_id,
        person_id=work.person_id,
        company_id=work.company_id,
        runtime_type="mock",
        provenance_snapshot_json=snapshot,
    )
    db.add(legacy)
    db.commit()
    ctx = executor.context_for_work_session(db, legacy.id)
    assert ctx.task_id == world.task.id and "purpose" not in ctx.as_dict()
    world.task.status = "in_review"
    db.commit()
    with pytest.raises(executor.ToolExecutionError) as error:
        executor.context_for_work_session(db, legacy.id)
    assert error.value.reason == "work_session_task_invalid"


@pytest.mark.parametrize("purpose", ["management", "review"])
def test_nonworker_scope_never_bypasses_current_autonomy_policy(db, world, purpose, monkeypatch):
    work = session_for(db, world, purpose)
    monkeypatch.setitem(
        C.AUTONOMY_BY_SIDE_EFFECT, C.ToolSideEffect.write, AutonomyLevel.requires_confirmation
    )
    actions = (
        None
        if purpose == "review"
        else [
            {"tool": "create_task", "args": {"project_id": world.project.id, "title": "Must wait"}}
        ]
    )
    receipt = send(work, decision(world, actions=actions))["results"][0]
    assert receipt["reason"] == "autonomy_requires_confirmation"
    assert receipt["authority"]["allowed"]
    assert not receipt["ok"]
    assert db.get(ToolAudit, receipt["audit_id"]).outcome == receipt["reason"]


@pytest.mark.parametrize(
    "change,reason",
    [
        ("provider", "runtime_provider_unavailable"),
        ("cancelled", "work_session_project_invalid"),
        ("denied", "budget_denied"),
    ],
)
async def test_own_verdict_phase_preserves_provider_budget_and_cancel_fences(
    db, world, change, reason
):
    work = session_for(db, world, "review", bound=True)
    calls = 0

    async def budget(work_session_id, request_id):
        nonlocal calls
        calls += 1
        if calls == 2:
            with SessionLocal() as concurrent:
                if change == "provider":
                    concurrent.execute(
                        update(Provider)
                        .where(Provider.id == work.provider_id)
                        .values(enabled=False)
                    )
                elif change == "cancelled":
                    concurrent.execute(
                        update(Project)
                        .where(Project.id == work.project_id)
                        .values(status="cancelled")
                    )
                concurrent.commit()
            return change != "denied"
        return True

    adapter = ScriptedAdapter(decision(world), finish())
    result = await run(work, adapter, budget_check=budget)
    assert result["reason"] == reason
    assert len(adapter.prompts) == 1
    # The applied result remains true even when feedback delivery must stop.
    receipt = result["turns"][0]["result"]["results"][0]
    assert receipt["ok"] and receipt["reason"] == "applied"
    db.expire_all()
    assert world.request.verdict == "PASS" and world.task.status == "done"
    assert work.status == "running" and work.ended_at is None


async def test_review_model_gets_saved_replay_and_fresh_write_refusal_feedback(db, world):
    work = session_for(db, world, "review", bound=True)
    request = decision(world)
    adapter = ScriptedAdapter(request, request, decision(world, key="late-write"), finish())
    result = await run(work, adapter)
    assert result["status"] == "finished"
    first, replay, denied, _finished = result["turns"]
    assert replay["result"] == first["result"]
    assert denied["result"]["results"][0]["reason"] == "work_session_write_forbidden"
    for index, previous in enumerate((first, replay, denied), start=1):
        assert adapter.prompts[index]["feedback"]["result"] == previous["result"]
        assert "tools" not in adapter.prompts[index]
        assert adapter.prompts[index]["scope"]["review_request_id"] == world.request.id


@pytest.mark.parametrize("change", ["notes", "verdict", "decided_at"])
def test_receipt_phase_does_not_accept_rewritten_verdict_facts(db, world, change):
    work = session_for(db, world, "review")
    request = decision(world)
    send(work, request)
    db.expire_all()
    if change == "notes":
        world.request.verdict_notes = "Not the applied notes"
    elif change == "verdict":
        world.request.verdict = "ESCALATE"
    else:
        world.request.decided_at = None
    db.commit()
    with pytest.raises(BridgeError) as error:
        send(work, request)
    assert error.value.reason == "work_session_review_invalid"


@pytest.mark.parametrize(
    "verdict,unfinished", [("REWORK", False), ("ESCALATE", False), ("PASS", True)]
)
def test_completed_project_acknowledgement_requires_own_pass_and_all_work_done(
    db, world, verdict, unfinished
):
    work = session_for(db, world, "review")
    request = decision(world)
    request["decision"]["actions"][0]["args"]["verdict"] = verdict
    send(work, request)
    db.expire_all()
    if unfinished:
        project_repo.create_task(db, project_id=world.project.id, title="Unfinished work")
    world.project.status = "completed"
    db.commit()
    with pytest.raises(BridgeError) as error:
        send(work, finish())
    assert error.value.reason == "work_session_project_invalid"


@pytest.mark.parametrize("status", ["completed", "cancelled", "rejected"])
def test_management_terminal_target_never_authorizes_fresh_work(db, world, status):
    work = session_for(db, world, "management")
    world.project.status = status
    db.commit()
    with pytest.raises(BridgeError) as error:
        send(
            work,
            decision(
                world,
                actions=[
                    {
                        "tool": "create_task",
                        "args": {"project_id": world.project.id, "title": "Late work"},
                    }
                ],
            ),
        )
    assert error.value.reason == "work_session_project_invalid"
    assert db.scalar(select(func.count()).select_from(DecisionRecord)) == 0


def test_ended_nonworker_session_cannot_continue_even_with_its_own_receipt(db, world):
    work = session_for(db, world, "review")
    request = decision(world)
    send(work, request)
    work.status, work.ended_at = "completed", utcnow()
    db.commit()
    with pytest.raises(BridgeError) as error:
        send(work, request)
    assert error.value.reason == "work_session_not_running"


@pytest.mark.parametrize("purpose", ["management", "review"])
@pytest.mark.parametrize("kind", ["project", "task", "company", "person", "employee", "position"])
def test_nonworker_envelope_scope_cannot_misattribute_an_in_scope_action(db, world, purpose, kind):
    work = session_for(db, world, purpose)
    other = project_repo.create_project(
        db, company_id=work.company_id, name="Other decision target"
    )
    other_task = project_repo.create_task(db, project_id=other.id, title="Other decision work")
    position = authority.resolve_actor_authority(db, work.employee_id)
    db.commit()
    scope_id = {
        "project": other.id,
        "task": other_task.id,
        "company": work.company_id,
        "person": work.person_id,
        "employee": work.employee_id,
        "position": position.position_definition_id,
    }[kind]
    actions = (
        None
        if purpose == "review"
        else [
            {
                "tool": "create_task",
                "args": {
                    "project_id": work.project_id,
                    "title": "Correct action, wrong decision target",
                },
            }
        ]
    )
    request = decision(world, actions=actions)
    request["decision"]["scope"] = f"{kind}:{scope_id}"
    with pytest.raises(BridgeError) as error:
        send(work, request)
    assert error.value.reason == "invalid_reference"
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(DecisionRecord)) == 0
    assert db.scalar(select(func.count()).select_from(RuntimeOperation)) == 0
    assert db.scalar(select(func.count()).select_from(ToolAudit)) == 0
    assert world.request.status == "open" and world.task.status == "in_review"


def test_review_envelope_scope_cannot_name_a_sibling_task(db, world):
    work = session_for(db, world, "review")
    sibling = project_repo.create_task(db, project_id=world.project.id, title="Unreviewed sibling")
    db.commit()
    request = decision(world)
    request["decision"]["scope"] = f"task:{sibling.id}"
    with pytest.raises(BridgeError) as error:
        send(work, request)
    assert error.value.reason == "invalid_reference"


@pytest.mark.parametrize("purpose", ["management", "review"])
def test_nonworker_envelope_scope_accepts_target_task_and_company_visible_context(
    db, world, purpose
):
    work = session_for(db, world, purpose)
    other = project_repo.create_project(db, company_id=work.company_id, name="Visible comparison")
    other_task = project_repo.create_task(db, project_id=other.id, title="Reference fact")
    target = (
        world.task
        if purpose == "review"
        else project_repo.create_task(
            db, project_id=work.project_id, title="Manager may target any project task"
        )
    )
    db.commit()
    actions = (
        None
        if purpose == "review"
        else [{"tool": "update_task", "args": {"task_id": target.id, "title": "Management action"}}]
    )
    request = decision(world, actions=actions)
    request["decision"]["scope"] = f"task:{target.id}"
    request["decision"]["context"] = {"project_id": other.id, "task_ids": [other_task.id]}
    result = send(work, request)
    assert result["decision_status"] == "APPLIED"
    record = db.get(DecisionRecord, result["decision_id"])
    assert record.task_id == target.id
    assert record.context_json["project_id"] == other.id
    assert record.context_json["task_ids"] == [other_task.id]
