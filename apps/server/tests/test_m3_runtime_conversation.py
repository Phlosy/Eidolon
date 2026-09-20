"""Local controlled conversations traverse the real bridge and domain transactions."""

import asyncio
import json
import time
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, text, update

from app.core.database import SessionLocal
from app.models.base import utcnow
from app.models.decision import DecisionRecord, ToolAudit
from app.models.organization import Employee
from app.models.position import PositionAuthorityGrant, PositionSlot
from app.models.project import Project, Task, WorkSession
from app.models.provider import ModelBinding, Provider
from app.models.runtime import RuntimeInstance as RuntimeRow
from app.models.runtime_operation import RuntimeOperation
from app.providers.secrets.store import get_secret_store
from app.repositories import position as position_repo
from app.repositories import project as project_repo
from app.repositories import runtimes as runtime_repo
from app.runtimes import probe
from app.runtimes.base import RuntimeEvent, RuntimeEventKind, RuntimeReply, RuntimeSession
from app.runtimes.container_common import resolve_container_instance
from app.work.runtime_conversation import ConversationLimits, run_conversation
from app.work.runtime_operations import RuntimeExecution


@pytest.fixture()
def world(db, default_company_id):
    actor = db.scalar(select(Employee).where(Employee.slug == "alice"))
    provider = Provider(
        company_id=default_company_id,
        name="Original controlled provider",
        provider_type="custom",
        base_url="https://provider.invalid/private-route",
        credential_ref="private-provider-reference",
    )
    project = Project(
        company_id=default_company_id,
        name="Controlled conversation",
        status="in_progress",
        management_employee_id=actor.id,
    )
    db.add_all([provider, project])
    db.flush()
    task = Task(
        project_id=project.id,
        title="Current assigned work",
        status="in_progress",
        assignee_id=actor.id,
    )
    db.add(task)
    row = runtime_repo.get_instance_for_employee(db, actor.id)
    if row is None:
        row = runtime_repo.create_instance(db, employee_id=actor.id)
    row.runtime_type = "hermes"
    row.status = "running"
    row.deployment_mode = "docker"
    row.container_id = "local-test-container"
    row.container_name = "local-test-runtime"
    row.resource_key = "local-test-resource"
    row.runtime_version = "local-test-version"
    row.image_digest = "sha256:local-test"
    row.internal_host, row.internal_port = "control.invalid", 8765
    row.metadata_json = {
        "api_key_ref": get_secret_store().store(db, "private-control-token"),
        "device_key_ref": get_secret_store().store(db, "private-device-key"),
        "applied_model_fingerprint": probe.model_fingerprint(provider, "original-model"),
    }
    db.flush()
    work = project_repo.create_work_session(
        db,
        task_id=task.id,
        employee_id=actor.id,
        runtime_type="hermes",
        runtime_instance_id=row.id,
        provider_id=provider.id,
        model="original-model",
        started_at=utcnow(),
    )
    db.commit()
    runtime = RuntimeSession(
        id="host-controlled-test",
        instance=resolve_container_instance(actor.id, "hermes", session_factory=SessionLocal),
        task_id=task.id,
    )
    return SimpleNamespace(
        actor=actor,
        provider=provider,
        project=project,
        task=task,
        row=row,
        work=work,
        runtime=runtime,
    )


def read(world, key="read"):
    return {
        "version": 1,
        "kind": "read",
        "operation_key": key,
        "tool": "inspect_project",
        "args": {"project_id": world.project.id},
    }


def decision(world, key="create", *, title="Model chosen task"):
    return {
        "version": 1,
        "kind": "decision",
        "operation_key": key,
        "decision": {
            "decision_type": "decompose_project",
            "reason": "The observed project needs one next step",
            "scope": f"project:{world.project.id}",
            "actions": [
                {
                    "tool": "create_task",
                    "args": {
                        "project_id": world.project.id,
                        "title": title,
                    },
                }
            ],
        },
    }


def finish(summary="Acknowledged"):
    return {"version": 1, "kind": "finish", "operation_key": "finish", "summary": summary}


