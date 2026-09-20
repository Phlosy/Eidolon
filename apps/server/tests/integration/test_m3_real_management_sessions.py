"""U8 real source-event -> ready runtime -> decision/verdict -> receipt proof.

The selected-model probe and business turns share the same finite real relay
allowance. Only the owned relay's network address is translated for host probes;
no readiness result, model reply, decision or verdict is substituted.
"""

import asyncio
import json
import socket
import uuid
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.core.redaction import redact
from app.models.decision import DecisionRecord, ToolAudit
from app.models.event import Event
from app.models.organization import Department, Employee
from app.models.position import PositionAssignment, PositionDefinition, PositionSlot
from app.models.project import Project, Task, WorkSession
from app.models.provider import Provider
from app.models.review import ReviewRequest
from app.models.runtime import RuntimeInstance as RuntimeRow
from app.providers.secrets.store import get_secret_store
from app.repositories import persons, providers
from app.repositories import runtimes as runtime_repo
from app.runtimes.gateway import gateway
from app.runtimes.manager.docker_manager import DockerRuntimeInstanceManager
from app.schemas.runtime import RuntimeModelProbeRequest
from app.services import artifacts
from app.work import authority, contracts, readiness, reviews
from app.work.model_validation import validate_model
from app.work.runtime_conversation import ConversationLimits
from app.work.runtime_reconciliation import probe_instance

pytestmark = pytest.mark.integration

# Freeze these acceptance environments independently of mutable image tags.
FIXED_IMAGES = {
    "hermes": "sha256:33a938c54ec8f58cd3128901b378c226b2846fde5f217533bdae2b1501bd26e5",
    "openclaw": "sha256:e7849cb6c1ef1ead39ab4be7d85edb2df89611f486e283284c7cf35ce39a20d4",
}


@pytest.fixture()
def relay_routes():
    return {}


@pytest.fixture()
def docker_service(docker_service, monkeypatch, relay_routes):
    create = docker_service.create_container

    def with_relay_loopback(**kwargs):
        if (kwargs.get("labels") or {}).get("eidolon.test") == "model-budget-relay":
            for port in range(
                settings.runtime_debug_port_end, settings.runtime_debug_port_start - 1, -1
            ):
                if port in {26922, 26990}:
                    continue
                with socket.socket() as candidate:
                    try:
                        candidate.bind(("127.0.0.1", port))
                    except OSError:
                        continue
                kwargs["ports"] = {"8642/tcp": ("127.0.0.1", port)}
                relay_routes[kwargs["name"]] = port
                break
            else:
                pytest.fail("No free reserved loopback port for the isolated budget relay")
        return create(**kwargs)

    monkeypatch.setattr(docker_service, "create_container", with_relay_loopback)
    return docker_service


class RelayLoopbackTransport(httpx.AsyncBaseTransport):
    def __init__(self, routes):
        self.routes, self.transport = routes, httpx.AsyncHTTPTransport(retries=0)

    async def handle_async_request(self, request):
        port = self.routes.get(request.url.host)
        if port is not None and request.url.scheme == "http" and request.url.port == 8642:
            request.url = request.url.copy_with(host="127.0.0.1", port=port)
        return await self.transport.handle_async_request(request)

    async def aclose(self):
        await self.transport.aclose()


@pytest.fixture()
def host_relay_access(monkeypatch, relay_routes):
    client = httpx.AsyncClient

    def routed_client(*args, **kwargs):
        if "transport" not in kwargs:
            kwargs["transport"] = RelayLoopbackTransport(relay_routes)
        return client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", routed_client)


@pytest.fixture()
def manager(docker_service, monkeypatch, runtime_type):
    image = FIXED_IMAGES[runtime_type]
    if docker_service.inspect_image(image) is None:
        pytest.skip(f"required fixed {runtime_type} image is unavailable: {image}")
    monkeypatch.setattr(settings, f"{runtime_type}_image", image)
    return DockerRuntimeInstanceManager(docker=docker_service)


