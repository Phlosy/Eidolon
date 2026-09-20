"""U11: actual Manager discovery/proposal, Owner approval and real hire provisioning.

This is not the U29 recruit-and-deliver path: no Worker task or Reviewer is run.
The shared U8 fixtures only route the isolated relay and pin the runtime image.
"""

import asyncio
import json
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from test_m3_real_management_sessions import (  # noqa: F401
    docker_service,
    host_relay_access,
    make_employee,
    relay_routes,
)
from test_m3_real_management_sessions import (
    manager as manager_fixture,
)
from test_m3_real_management_sessions import (
    ready_runtime as ready_runtime_fixture,
)

from app.core.config import settings
from app.economy.contracts import EconomicActor
from app.models.action_confirmation import ActionConfirmation
from app.models.auth import CompanyMembership, User, UserSession
from app.models.base import utcnow
from app.models.cultivation import CharacterProfile
from app.models.decision import DecisionRecord, ToolAudit
from app.models.event import Event
from app.models.lifecycle import ProvisioningJob
from app.models.organization import Employee
from app.models.position import PositionSlot
from app.models.project import Project, WorkSession
from app.repositories import market, persons, providers
from app.repositories import runtimes as runtime_repo
from app.runtimes.gateway import gateway
from app.schemas.position import AssignmentIn
from app.schemas.runtime import RuntimeModelProbeRequest
from app.services import position_service
from app.services.economy.monetary import MonetaryAuthority
from app.services.economy.talent_trade import TalentTradeService
from app.work import (
    action_confirmations,
    authority,
    contracts,
    management_runner,
    readiness,
    runtime_provisioning,
)
from app.work.model_validation import validate_model
from app.work.runtime_conversation import ConversationLimits
from app.work.runtime_reconciliation import probe_instance

pytestmark = pytest.mark.integration
manager = manager_fixture
ready_runtime = ready_runtime_fixture