class ScriptedAdapter:
    type = "hermes"
    supports_controlled_turn = True

    def __init__(self, *replies):
        self.replies = iter(replies)
        self.prompts = []
        self.cancelled = []

    async def complete_turn(self, session, prompt, *, request_id, timeout):
        self.prompts.append(json.loads(prompt))
        session.remote_run_id = f"actual-run-{len(self.prompts)}"
        reply = next(self.replies)
        if callable(reply):
            reply = await reply(session, self.prompts[-1])
        return RuntimeReply(
            session.id,
            request_id,
            session.remote_run_id,
            reply if isinstance(reply, str) else json.dumps(reply),
        )

    async def cancel_task(self, session):
        self.cancelled.append(session.remote_run_id)
        session.status = "cancelled"


async def admitted(work_session_id, request_id):
    return True


async def run(world, adapter, **kwargs):
    return await run_conversation(
        world.work.id,
        adapter=adapter,
        runtime_session=world.runtime,
        goal="Inspect facts, create the next task, and acknowledge its saved receipt.",
        budget_check=kwargs.pop("budget_check", admitted),
        session_factory=SessionLocal,
        **kwargs,
    )


async def test_read_create_and_acknowledge_saved_results(db, world):
    adapter = ScriptedAdapter(read(world), decision(world), finish("Task receipt acknowledged"))
    result = await run(world, adapter)
    assert result["status"] == "finished"
    assert result["summary"] == "Task receipt acknowledged"
    assert len(result["turns"]) == 3
    first, created, final = result["turns"]
    assert adapter.prompts[1]["feedback"]["result"] == first["result"]
    assert adapter.prompts[2]["feedback"]["result"] == created["result"]
    assert created["result"]["decision_status"] == "APPLIED"
    with SessionLocal() as observer:
        audit = observer.get(ToolAudit, created["result"]["results"][0]["audit_id"])
        assert audit.tool_name == "create_task" and audit.outcome == "applied"
        record = observer.get(DecisionRecord, created["result"]["decision_id"])
        assert record.actor_employee_id == world.actor.id
        assert observer.scalar(select(Task).where(Task.title == "Model chosen task")) is not None
        work = observer.get(WorkSession, world.work.id)
        assert work.runtime_session_ref == world.runtime.id
        assert work.status == "running" and work.ended_at is None
        assert observer.get(Task, world.task.id).status == "in_progress"
        assert observer.get(Project, world.project.id).status == "in_progress"
    initial = adapter.prompts[0]
    assert initial["scope"]["work_session_id"] == world.work.id
    assert initial["role_context"]["person_id"] == world.actor.person_id
    assert any(tool["name"] == "create_task" for tool in initial["tools"])
    assert next(tool for tool in initial["tools"] if tool["name"] == "create_task")[
        "capability_actions"
    ] == ["split_task"]
    assert initial["reply_contract"]["version"] == 1
    evidence = json.dumps([adapter.prompts, result])
    for private in (
        "private-control-token",
        "private-device-key",
        "private-provider-reference",
        "control.invalid",
        "provider.invalid",
        "local-test-container",
    ):
        assert private not in evidence
    assert [turn["run_id"] for turn in result["turns"]] == [
        "actual-run-1",
        "actual-run-2",
        "actual-run-3",
    ]
    assert final["result"]["status"] == "finished"
    assert adapter.cancelled == []


def counts(world):
    with SessionLocal() as db:
        return tuple(
            db.scalar(select(func.count()).select_from(model))
            for model in (RuntimeOperation, DecisionRecord, ToolAudit, Task)
        )


