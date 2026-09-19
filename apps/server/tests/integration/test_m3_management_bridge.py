"""Fixed-image, budgeted protocol and real business bridge acceptance."""

import json
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.base import utcnow
from app.models.decision import DecisionRecord, ToolAudit
from app.models.organization import Department, Employee
from app.models.position import PositionAssignment, PositionDefinition, PositionSlot
from app.models.project import Project, Task, WorkSession
from app.models.provider import Provider
from app.repositories import persons
from app.repositories import project as project_repo
from app.runtimes.base import RuntimeInstance, TaskContext
from app.runtimes.container_common import resolve_container_instance
from app.runtimes.hermes.adapter import HermesAdapter
from app.runtimes.manager.docker_manager import DockerRuntimeInstanceManager
from app.runtimes.openclaw.adapter import OpenClawAdapter
from app.work import authority, contracts

pytestmark = pytest.mark.integration

FIXED_IMAGES = {
    "hermes": "sha256:33a938c54ec8f58cd3128901b378c226b2846fde5f217533bdae2b1501bd26e5",
    "openclaw": "sha256:e7849cb6c1ef1ead39ab4be7d85edb2df89611f486e283284c7cf35ce39a20d4",
}


@pytest.fixture()
def manager(docker_service, monkeypatch, runtime_type):
    image = FIXED_IMAGES[runtime_type]
    if docker_service.inspect_image(image) is None:
        pytest.skip(f"required fixed {runtime_type} image is unavailable: {image}")
    monkeypatch.setattr(settings, f"{runtime_type}_image", image)
    return DockerRuntimeInstanceManager(docker=docker_service)


@pytest.mark.parametrize("runtime_type", ["hermes", "openclaw"])
@pytest.mark.parametrize("model_relay", [{"max_requests": 2, "output_limit": 128}], indirect=True)
async def test_fixed_image_complete_response_and_conversation_continuation(
    runtime_type, manager, create_model_runtime, model_relay
):
    runtime = await create_model_runtime(runtime_type, manager)
    adapter = {"hermes": HermesAdapter, "openclaw": OpenClawAdapter}[runtime_type]()
    connection = RuntimeInstance(
        employee_id=runtime.instance.employee_id,
        profile="real-protocol-probe",
        home_path=runtime.instance.workspace_path,
        details={
            "base_url": runtime.base_url,
            "api_key": runtime.token,
            "device_key": runtime.device,
        },
    )
    # Transport characterization has no business WorkSession or business mutation.
    current = await adapter.create_session(
        connection, TaskContext(task_id=0, project_id=0, title="protocol", kind="general")
    )
    nonce = uuid.uuid4().hex[:12]
    first = await adapter.complete_turn(
        current,
        "Protocol check. Do not use tools. Return only a JSON object with exactly "
        f'these fields: {{"kind":"read","nonce":"{nonce}"}}. Remember the nonce for the next turn.',
        request_id="protocol-first",
        timeout=50,
    )
    assert json.loads(first.text) == {"kind": "read", "nonce": nonce}
    second = await adapter.complete_turn(
        current,
        'The read receipt is {"ok":true,"fact":"RECEIPT_ACCEPTED"}. Return only a JSON object '
        'with exactly kind="finish", nonce=the nonce from the previous turn, '
        "receipt=the fact in this receipt. Do not use tools.",
        request_id="protocol-second",
        timeout=50,
    )
    assert json.loads(second.text) == {
        "kind": "finish",
        "nonce": nonce,
        "receipt": "RECEIPT_ACCEPTED",
    }
    assert first.session_id == second.session_id == current.id
    assert first.run_id != second.run_id
    receipt = json.loads(model_relay.receipt.read_text())
    assert receipt["attempts"] == 2
    assert receipt["cost_usd"] is not None
    assert Decimal(receipt["cost_usd"]) <= Decimal(receipt["max_cost_usd"])
    print(
        "REAL_PROTOCOL_EVIDENCE="
        + json.dumps(
            {
                "runtime": runtime_type,
                "image_id": runtime.instance.image_digest,
                "version": runtime.instance.runtime_version,
                "session_id": current.id,
                "run_ids": [first.run_id, second.run_id],
                "responses": [json.loads(first.text), json.loads(second.text)],
                "model_receipt": receipt,
            },
            sort_keys=True,
        )
    )