@pytest.fixture()
def make_employee(make_employee, idb, purpose, runtime_type):
    def create():
        original = make_employee()
        with idb() as db:
            employee = db.get(Employee, original.id)
            person = persons.create_person(db, slug=employee.slug, name=employee.name)
            department = Department(
                company_id=employee.company_id, name="Delivery", slug="delivery"
            )
            position = PositionDefinition(
                company_id=employee.company_id,
                code=f"u8-{purpose}",
                name=f"Project {purpose}",
                job_family="management",
                responsibilities=[
                    "Read the bound work facts, decide within authority, and acknowledge results."
                ],
            )
            db.add_all([department, position])
            db.flush()
            employee.person_id = person.id
            employee.department_id = department.id
            employee.runtime_type = runtime_type
            slot = PositionSlot(
                company_id=employee.company_id,
                department_id=department.id,
                position_definition_id=position.id,
                administrative_status="active",
                slot_code=f"u8-{purpose}-1",
            )
            db.add(slot)
            db.flush()
            db.add(
                PositionAssignment(
                    employee_id=employee.id,
                    department_id=department.id,
                    position_slot_id=slot.id,
                    employment_status="active",
                    assignment_type="primary",
                    is_primary=True,
                    position_title_snapshot=position.name,
                )
            )
            authority.grant_authority(
                db,
                position_definition_id=position.id,
                kind=(
                    contracts.AuthorityKind.accept_delivery
                    if purpose == "review"
                    else contracts.AuthorityKind.plan_project_work
                ),
                commit=False,
            )
            db.commit()
            return employee

    return create


@pytest.fixture()
async def ready_runtime(
    idb, make_employee, manager, model_relay, wait_control, host_relay_access, runtime_type
):
    employee = make_employee()
    budget = json.loads(model_relay.receipt.read_text())
    with idb() as db:
        provider = Provider(
            company_id=employee.company_id,
            name="U8 isolated budget relay",
            provider_type="custom",
            base_url=model_relay.url,
            scope="company",
            enabled=True,
            credential_ref=get_secret_store().store(db, model_relay.token),
            metadata_json={
                "probe_pricing": {
                    model_relay.model: {
                        "input_usd_per_million": budget["input_price_usd_per_million"],
                        "output_usd_per_million": budget["output_price_usd_per_million"],
                        "valid_until": budget["pricing_valid_until"],
                        "source": "https://api-docs.deepseek.com/quick_start/pricing/",
                    }
                }
            },
        )
        db.add(provider)
        db.flush()
        binding = providers.create_binding(
            db,
            employee_id=employee.id,
            provider_id=provider.id,
            model=model_relay.model,
            is_primary=True,
        )
        runtime_repo.create_instance(
            db,
            employee_id=employee.id,
            runtime_type=runtime_type,
            deployment_mode="docker",
            model_binding_id=binding.id,
        )
        db.commit()
    instance = await manager.ensure_instance(
        employee.id,
        runtime_type=runtime_type,
        deployment_mode="docker",
        provider_id=provider.id,
        model=model_relay.model,
    )
    with idb() as db:
        token = manager._secrets.retrieve(db, instance.metadata_json["api_key_ref"])
        device = manager._secrets.retrieve(db, instance.metadata_json.get("device_key_ref"))
    await wait_control(manager, instance, token, device)
    assert await probe_instance(manager, instance.id)
    probe_receipt = await validate_model(
        manager,
        instance.id,
        RuntimeModelProbeRequest(
            allow_paid=True,
            idempotency_key="u8-real-selected-model",
            max_cost_usd=Decimal("0.005"),
            max_output_tokens=32,
        ),
    )
    assert probe_receipt["state"] == "passed", probe_receipt
    with idb() as db:
        report = readiness.readiness_report(db, db.get(Employee, employee.id))
        assert not report.blocking_gaps, report.as_dict()
        actual = db.get(RuntimeRow, instance.id)
    return SimpleNamespace(
        instance=actual,
        employee=employee,
        readiness=report.as_dict(),
        probe_receipt=probe_receipt,
    )