@pytest.mark.parametrize(
    "change,reason",
    [
        ("unsupported", "controlled_turn_unsupported"),
        ("adapter", "runtime_binding_mismatch"),
        ("session_id", "invalid_session_id"),
        ("task_id", "runtime_binding_mismatch"),
        ("employee_id", "runtime_binding_mismatch"),
        ("missing_instance", "runtime_binding_missing"),
        ("missing_row", "runtime_binding_mismatch"),
        ("foreign_row", "runtime_binding_mismatch"),
        ("owner", "runtime_identity_mismatch"),
        ("type", "runtime_identity_mismatch"),
        ("resource", "runtime_identity_mismatch"),
        ("fingerprint", "runtime_binding_changed"),
        ("base_url", "runtime_connection_mismatch"),
        ("api_key", "runtime_connection_mismatch"),
        ("device_key", "runtime_connection_mismatch"),
        ("model", "runtime_model_identity_unknown"),
        ("binding", "runtime_session_mismatch"),
        ("ended", "work_session_not_running"),
    ],
)
async def test_invalid_binding_stops_before_model_or_business_write(db, world, change, reason):
    adapter = ScriptedAdapter(decision(world))
    if change == "unsupported":
        adapter.supports_controlled_turn = False
    elif change == "adapter":
        adapter.type = "openclaw"
    elif change == "session_id":
        world.runtime.id = "invalid/session"
    elif change == "task_id":
        world.runtime.task_id += 1000
    elif change == "employee_id":
        world.runtime.instance.employee_id += 1000
    elif change == "missing_instance":
        world.runtime.instance = None
    elif change == "missing_row":
        world.runtime.instance.details.pop("row_id")
    elif change == "foreign_row":
        world.runtime.instance.details["row_id"] += 1000
    elif change == "owner":
        world.row.person_id += 1000
    elif change == "type":
        world.row.runtime_type = "openclaw"
    elif change == "resource":
        world.row.resource_key = "replacement"
    elif change == "fingerprint":
        world.runtime.instance.details.pop("instance_fingerprint")
    elif change in {"base_url", "api_key", "device_key"}:
        world.runtime.instance.details[change] = "other-connection"
    elif change == "model":
        world.row.metadata_json = {**world.row.metadata_json, "applied_model_fingerprint": None}
    elif change == "binding":
        world.work.runtime_session_ref = "another-session"
    elif change == "ended":
        world.work.ended_at = utcnow()
    db.commit()
    before = counts(world)
    result = await run(world, adapter)
    assert result["reason"] == reason, result
    assert result["status"] == "stopped"
    assert adapter.prompts == [] and adapter.cancelled == []
    assert counts(world) == before


async def test_desired_employee_configuration_does_not_rewrite_execution_identity(db, world):
    world.actor.runtime_type = "openclaw"
    db.add(
        ModelBinding(
            employee_id=world.actor.id,
            person_id=world.actor.person_id,
            provider_id=world.provider.id,
            model="desired-other-model",
            is_primary=False,
        )
    )
    db.commit()
    adapter = ScriptedAdapter(decision(world), finish())
    result = await run(world, adapter)
    assert result["status"] == "finished"
    assert result["turns"][0]["result"]["results"][0]["actor"]["runtime_type"] == "hermes"
    db.expire_all()
    assert world.work.model == "original-model" and world.work.runtime_type == "hermes"


@pytest.mark.parametrize("stage", ["before_turn", "during_turn"])
@pytest.mark.parametrize("change", ["disabled", "private_owner", "company"])
async def test_original_provider_admission_is_rechecked(db, world, stage, change):
    world.provider.scope = "employee"
    world.provider.owner_employee_id = world.actor.id
    world.provider.owner_person_id = world.actor.person_id
    db.commit()

    def revoke():
        with SessionLocal() as writer:
            provider = writer.get(Provider, world.provider.id)
            if change == "disabled":
                provider.enabled = False
            elif change == "private_owner":
                provider.owner_employee_id += 1000
                provider.owner_person_id += 1000
            else:
                provider.company_id += 1000
            writer.commit()

    async def during(session, prompt):
        revoke()
        return decision(world)

    if stage == "before_turn":
        revoke()
    adapter = ScriptedAdapter(during if stage == "during_turn" else decision(world), finish())
    before = counts(world)
    result = await run(world, adapter)
    assert result["reason"] == "runtime_provider_unavailable"
    assert len(adapter.prompts) == int(stage == "during_turn")
    assert counts(world) == before
    db.expire_all()
    assert world.work.provider_id == world.provider.id and world.work.model == "original-model"