@pytest.mark.parametrize("purpose,runtime_type", [("management", "hermes")])
@pytest.mark.parametrize("model_relay", [{"max_requests": 8, "output_limit": 768}], indirect=True)
async def test_real_manager_recruits_selected_person_after_confirmation(
    purpose, runtime_type, idb, ready_runtime, model_relay, manager, wait_control, monkeypatch
):
    monkeypatch.setattr(settings, "runtime_mode", "auto")
    monkeypatch.setattr(settings, "allow_planning_fixtures", False)
    monkeypatch.setattr("app.runtimes.manager.docker_manager._manager", manager)
    monkeypatch.setattr(runtime_provisioning, "SessionLocal", idb)
    employee = ready_runtime.employee
    marker = uuid.uuid4().hex[:12]
    with idb() as db:
        actor = authority.resolve_actor_authority(db, employee.id)
        for kind in (contracts.AuthorityKind.approve_hiring, contracts.AuthorityKind.spend_credits):
            authority.grant_authority(
                db,
                position_definition_id=actor.position_definition_id,
                kind=kind,
                max_amount=2000 if kind is contracts.AuthorityKind.spend_credits else None,
                commit=False,
            )
        person = persons.create_person(db, slug=f"candidate-{marker}", name=f"Engineer {marker}")
        db.add(
            CharacterProfile(
                person_id=person.id,
                identity_id=f"EU-U11-{marker}",
                origin="blank",
                lifecycle="ready",
            )
        )
        participant = market.ensure_participant(
            db, kind="system_issuer", company_id=None, display_name="Isolated issuer"
        )
        listing, _ = market.create_active_listing(
            db, person_id=person.id, listed_by_participant_id=participant.id
        )
        TalentTradeService(db).set_terms(listing.id, company_id=None, price=1200, commit=False)
        MonetaryAuthority(db).mint(
            actor=EconomicActor.company(employee.company_id),
            amount=5000,
            reason="U11 isolated test funding",
            commit=False,
        )
        binding = providers.get_primary_binding(db, employee.id)
        readiness.set_company_runtime_defaults(
            db,
            employee.company_id,
            {
                "runtime_type": runtime_type,
                "deployment_mode": "docker",
                "provider_id": binding.provider_id,
                "model": model_relay.model,
            },
            commit=False,
        )
        project = Project(
            company_id=employee.company_id,
            name="Confirmed market hiring",
            status="in_progress",
            work_mode="managed",
            planning_fixture="none",
            management_employee_id=employee.id,
            management_person_id=employee.person_id,
            goal=(
                f"Find the market candidate whose name contains {marker}. Read search_market, "
                "then inspect_candidate for its actual commercial_terms. Propose hiring that "
                "candidate by submitting one recruit decision with one recruit_from_market "
                "action at the exact listed price. Wait for human confirmation; do not claim "
                "hiring already succeeded. Once you receive confirmation_pending, finish with "
                "summary exactly confirmation=<confirmation id>;audit=<proposal audit id>."
            ),
        )
        db.add(project)
        db.flush()
        source = Event(
            company_id=employee.company_id,
            project_id=project.id,
            type="project.replan_required",
            payload={"project_id": project.id},
        )
        db.add(source)
        db.commit()
        person_id, listing_id, source_id = person.id, listing.id, source.id

    transcript = []
    adapter = gateway.adapter_for(runtime_type)
    complete = adapter.complete_turn

    async def record(session, prompt, *, request_id, timeout):
        reply = await complete(session, prompt, request_id=request_id, timeout=timeout)
        transcript.append(
            {
                "request_id": request_id,
                "run_id": reply.run_id,
                "response_text": reply.text,
                "feedback": json.loads(prompt).get("feedback"),
            }
        )
        return reply

    monkeypatch.setattr(adapter, "complete_turn", record)

    async def budget_available(event_id):
        assert event_id == source_id
        return json.loads(model_relay.receipt.read_text())["attempts"] < 7

    async def budget_check(work_id, request_id):
        return await budget_available(source_id)

    runner = management_runner.ManagementRunner(
        session_factory=idb,
        enabled=True,
        budget_available=budget_available,
        budget_check=budget_check,
        limits=ConversationLimits(
            max_rounds=6, turn_timeout_seconds=60, total_timeout_seconds=210, max_actions=6
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
        print("REAL_RECRUITMENT_TRANSCRIPT=" + json.dumps(transcript, default=str))

    with idb() as db:
        work = db.get(WorkSession, work.id)
        assert work.status == "completed", {"status": work.status, "error": work.error}
        rows = list(db.scalars(select(ActionConfirmation)))
        assert len(rows) == 1 and rows[0].status == "pending"
        row = rows[0]
        assert row.arguments_json == dict(project_id=project.id, listing_id=listing_id, amount=1200)
        assert row.person_id == employee.person_id and row.employee_id == employee.id
        assert db.get(DecisionRecord, row.decision_id).status == "EXECUTING"
        assert not db.scalar(select(Employee).where(Employee.person_id == person_id))
        audits = list(db.scalars(select(ToolAudit).order_by(ToolAudit.id)))
        proposal = db.get(ToolAudit, row.proposal_audit_id)
        for tool in ("inspect_project", "search_market", "inspect_candidate"):
            assert any(
                a.tool_name == tool
                and a.outcome == "read"
                and a.id < proposal.id
                and a.runtime_operation_id < proposal.runtime_operation_id
                for a in audits
            )
        assert work.summary == f"confirmation={row.id};audit={proposal.id}"
        owner = User(
            email="owner@isolated.invalid",
            username="isolated-owner",
            password_hash="test-identity-not-a-login",
            status="active",
        )
        db.add(owner)
        db.flush()
        session = UserSession(
            user_id=owner.id,
            token_hash=uuid.uuid4().hex,
            csrf_token=uuid.uuid4().hex,
            expires_at=utcnow() + timedelta(hours=1),
            last_seen_at=utcnow(),
        )
        db.add_all(
            [
                session,
                CompanyMembership(user_id=owner.id, company_id=employee.company_id, role="OWNER"),
            ]
        )
        db.commit()
        identity = dict(company_id=employee.company_id, user_id=owner.id, session_id=session.id)
        action_confirmations.review(db, row.id, approve=True, **identity)
        row = action_confirmations.execute(db, row.id, **identity)
        assert row.status == "executed", action_confirmations.view(db, row)
        hired = db.scalar(select(Employee).where(Employee.person_id == person_id))
        result = db.get(ToolAudit, row.final_audit_id).result_json
        job_id = result["provisioning"]["job_id"]
        assert result["provisioning"]["job_status"] == "pending"
        assert result["trade"]["gross"] == 1200
        assert (
            db.scalar(
                select(func.count()).select_from(Employee).where(Employee.person_id == person_id)
            )
            == 1
        )
        # An explicit human assignment supplies the optional position fact. This
        # test never attributes choosing a position or assigning work to the model.
        actor = authority.resolve_actor_authority(db, employee.id)
        slot = PositionSlot(
            company_id=employee.company_id,
            department_id=employee.department_id,
            position_definition_id=actor.position_definition_id,
            administrative_status="active",
            headcount_index=2,
            slot_code="u11-explicit-owner-assignment",
        )
        db.add(slot)
        db.flush()
        position_service.assign_position(
            db,
            hired,
            AssignmentIn(
                slot_id=slot.id,
                reason="Explicit Owner assignment for isolated readiness acceptance",
                assigned_by=owner.id,
            ),
        )
        hired_id = hired.id
        confirmation = action_confirmations.view(db, row)
        provenance = work.provenance_snapshot_json

    assert await runtime_provisioning.run_job(job_id)
    with idb() as db:
        job = db.get(ProvisioningJob, job_id)
        assert job.status == "done", readiness.orchestration_result(db, job).as_dict()
        instance = runtime_repo.get_instance_for_employee(db, hired_id)
        token = manager._secrets.retrieve(db, instance.metadata_json["api_key_ref"])
        device = manager._secrets.retrieve(db, instance.metadata_json.get("device_key_ref"))
    await wait_control(manager, instance, token, device)
    assert await probe_instance(manager, instance.id)
    probe = await validate_model(
        manager,
        instance.id,
        RuntimeModelProbeRequest(
            allow_paid=True,
            idempotency_key="u11-new-hire-model",
            max_cost_usd=Decimal("0.005"),
            max_output_tokens=32,
        ),
    )
    assert probe["state"] == "passed", probe
    with idb() as db:
        report = readiness.readiness_report(db, db.get(Employee, hired_id)).as_dict()
        assert report["ready_to_work"], report
    print(
        "REAL_RECRUITMENT_EVIDENCE="
        + json.dumps(
            {
                "runtime": runtime_type,
                "manager_image": ready_runtime.instance.image_digest,
                "manager_version": ready_runtime.instance.runtime_version,
                "manager_provenance": provenance,
                "confirmation": confirmation,
                "person_id": person_id,
                "employee_id": hired_id,
                "readiness": report,
                "new_hire_probe": probe,
                "new_hire_image": instance.image_digest,
                "model_receipt": json.loads(model_relay.receipt.read_text()),
            },
            default=str,
            sort_keys=True,
        )
    )