@pytest.mark.parametrize("purpose,runtime_type", [("management", "hermes"), ("review", "openclaw")])
@pytest.mark.parametrize("model_relay", [{"max_requests": 5, "output_limit": 768}], indirect=True)
async def test_persisted_source_wakes_real_controller_and_delivers_actual_receipt(
    purpose, runtime_type, idb, ready_runtime, model_relay, monkeypatch
):
    from app.work import management_dispatch, management_runner

    monkeypatch.setattr(settings, "runtime_mode", "auto")
    monkeypatch.setattr(settings, "allow_planning_fixtures", False)
    employee = ready_runtime.employee
    marker = uuid.uuid4().hex[:12]
    with idb() as db:
        project = Project(
            company_id=employee.company_id,
            name="Source-driven contributor handoff",
            status="in_progress",
            work_mode="managed",
            planning_fixture="none",
            management_employee_id=employee.id,
            management_person_id=employee.person_id,
            goal=(
                "Create one concise contributor onboarding runbook task. Its title must include "
                f"acceptance marker {marker}. Submit one decompose_project decision with one "
                "create_task action, with description and acceptance_criteria; "
                "leave it unassigned. "
                "After receiving the applied result, finish with summary exactly "
                "task=<created task id>;audit=<create_task audit id>."
            ),
        )
        db.add(project)
        db.flush()
        if purpose == "management":
            source = Event(
                company_id=employee.company_id,
                project_id=project.id,
                type="project.replan_required",
                payload={"project_id": project.id},
            )
            db.add(source)
            db.commit()
            source_id, review_id, reviewed_task_id = source.id, None, None
        else:
            task = Task(
                project_id=project.id,
                title="Prepared contributor runbook",
                status="in_review",
                acceptance_criteria=(
                    f"A source_code artifact titled Runbook {marker} must exist. "
                    f"Include acceptance marker {marker} in the verdict notes."
                ),
                produces_json=["source_code"],
            )
            db.add(task)
            db.flush()
            artifacts.record_project_artifact(
                db,
                project,
                artifact_type="source_code",
                title=f"Runbook {marker}",
                content="Install the pinned dependencies, run the test suite, and open a review.",
                task_id=task.id,
            )
            request = reviews.open_review_request(
                db,
                task=task,
                requester_employee_id=employee.id,
                reviewer_employee_id=employee.id,
                reason=(
                    "Inspect the task review facts and decide whether the exact declared "
                    "artifact exists. "
                    "Submit one accept_delivery decision with one submit_review_verdict action. "
                    "After receiving the actual result, finish with summary exactly "
                    "review=<review request id>;audit=<submit_review_verdict audit id>."
                ),
            )
            source_id = db.scalar(
                select(Event.id).where(
                    Event.type == "task.review_named",
                    Event.company_id == employee.company_id,
                    Event.project_id == project.id,
                    Event.task_id == task.id,
                )
            )
            review_id, reviewed_task_id = request.id, task.id

    transcript = []
    turn_errors = []
    adapter = gateway.adapter_for(runtime_type)
    complete_turn = adapter.complete_turn

    async def recorded_turn(session, prompt, *, request_id, timeout):
        try:
            reply = await complete_turn(session, prompt, request_id=request_id, timeout=timeout)
        except Exception as exc:
            turn_errors.append(
                {
                    "request_id": request_id,
                    "kind": type(exc).__name__,
                    "reason": redact(str(exc))[:256],
                }
            )
            raise
        transcript.append(
            {
                "request_id": request_id,
                "run_id": reply.run_id,
                "response_text": reply.text,
                "feedback": json.loads(prompt).get("feedback"),
            }
        )
        return reply

    monkeypatch.setattr(adapter, "complete_turn", recorded_turn)
    admissions = []

    async def budget_available(source_event_id):
        assert source_event_id == source_id
        receipt = json.loads(model_relay.receipt.read_text())
        return receipt["attempts"] < model_relay.max_requests

    async def budget_check(work_session_id, request_id):
        with idb() as db:
            work = db.get(WorkSession, work_session_id)
            assert work.employee_id == employee.id and work.purpose == purpose
        admissions.append(request_id)
        return await budget_available(source_id)

    runner = management_runner.ManagementRunner(
        session_factory=idb,
        enabled=True,
        budget_available=budget_available,
        budget_check=budget_check,
        limits=ConversationLimits(
            max_rounds=4, turn_timeout_seconds=60, total_timeout_seconds=210, max_actions=4
        ),
    )
    runner.start()
    try:
        async with asyncio.timeout(240):
            while True:
                with idb() as db:
                    work = db.scalar(
                        select(WorkSession).where(WorkSession.employee_id == employee.id)
                    )
                    if work is not None and work.ended_at is not None:
                        break
                await asyncio.sleep(0.1)
    finally:
        await runner.stop()

    receipt = json.loads(model_relay.receipt.read_text())
    with idb() as db:
        work = db.get(WorkSession, work.id)
        audits = list(
            db.scalars(
                select(ToolAudit).where(ToolAudit.work_session_id == work.id).order_by(ToolAudit.id)
            )
        )
        decisions = list(db.scalars(select(DecisionRecord)))
        dispatches = management_dispatch.list_dispatches(
            db, company_id=employee.company_id, project_id=project.id
        )
        persisted = {
            "work": {
                "id": work.id,
                "purpose": work.purpose,
                "task_id": work.task_id,
                "project_id": work.project_id,
                "review_request_id": work.review_request_id,
                "status": work.status,
                "summary": work.summary,
                "error": work.error,
                "provenance": work.provenance_snapshot_json,
            },
            "dispatches": dispatches,
            "decisions": [
                {"id": d.id, "status": d.status, "actor_person_id": d.actor_person_id}
                for d in decisions
            ],
            "audits": [
                {
                    "id": a.id,
                    "tool": a.tool_name,
                    "outcome": a.outcome,
                    "operation_id": a.runtime_operation_id,
                    "decision_id": a.decision_id,
                    "actor_person_id": a.actor_person_id,
                    "result": a.result_json,
                }
                for a in audits
            ],
        }
        print(
            "REAL_MANAGEMENT_SESSION_EVIDENCE="
            + json.dumps(
                {
                    "purpose": purpose,
                    "runtime": runtime_type,
                    "image_id": ready_runtime.instance.image_digest,
                    "version": ready_runtime.instance.runtime_version,
                    "source_event_id": source_id,
                    "readiness": ready_runtime.readiness,
                    "model_probe": ready_runtime.probe_receipt,
                    "transcript": transcript,
                    "turn_errors": turn_errors,
                    "persisted": persisted,
                    "model_receipt": receipt,
                },
                sort_keys=True,
                default=str,
            )
        )
        assert work.status == "completed", persisted
        assert work.task_id is None and work.project_id == project.id
        assert work.review_request_id == review_id
        source_dispatch = next(d for d in dispatches if d["source_event_id"] == source_id)
        assert source_dispatch["work_session_id"] == work.id
        assert source_dispatch["purpose"] == purpose
        assert source_dispatch["recipient_employee_id"] == employee.id
        assert source_dispatch["work_session"]["status"] == "completed"
        assert source_dispatch["waiting_reason"] is None
        assert source_dispatch["resolution"] is None
        assert db.scalar(select(func.count()).select_from(WorkSession)) == 1
        assert len(decisions) == 1 and decisions[0].status == "APPLIED"
        assert all(a.actor_person_id == employee.person_id for a in audits)
        write = next(a for a in audits if a.outcome == "applied")
        assert write.decision_id == decisions[0].id and write.runtime_operation_id is not None
        if purpose == "management":
            task = db.scalar(select(Task))
            assert task.status == "backlog" and task.assignee_id is None and marker in task.title
            assert work.summary == f"task={task.id};audit={write.id}"
            assert [a.tool_name for a in audits] == ["inspect_project", "create_task"]
        else:
            request = db.get(ReviewRequest, review_id)
            assert request.verdict == "PASS" and marker in request.verdict_notes
            assert db.get(Task, reviewed_task_id).status == "done"
            assert work.summary == f"review={review_id};audit={write.id}"
            assert [a.tool_name for a in audits] == ["inspect_task_review", "submit_review_verdict"]
        assert transcript[-1]["feedback"]["result"]["results"][0]["audit_id"] == write.id
    assert len(set(admissions)) == len(admissions) == len(transcript)
    assert 4 <= receipt["attempts"] <= 5  # one readiness probe plus actual business turns
    assert all(row["state"] == "settled" for row in receipt["requests"])
    assert Decimal(receipt["reserved_cost_usd"]) == 0
    assert Decimal(receipt["cost_usd"]) <= Decimal(receipt["max_cost_usd"])