async def test_legacy_null_resource_key_is_preserved_without_invention(db, world):
    # A genuinely canonical legacy-like snapshot, not a rewritten WorkSession.
    world.row.resource_key = None
    db.flush()
    work = project_repo.create_work_session(
        db,
        task_id=world.task.id,
        employee_id=world.actor.id,
        runtime_type="hermes",
        runtime_instance_id=world.row.id,
        provider_id=world.provider.id,
        model="original-model",
    )
    db.commit()
    world.work = work
    world.runtime.instance = resolve_container_instance(world.actor.id, "hermes")
    result = await run(world, ScriptedAdapter(finish()))
    assert result["status"] == "finished"
    assert world.work.provenance_snapshot_json["runtime"]["resource_key"] is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("session_id", "foreign-session"),
        ("request_id", "foreign-request"),
        ("run_id", "foreign-run"),
        ("run_id", ""),
        ("run_id", "x" * 257),
    ],
)
async def test_foreign_or_unbounded_reply_identity_never_reaches_bridge(world, field, value):
    adapter = ScriptedAdapter(decision(world))
    original = adapter.complete_turn

    async def forged(*args, **kwargs):
        return replace(await original(*args, **kwargs), **{field: value})

    adapter.complete_turn = forged
    before = counts(world)
    result = await run(world, adapter)
    assert result["reason"] == "reply_identity_mismatch"
    assert counts(world) == before


async def test_active_legacy_stream_is_never_consumed_or_cancelled(world):
    event = RuntimeEvent(RuntimeEventKind.tool_call, decision(world))
    await world.runtime.queue.put(event)
    world.runtime.background = asyncio.create_task(asyncio.Event().wait())
    adapter = ScriptedAdapter(decision(world))
    before = counts(world)
    try:
        result = await run(world, adapter)
        assert result["reason"] == "session_already_streaming"
        assert world.runtime.status == "running"
        assert await world.runtime.queue.get() is event
        assert adapter.prompts == adapter.cancelled == []
        assert counts(world) == before
    finally:
        world.runtime.background.cancel()
        await asyncio.gather(world.runtime.background, return_exceptions=True)


@pytest.mark.parametrize(
    "change,reason",
    [
        ("end", "work_session_not_running"),
        ("offboard", "employee_not_active"),
        ("reassign", "work_session_task_invalid"),
        ("project", "work_session_project_invalid"),
        ("connection", "runtime_binding_changed"),
        ("cancel", "session_not_running"),
    ],
)
async def test_scope_is_rechecked_after_model_await(world, change, reason):
    async def changing(session, prompt):
        with SessionLocal() as db:
            # This independent writer would fail if the host held a DB lock over await.
            db.execute(text("PRAGMA busy_timeout=50"))
            if change == "end":
                db.get(WorkSession, world.work.id).ended_at = utcnow()
            elif change == "offboard":
                db.get(Employee, world.actor.id).lifecycle_status = "offboarded"
            elif change == "reassign":
                db.get(Task, world.task.id).assignee_id = None
            elif change == "project":
                db.get(Project, world.project.id).status = "done"
            elif change == "connection":
                db.get(RuntimeRow, world.row.id).internal_port += 1
            elif change == "cancel":
                session.status = "cancelled"
            db.commit()
        return decision(world)

    adapter = ScriptedAdapter(changing)
    before = counts(world)
    result = await run(world, adapter)
    assert result["reason"] == reason, result
    assert counts(world) == before
    assert len(adapter.prompts) == 1


async def test_budget_io_has_no_database_lock_and_scope_is_checked_again(world):
    async def admission(*_args):
        with SessionLocal() as db:
            db.execute(text("PRAGMA busy_timeout=50"))
            db.get(WorkSession, world.work.id).ended_at = utcnow()
            db.commit()
        return True

    adapter = ScriptedAdapter(decision(world))
    result = await run(world, adapter, budget_check=admission)
    assert result["reason"] == "work_session_not_running"
    assert adapter.prompts == []