class TestRealBusinessBridge:
    @pytest.fixture()
    def make_employee(self, make_employee, idb):
        """Give the test actor its identity and authority before provisioning."""

        def create():
            employee = make_employee()
            with idb() as db:
                employee = db.get(Employee, employee.id)
                person = persons.create_person(db, slug=employee.slug, name=employee.name)
                department = Department(
                    company_id=employee.company_id, name="Project planning", slug="planning"
                )
                position = PositionDefinition(
                    company_id=employee.company_id,
                    code="project-planner",
                    name="Project planner",
                    job_family="management",
                    responsibilities=["Read project facts and plan concrete next tasks."],
                )
                db.add_all([department, position])
                db.flush()
                employee.person_id = person.id
                employee.department_id = department.id
                slot = PositionSlot(
                    company_id=employee.company_id,
                    department_id=department.id,
                    position_definition_id=position.id,
                    administrative_status="active",
                    slot_code="project-planner-1",
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
                    kind=contracts.AuthorityKind.plan_project_work,
                    commit=False,
                )
                db.commit()
                return employee

        return create

    @pytest.mark.parametrize("runtime_type", ["hermes", "openclaw"])
    @pytest.mark.parametrize(
        "model_relay", [{"max_requests": 4, "output_limit": 768}], indirect=True
    )
    async def test_model_reads_decides_and_acknowledges_actual_business_receipt(
        self, runtime_type, manager, create_model_runtime, model_relay, idb, monkeypatch
    ):
        from app.work.runtime_conversation import ConversationLimits, run_conversation

        monkeypatch.setattr(settings, "allow_planning_fixtures", False)
        runtime = await create_model_runtime(runtime_type, manager)
        marker = uuid.uuid4().hex[:12]
        with idb() as db:
            employee = db.get(Employee, runtime.instance.employee_id)
            provider = db.scalar(select(Provider).where(Provider.company_id == employee.company_id))
            project = Project(
                company_id=employee.company_id,
                name="Contributor onboarding",
                goal=(
                    "Create a concise onboarding runbook for contributors. "
                    f"The next task title must include acceptance marker {marker}."
                ),
                status="in_progress",
                work_mode="managed",
                planning_fixture="none",
            )
            db.add(project)
            db.flush()
            task = Task(
                project_id=project.id,
                title="Plan the onboarding handoff",
                assignee_id=employee.id,
                status="in_progress",
            )
            db.add(task)
            db.flush()
            work = project_repo.create_work_session(
                db,
                task_id=task.id,
                employee_id=employee.id,
                runtime_type=runtime_type,
                runtime_instance_id=runtime.instance.id,
                provider_id=provider.id,
                model=model_relay.model,
                started_at=utcnow(),
            )
            db.commit()

        adapter = {"hermes": HermesAdapter, "openclaw": OpenClawAdapter}[runtime_type]()
        connection = resolve_container_instance(employee.id, runtime_type, session_factory=idb)
        current = await adapter.create_session(
            connection,
            TaskContext(task_id=task.id, project_id=project.id, title=task.title, kind=task.kind),
        )
        transcript = []
        complete_turn = adapter.complete_turn

        async def record_turn(session, prompt, *, request_id, timeout):
            reply = await complete_turn(session, prompt, request_id=request_id, timeout=timeout)
            transcript.append(
                {
                    "request_id": request_id,
                    "run_id": reply.run_id,
                    "response_text": reply.text,
                    "feedback": json.loads(prompt).get("feedback"),
                }
            )
            return reply

        monkeypatch.setattr(adapter, "complete_turn", record_turn)
        admissions = []

        async def budget_check(work_session_id, request_id):
            assert work_session_id == work.id
            admissions.append(request_id)
            # This is only host admission. The separate relay reserves money
            # before every upstream call, including native retries/auxiliary calls.
            receipt = json.loads(model_relay.receipt.read_text())
            return receipt["attempts"] < model_relay.max_requests

        result = await run_conversation(
            work.id,
            adapter=adapter,
            runtime_session=current,
            goal=(
                "Use only the host JSON reply protocol, never native tools. First request "
                "inspect_project for the project_id in your scope. After reading its actual "
                "spec.goal, decide one concrete next task for that goal and include the "
                "acceptance marker from the goal in its title. Submit exactly one "
                "decompose_project decision with one create_task action, specifying its "
                "description and acceptance_criteria. Do not assign or execute the new task. "
                "After receiving the actual successful create_task receipt, finish with "
                "summary exactly task=<created task id>;audit=<create_task audit id>. "
                "Use a distinct operation_key for each new operation. Keep all JSON concise."
            ),
            budget_check=budget_check,
            limits=ConversationLimits(
                max_rounds=4,
                turn_timeout_seconds=60,
                total_timeout_seconds=210,
                max_actions=4,
            ),
            session_factory=idb,
        )
        receipt = json.loads(model_relay.receipt.read_text())
        with idb() as db:
            audits = list(
                db.scalars(
                    select(ToolAudit)
                    .where(ToolAudit.work_session_id == work.id)
                    .order_by(ToolAudit.id)
                )
            )
            decisions = list(db.scalars(select(DecisionRecord)))
            tasks = list(db.scalars(select(Task).where(Task.id != task.id)))
            persisted = {
                "audits": [
                    {
                        "id": item.id,
                        "tool_name": item.tool_name,
                        "operation_id": item.runtime_operation_id,
                        "decision_id": item.decision_id,
                        "actor_person_id": item.actor_person_id,
                        "work_session_id": item.work_session_id,
                        "outcome": item.outcome,
                        "result": item.result_json,
                    }
                    for item in audits
                ],
                "decisions": [
                    {"id": item.id, "status": item.status, "reason": item.reason}
                    for item in decisions
                ],
                "tasks": [
                    {"id": item.id, "title": item.title, "status": item.status} for item in tasks
                ],
            }
            print(
                "REAL_BUSINESS_BRIDGE_EVIDENCE="
                + json.dumps(
                    {
                        "runtime": runtime_type,
                        "image_id": runtime.instance.image_digest,
                        "version": runtime.instance.runtime_version,
                        "session_id": current.id,
                        "work_session_id": work.id,
                        "provenance": work.provenance_snapshot_json,
                        "transcript": transcript,
                        "result": result,
                        "persisted": persisted,
                        "model_receipt": receipt,
                    },
                    sort_keys=True,
                )
            )
            assert result["status"] == "finished", result
            assert [item.tool_name for item in audits] == ["inspect_project", "create_task"]
            assert [item.outcome for item in audits] == ["read", "applied"]
            assert all(item.actor_person_id == employee.person_id for item in audits)
            assert len(decisions) == len(tasks) == 1
            assert decisions[0].status == "APPLIED"
            assert decisions[0].actor_employee_id == employee.id
            assert audits[1].decision_id == decisions[0].id
            assert audits[1].result_json["task"]["task_id"] == tasks[0].id
            assert marker in tasks[0].title
            assert result["summary"] == f"task={tasks[0].id};audit={audits[1].id}"
            assert tasks[0].status == "backlog" and tasks[0].assignee_id is None
            assert db.get(WorkSession, work.id).runtime_session_ref == current.id
            assert db.get(WorkSession, work.id).status == "running"
            assert db.get(Task, task.id).status == "in_progress"
            assert db.get(Project, project.id).status == "in_progress"

        # The model can learn the marker only from the actual read. It then
        # acknowledges IDs that existed only after the actual write committed.
        assert transcript[-1]["feedback"]["result"]["results"][0]["audit_id"] == audits[1].id
        assert len(set(item["run_id"] for item in transcript)) == len(transcript)
        assert len(set(admissions)) == len(admissions) == len(transcript)
        assert 3 <= receipt["attempts"] <= 4
        assert all(item["state"] == "settled" for item in receipt["requests"])
        assert Decimal(receipt["reserved_cost_usd"]) == 0
        assert Decimal(receipt["cost_usd"]) <= Decimal(receipt["max_cost_usd"])