async def test_authority_revoked_during_await_returns_actual_denial_and_fresh_role(db, world):
    assignment = position_repo.active_primary_assignment(db, world.actor.id)
    definition_id = db.get(PositionSlot, assignment.position_slot_id).position_definition_id
    db.commit()

    async def revoke(session, prompt):
        with SessionLocal() as writer:
            writer.execute(
                update(PositionAuthorityGrant)
                .where(
                    PositionAuthorityGrant.position_definition_id == definition_id,
                    PositionAuthorityGrant.authority_kind == "plan_project_work",
                )
                .values(effective_to=utcnow())
            )
            writer.commit()
        return decision(world)

    adapter = ScriptedAdapter(revoke, finish("Saw the denial"))
    result = await run(world, adapter)
    receipt = result["turns"][0]["result"]
    assert receipt["decision_status"] == "FAILED"
    assert receipt["results"][0]["reason"] == "not_authorized"
    assert adapter.prompts[1]["feedback"]["result"] == receipt
    assert not any(
        grant["kind"] == "plan_project_work"
        for grant in adapter.prompts[1]["role_context"]["authority"]
    )
    assert result["status"] == "finished"


async def test_replayed_and_conflicting_requests_use_real_saved_receipt_ids(world):
    request = decision(world)
    adapter = ScriptedAdapter(request, request, decision(world, title="Conflicting task"), finish())
    result = await run(world, adapter)
    a, replayed, conflict, _ = result["turns"]
    assert a["result"] == replayed["result"]
    assert conflict["error"]["reason"] == "operation_conflict"
    assert conflict["error"]["operation_id"] == a["result"]["operation_id"]
    assert adapter.prompts[3]["feedback"]["error"] == conflict["error"]
    with SessionLocal() as observer:
        assert (
            observer.scalar(
                select(func.count()).select_from(Task).where(Task.title == "Model chosen task")
            )
            == 1
        )
        assert observer.scalar(select(Task).where(Task.title == "Conflicting task")) is None
        assert observer.scalar(select(func.count()).select_from(ToolAudit)) == 1


async def test_partial_success_is_returned_to_next_turn_without_fabricated_rollback(world):
    request = decision(world)
    request["decision"]["actions"].append(
        {
            "tool": "update_task",
            "args": {"task_id": world.task.id, "status": "done"},
        }
    )
    adapter = ScriptedAdapter(request, finish("Partial result acknowledged"))
    result = await run(world, adapter)
    receipt = result["turns"][0]["result"]
    assert receipt["decision_status"] == "PARTIALLY_APPLIED"
    assert [r["ok"] for r in receipt["results"]] == [True, False]
    assert adapter.prompts[1]["feedback"]["result"] == receipt


@pytest.mark.parametrize(
    "raw",
    [
        "I completed everything",
        '{"\\ud800":1,"\\ud800":2}',
        '{"version":1,"kind":"read","operation_key":"x","actor_employee_id":1}',
    ],
)
async def test_invalid_reply_gets_bounded_unicode_safe_feedback_only(world, raw):
    adapter = ScriptedAdapter(raw, finish("Corrected"))
    before = counts(world)
    result = await run(world, adapter)
    invalid = result["turns"][0]
    assert invalid["request"] is None
    assert invalid["error"]["reason"] == "invalid_request"
    assert adapter.prompts[1]["feedback"]["error"] == invalid["error"]
    assert len(json.dumps(result, ensure_ascii=False).encode()) < 5000
    assert counts(world) == (before[0] + 1, *before[1:])


async def test_invalid_reference_gets_repairable_feedback(world):
    request = decision(world)
    request["decision"]["context"] = {"artifact_refs": [999999]}
    adapter = ScriptedAdapter(request, finish())
    result = await run(world, adapter)
    assert result["turns"][0]["error"]["reason"] == "invalid_reference"
    assert result["status"] == "finished"
    assert adapter.prompts[1]["feedback"]["error"]["reason"] == "invalid_reference"


@pytest.mark.parametrize(
    "limit,reason,calls",
    [
        ({"max_rounds": 1}, "round_limit", 1),
        ({"max_actions": 0}, "action_limit", 1),
        ({"max_prompt_bytes": 100}, "prompt_size_limit", 0),
        ({"max_reply_bytes": 10}, "reply_size_limit", 1),
        ({"max_result_bytes": 10}, "result_size_limit", 1),
    ],
)
async def test_loop_limits_stop_without_starting_an_extra_turn(world, limit, reason, calls):
    adapter = ScriptedAdapter(decision(world), finish())
    result = await run(world, adapter, limits=ConversationLimits(**limit))
    assert result["reason"] == reason
    assert len(adapter.prompts) == calls
    if reason in {"round_limit", "result_size_limit"}:
        with SessionLocal() as db:
            assert db.scalar(select(Task).where(Task.title == "Model chosen task")) is not None
    else:
        assert not any(turn.get("result") for turn in result["turns"])
    if reason == "result_size_limit":
        reference = result["turns"][0]["receipt_reference"]
        with SessionLocal() as db:
            assert db.get(ToolAudit, reference["audit_ids"][0]).outcome == "applied"
    assert adapter.cancelled == []  # the complete reply already ended its remote run


async def test_total_action_allowance_counts_replayed_actions(world):
    request = decision(world)
    adapter = ScriptedAdapter(request, request, request)
    result = await run(world, adapter, limits=ConversationLimits(max_actions=2))
    assert result["reason"] == "action_limit" and result["submitted_actions"] == 2
    assert result["turns"][0]["result"] == result["turns"][1]["result"]
    assert "result" not in result["turns"][2]


@pytest.mark.parametrize(
    "change",
    [
        {"max_rounds": 0},
        {"max_rounds": 33},
        {"max_rounds": True},
        {"max_actions": -1},
        {"max_actions": 513},
        {"max_actions": 0.5},
        {"turn_timeout_seconds": 0},
        {"turn_timeout_seconds": 901},
        {"total_timeout_seconds": float("inf")},
        {"total_timeout_seconds": float("nan")},
        {"total_timeout_seconds": 3601},
        {"max_reply_bytes": 65537},
        {"max_prompt_bytes": 0},
        {"max_result_bytes": 1024 * 1024 + 1},
    ],
)
def test_limits_reject_unbounded_or_ambiguous_configuration(change):
    with pytest.raises(ValueError):
        ConversationLimits(**change)


@pytest.mark.parametrize(
    "admission,reason",
    [
        (None, "budget_unavailable"),
        (False, "budget_denied"),
        (1, "budget_denied"),
        ("exception", "budget_unavailable"),
    ],
)
async def test_budget_is_mandatory_fail_closed_and_never_uses_global_abort(
    world, admission, reason
):
    async def check(*args):
        if admission == "exception":
            raise RuntimeError("private-budget-connection")
        return admission

    adapter = ScriptedAdapter(decision(world))
    result = await run(world, adapter, budget_check=None if admission is None else check)
    assert result["reason"] == reason
    assert adapter.prompts == adapter.cancelled == []
    assert "private-budget-connection" not in json.dumps(result)


async def test_admission_is_checked_for_each_unique_request_and_can_deny_later(world):
    admitted_ids = []

    async def check(work_session_id, request_id):
        assert work_session_id == world.work.id
        admitted_ids.append(request_id)
        return len(admitted_ids) == 1

    adapter = ScriptedAdapter(read(world), decision(world))
    result = await run(world, adapter, budget_check=check)
    assert result["reason"] == "budget_denied"
    assert len(set(admitted_ids)) == 2
    assert result["turns"][0]["request_id"] == admitted_ids[0]
    assert len(adapter.prompts) == 1 and adapter.cancelled == []


async def test_budget_wait_is_bounded_by_total_deadline_without_remote_cancellation(world):
    async def slow(*args):
        await asyncio.Event().wait()

    adapter = ScriptedAdapter(decision(world))
    result = await run(
        world, adapter, budget_check=slow, limits=ConversationLimits(total_timeout_seconds=0.03)
    )
    assert result["reason"] == "total_timeout"
    assert adapter.prompts == adapter.cancelled == []


@pytest.mark.parametrize("overall", [False, True])
async def test_model_timeouts_target_only_the_known_inflight_run(world, overall):
    async def slow(session, prompt):
        await asyncio.Event().wait()

    adapter = ScriptedAdapter(slow)
    limit = {"total_timeout_seconds": 0.03} if overall else {"turn_timeout_seconds": 0.03}
    result = await run(world, adapter, limits=ConversationLimits(**limit))
    assert result["reason"] == ("total_timeout" if overall else "turn_timeout")
    assert adapter.cancelled == ["actual-run-1"]
    assert result["turns"][0]["remote_stop"] == {"run_id": "actual-run-1", "confirmed": False}


async def test_late_reply_cannot_evade_the_host_turn_deadline(world):
    async def late(session, prompt):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return decision(world)

    adapter = ScriptedAdapter(late, finish())
    before = counts(world)
    result = await run(world, adapter, limits=ConversationLimits(turn_timeout_seconds=0.02))
    assert result["reason"] == "turn_timeout"
    assert counts(world) == before
    assert len(adapter.prompts) == 1


async def test_expired_saved_deadline_prevents_controlled_send(world, monkeypatch):
    from app.work import telemetry

    original = telemetry.prepare_attempt

    def expired(*args, **kwargs):
        kwargs["deadline_at"] = utcnow() - timedelta(seconds=1)
        return original(*args, **kwargs)

    monkeypatch.setattr(telemetry, "prepare_attempt", expired)
    adapter = ScriptedAdapter(decision(world))
    before = counts(world)
    result = await run(world, adapter)
    assert result["reason"] == "turn_timeout"
    assert adapter.prompts == adapter.cancelled == []
    assert counts(world) == before


async def test_already_stopped_adapter_failure_is_not_cancelled_twice(world):
    async def stopped(session, prompt):
        session.status = "failed"
        session.cancel_confirmed = False
        raise ConnectionError("private-control-route")

    adapter = ScriptedAdapter(stopped)
    result = await run(world, adapter)
    assert result["reason"] == "runtime_failed"
    assert adapter.cancelled == []
    assert result["turns"][0]["remote_stop"]["confirmed"] is False
    assert "private-control-route" not in json.dumps(result)


@pytest.mark.parametrize("suppress", [False, True])
async def test_cancellation_propagates_and_stops_only_current_known_run(world, suppress):
    entered = asyncio.Event()

    async def pending(session, prompt):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            if suppress:
                return decision(world)
            raise

    adapter = ScriptedAdapter(pending)
    task = asyncio.create_task(run(world, adapter))
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert world.runtime.status == "cancelled"
    assert adapter.cancelled == ([] if suppress else ["actual-run-1"])
    with SessionLocal() as db:
        assert db.get(Task, world.task.id).status == "in_progress"
        assert db.get(WorkSession, world.work.id).status == "running"


async def test_cancellation_before_first_model_call_does_not_abort_a_remote_session(world):
    entered = asyncio.Event()

    async def check(*args):
        entered.set()
        await asyncio.Event().wait()

    adapter = ScriptedAdapter(finish())
    task = asyncio.create_task(run(world, adapter, budget_check=check))
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert adapter.prompts == adapter.cancelled == []


async def test_concurrent_and_repeated_host_invocation_cannot_reset_allowance(world):
    entered, release = asyncio.Event(), asyncio.Event()

    async def pending(session, prompt):
        entered.set()
        await release.wait()
        return finish()

    adapter = ScriptedAdapter(pending)
    task = asyncio.create_task(run(world, adapter))
    await asyncio.wait_for(entered.wait(), 1)
    concurrent = await run(world, adapter)
    assert concurrent["reason"] == "conversation_already_started"
    assert world.runtime.status == "running"
    release.set()
    assert (await task)["status"] == "finished"
    again = await run(world, adapter)
    assert again["reason"] == "conversation_already_started"
    assert len(adapter.prompts) == 1


async def test_runtime_change_between_actions_stops_with_prior_receipt(world, monkeypatch):
    execute = RuntimeExecution.execute_action

    def replace_connection(self, index, *args, **kwargs):
        result = execute(self, index, *args, **kwargs)
        if index == 0:
            with SessionLocal() as db:
                db.get(RuntimeRow, world.row.id).internal_port += 1
                db.commit()
        return result

    monkeypatch.setattr(RuntimeExecution, "execute_action", replace_connection)
    request = decision(world)
    request["decision"]["actions"].append(
        {
            "tool": "create_task",
            "args": {"project_id": world.project.id, "title": "Must not run"},
        }
    )
    adapter = ScriptedAdapter(request, finish())
    result = await run(world, adapter)
    assert result["reason"] == "runtime_binding_changed"
    receipt = result["turns"][0]["result"]
    assert receipt["status"] == "interrupted" and receipt["action_indices"] == [0]
    assert receipt["results"][0]["ok"]
    with SessionLocal() as db:
        assert db.scalar(select(Task).where(Task.title == "Model chosen task")) is not None
        assert db.scalar(select(Task).where(Task.title == "Must not run")) is None
    assert len(adapter.prompts) == 1


async def test_total_deadline_preserves_committed_action_and_stops_later_actions(
    world, monkeypatch
):
    execute = RuntimeExecution.execute_action

    def slow_first(self, index, *args, **kwargs):
        result = execute(self, index, *args, **kwargs)
        if index == 0:
            time.sleep(0.15)
        return result

    monkeypatch.setattr(RuntimeExecution, "execute_action", slow_first)
    request = decision(world)
    request["decision"]["actions"].append(
        {
            "tool": "create_task",
            "args": {"project_id": world.project.id, "title": "Too late"},
        }
    )
    result = await run(
        world, ScriptedAdapter(request), limits=ConversationLimits(total_timeout_seconds=0.1)
    )
    assert result["reason"] == "total_timeout"
    receipt = result["turns"][0]["result"]
    assert receipt["status"] == "interrupted" and receipt["action_indices"] == [0]
    with SessionLocal() as db:
        assert db.scalar(select(Task).where(Task.title == "Model chosen task")) is not None
        assert db.scalar(select(Task).where(Task.title == "Too late")) is None


async def test_storage_failure_stops_without_claiming_prior_action_was_rolled_back(world):
    with SessionLocal() as db:
        db.execute(
            text(
                "CREATE TRIGGER conversation_receipt_failure BEFORE UPDATE OF runtime_operation_id "
                "ON tool_audits WHEN NEW.action_index = 1 "
                "BEGIN SELECT RAISE(ABORT, 'private-storage-error'); END"
            )
        )
        db.commit()
    request = decision(world)
    request["decision"]["actions"].append(
        {
            "tool": "create_task",
            "args": {"project_id": world.project.id, "title": "Rolled back"},
        }
    )
    adapter = ScriptedAdapter(request, finish())
    try:
        result = await run(world, adapter)
        assert result["reason"] == "storage_failed"
        error = result["turns"][0]["error"]
        assert error["operation_id"] is not None
        assert len(adapter.prompts) == 1
        with SessionLocal() as db:
            assert db.scalar(select(Task).where(Task.title == "Model chosen task")) is not None
            assert db.scalar(select(Task).where(Task.title == "Rolled back")) is None
            assert (
                db.scalar(
                    select(ToolAudit).where(ToolAudit.runtime_operation_id == error["operation_id"])
                ).outcome
                == "applied"
            )
        assert "private-storage-error" not in json.dumps(result)
    finally:
        with SessionLocal() as db:
            db.execute(text("DROP TRIGGER conversation_receipt_failure"))
            db.commit()
