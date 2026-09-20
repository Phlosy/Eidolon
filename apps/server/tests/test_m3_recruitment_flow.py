"""U11 real domain/bridge/confirmation chain; these tests make no paid calls."""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from test_m3_action_confirmations import approve_api, owner
from test_m3_management_runtime import session_for
from test_m3_management_runtime import world as management_world
from test_talent_trade import _fund, _listing_for, _ready_person, _snapshot_t2_assets, _wallet

from app.core.database import SessionLocal
from app.economy.contracts import EconomicActor
from app.economy.policy import economic_policy
from app.models.action_confirmation import ActionConfirmation
from app.models.base import utcnow
from app.models.decision import ToolAudit
from app.models.economy import Contract, Offer
from app.models.event import Event
from app.models.lifecycle import ProvisioningJob
from app.models.organization import Company, Employee
from app.models.position import PositionAuthorityGrant
from app.services.economy.monetary import MonetaryAuthority
from app.services.economy.talent_trade import TalentTradeService
from app.work import action_confirmations as confirmations
from app.work import authority, readiness, runtime_provisioning
from app.work import contracts as C
from app.work.runtime_bridge import handle_reply

world = management_world


@pytest.fixture()
def market(db, world):
    person = _ready_person(db, owner_company=None, name="U11 candidate")
    listing = _listing_for(db, person.id, seller_company=None)
    terms = TalentTradeService(db).set_terms(listing.id, company_id=None, price=1200)
    company = db.get(Company, world.project.company_id)
    account_id = _fund(db, company, 10000)
    position = authority.resolve_actor_authority(db, world.manager.id)
    for kind in (C.AuthorityKind.approve_hiring, C.AuthorityKind.spend_credits):
        authority.grant_authority(
            db,
            position_definition_id=position.position_definition_id,
            kind=kind,
            max_amount=5000 if kind is C.AuthorityKind.spend_credits else None,
        )
    db.commit()
    return SimpleNamespace(person=person, listing=listing, terms=terms, account_id=account_id)


def read(work, name, args, key="read"):
    return handle_reply(
        work.id,
        json.dumps(dict(version=1, kind="read", operation_key=key, tool=name, args=args)),
    )["results"][0]


def request(world, market, *, key="recruit", **changes):
    args = dict(project_id=world.project.id, listing_id=market.listing.id, amount=1200)
    args.update(changes)
    return dict(
        version=1,
        kind="decision",
        operation_key=key,
        decision=dict(
            decision_type="recruit",
            reason="Recruit the selected candidate for this project",
            scope=f"project:{world.project.id}",
            actions=[dict(tool="recruit_from_market", args=args)],
        ),
    )


def propose(db, world, market, **changes):
    work = session_for(db, world, "management")
    payload = request(world, market, **changes)
    result = handle_reply(work.id, json.dumps(payload))
    assert result["results"][0]["reason"] == "confirmation_pending", result
    return work, payload, result["results"][0]["data"]["confirmation"]["id"]


def test_candidate_reads_show_actual_public_commercial_terms(client, db, world, market):
    work = session_for(db, world, "management")
    result = read(work, "inspect_candidate", {"listing_id": market.listing.id})
    assert result["ok"], result
    public = result["data"]["commercial_terms"]
    assert public == {"price": 1200, "currency": "CREDIT", "sale_mode": "buyout"}
    api = client.get(f"/api/v1/market/listings/{market.listing.id}")
    assert api.status_code == 200 and api.json()["commercial_terms"] == public


def test_confirmed_buyout_keeps_person_and_defers_environment(client, db, world, market):
    owner(client, db, world)
    assets = _snapshot_t2_assets(db)
    wallet = _wallet(market.account_id)
    work, payload, row_id = propose(db, world, market)
    assert _wallet(market.account_id) == wallet
    assert not db.scalar(select(Employee).where(Employee.person_id == market.person.id))
    reply = approve_api(client, row_id)
    assert reply.status_code == 200 and reply.json()["status"] == "executed", reply.text
    db.expire_all()
    employee = db.scalar(select(Employee).where(Employee.person_id == market.person.id))
    assert employee.company_id == world.project.company_id
    assert _snapshot_t2_assets(db) == assets
    assert _wallet(market.account_id)["available"] == wallet["available"] - 1200
    row = db.get(ActionConfirmation, row_id)
    job = db.scalar(select(ProvisioningJob).where(ProvisioningJob.employee_id == employee.id))
    assert job.owner == "runtime" and job.status == "pending"
    assert row.final_audit_id is not None
    for kind in ("person.recruited", "talent.purchased"):
        assert db.scalar(select(func.count()).select_from(Event).where(Event.type == kind)) == 1
    final = db.get(ToolAudit, row.final_audit_id)
    assert final.authority_json["authority_kind"] == "approve_hiring"
    assert final.authority_json["additional_checks"][0]["authority_kind"] == "spend_credits"
    assert approve_api(client, row_id).json() == reply.json()
    assert (
        handle_reply(work.id, json.dumps(payload))["results"][0]["reason"] == "confirmation_pending"
    )
    assert _wallet(market.account_id)["available"] == wallet["available"] - 1200


def test_purchase_source_events_share_outer_transaction(db, world, market):
    _, purchase = TalentTradeService(db).create_offer(
        market.listing.id,
        buyer_company_id=world.project.company_id,
        amount=1200,
        commit=False,
    )
    assert purchase is not None
    for kind in ("person.recruited", "talent.purchased"):
        assert db.scalar(select(func.count()).select_from(Event).where(Event.type == kind)) == 1
    db.rollback()
    assert not db.get(Employee, purchase.employee_id)
    for kind in ("person.recruited", "talent.purchased"):
        assert db.scalar(select(func.count()).select_from(Event).where(Event.type == kind)) == 0


@pytest.mark.parametrize("kind", ["approve_hiring", "spend_credits"])
@pytest.mark.parametrize("after_proposal", [False, True])
def test_each_current_authority_is_required(client, db, world, market, kind, after_proposal):
    owner(client, db, world)
    if after_proposal:
        _, _, row_id = propose(db, world, market)
    position = authority.resolve_actor_authority(db, world.manager.id)
    grant = db.scalar(
        select(PositionAuthorityGrant).where(
            PositionAuthorityGrant.position_definition_id == position.position_definition_id,
            PositionAuthorityGrant.authority_kind == kind,
            PositionAuthorityGrant.effective_to.is_(None),
        )
    )
    authority.revoke_authority(db, grant_id=grant.id)
    if after_proposal:
        reply = approve_api(client, row_id).json()
        assert reply["status"] == "failed" and reply["result"]["reason"] == "not_authorized"
    else:
        work = session_for(db, world, "management")
        result = handle_reply(work.id, json.dumps(request(world, market)))["results"][0]
        assert result["reason"] == "not_authorized", result
        assert db.scalar(select(func.count()).select_from(ActionConfirmation)) == 0
    db.expire_all()
    assert not db.scalar(select(Employee).where(Employee.person_id == market.person.id))
    assert _wallet(market.account_id)["available"] == 10000


def test_spending_ceiling_applies_to_additional_authority(db, world, market):
    position = authority.resolve_actor_authority(db, world.manager.id)
    grant = db.scalar(
        select(PositionAuthorityGrant).where(
            PositionAuthorityGrant.position_definition_id == position.position_definition_id,
            PositionAuthorityGrant.authority_kind == "spend_credits",
            PositionAuthorityGrant.effective_to.is_(None),
        )
    )
    authority.revoke_authority(db, grant_id=grant.id)
    authority.grant_authority(
        db,
        position_definition_id=position.position_definition_id,
        kind=C.AuthorityKind.spend_credits,
        max_amount=1199,
    )
    work = session_for(db, world, "management")
    result = handle_reply(work.id, json.dumps(request(world, market)))["results"][0]
    assert result["reason"] == "not_authorized"
    assert result["authority"]["reason"] == "amount_exceeds_grant"
    assert db.scalar(select(func.count()).select_from(ActionConfirmation)) == 0


@pytest.mark.parametrize("target", ["review", "other_project"])
def test_recruitment_stays_bound_to_current_management_project(db, world, market, target):
    work = session_for(db, world, "review" if target == "review" else "management")
    changes = {"project_id": world.project.id + 999} if target == "other_project" else {}
    result = handle_reply(work.id, json.dumps(request(world, market, **changes)))["results"][0]
    assert result["reason"] == "work_session_write_forbidden", result
    assert db.scalar(select(func.count()).select_from(ActionConfirmation)) == 0


@pytest.mark.parametrize("change", ["price", "mode", "closed", "policy", "amount"])
def test_changed_terms_cannot_reuse_approval(client, db, world, market, monkeypatch, change):
    owner(client, db, world)
    if change == "amount":
        work = session_for(db, world, "management")
        response = handle_reply(work.id, json.dumps(request(world, market, amount=1199)))
        assert response["results"][0]["reason"] == "confirmation_amount_mismatch"
    else:
        _, _, row_id = propose(db, world, market)
        if change == "price":
            market.terms.price = 1300
        elif change == "mode":
            market.terms.sale_mode = "negotiation"
        elif change == "closed":
            market.listing.status, market.listing.closed_at = "closed", utcnow()
        else:
            monkeypatch.setattr(
                "app.services.economy.talent_trade.economic_policy",
                lambda: replace(economic_policy(), contract_fee_bps=400),
            )
        db.commit()
        response = approve_api(client, row_id).json()
        assert response["status"] == "failed", response
    db.expire_all()
    assert not db.scalar(select(Employee).where(Employee.person_id == market.person.id))
    assert _wallet(market.account_id)["available"] == 10000


@pytest.mark.parametrize("mode", ["negotiation", "missing"])
def test_no_implicit_free_hire_or_seller_acceptance(db, world, market, mode):
    if mode == "missing":
        db.delete(market.terms)
    else:
        market.terms.sale_mode = mode
    db.commit()
    work = session_for(db, world, "management")
    response = handle_reply(work.id, json.dumps(request(world, market)))["results"][0]
    assert not response["ok"]
    assert response["error"] == (
        "commercial_terms_not_set"
        if mode == "missing"
        else "negotiation_requires_seller_acceptance"
    )
    assert db.scalar(select(func.count()).select_from(Offer)) == 0
    assert not db.scalar(select(Employee).where(Employee.person_id == market.person.id))


def test_exhausted_funds_preserve_listing_and_employment(client, db, world, market):
    identity = owner(client, db, world)
    _, _, row_id = propose(db, world, market)
    confirmations.review(db, row_id, approve=True, **identity)
    MonetaryAuthority(db).burn(
        actor=EconomicActor.company(world.project.company_id), amount=10000, reason="Other spend"
    )
    row = confirmations.execute(db, row_id, **identity)
    assert row.status == "failed"
    db.expire_all()
    assert market.listing.status == "active"
    assert _wallet(market.account_id)["available"] == 0
    assert not db.scalar(select(Employee).where(Employee.person_id == market.person.id))
    assert db.scalar(select(func.count()).select_from(Contract)) == 0
    assert db.scalar(select(func.count()).select_from(Offer)) == 0


def test_concurrent_proposals_only_one_purchase_and_no_duplicate_charge(client, db, world, market):
    identity = owner(client, db, world)
    work, _, first = propose(db, world, market)
    reply = handle_reply(work.id, json.dumps(request(world, market, key="second-recruit")))
    second = reply["results"][0]["data"]["confirmation"]["id"]
    barrier = Barrier(2)

    def execute(row_id):
        with SessionLocal() as session:
            barrier.wait(timeout=5)
            confirmations.review(session, row_id, approve=True, **identity)
            return confirmations.execute(session, row_id, **identity).status

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(execute, [first, second]))
    assert sorted(statuses) == ["executed", "failed"]
    db.expire_all()
    assert (
        db.scalar(
            select(func.count()).select_from(Employee).where(Employee.person_id == market.person.id)
        )
        == 1
    )
    assert db.scalar(select(func.count()).select_from(Contract)) == 1
    assert _wallet(market.account_id)["available"] == 8800


def test_audit_failure_rolls_back_purchase_intent_and_events(client, db, world, market):
    from sqlalchemy import event

    identity = owner(client, db, world)
    _, _, row_id = propose(db, world, market)
    confirmations.review(db, row_id, approve=True, **identity)

    def fail(mapper, connection, target):
        if target.tool_name == "recruit_from_market" and target.outcome == "applied":
            raise RuntimeError("injected final audit failure")

    event.listen(ToolAudit, "before_insert", fail)
    try:
        with SessionLocal() as session, pytest.raises(RuntimeError, match="injected final audit"):
            confirmations.execute(session, row_id, **identity)
    finally:
        event.remove(ToolAudit, "before_insert", fail)
    db.expire_all()
    assert market.listing.status == "active"
    assert db.get(ActionConfirmation, row_id).status == "approved"
    assert not db.scalar(select(Employee).where(Employee.person_id == market.person.id))
    assert db.scalar(select(func.count()).select_from(Contract)) == 0
    assert db.scalar(select(func.count()).select_from(ProvisioningJob)) == 0
    assert (
        db.scalar(
            select(func.count())
            .select_from(Event)
            .where(Event.type.in_(["person.recruited", "talent.purchased"]))
        )
        == 0
    )
    assert _wallet(market.account_id)["available"] == 10000
    assert confirmations.execute(db, row_id, **identity).status == "executed"
    assert _wallet(market.account_id)["available"] == 8800


@pytest.mark.asyncio
async def test_manager_can_read_partial_provisioning_and_current_readiness(
    client, db, world, market, monkeypatch
):
    owner(client, db, world)
    readiness.set_company_runtime_defaults(
        db, world.project.company_id, {"runtime_type": "hermes", "deployment_mode": "docker"}
    )
    db.commit()
    work, _, row_id = propose(db, world, market)
    reply = approve_api(client, row_id).json()
    assert reply["status"] == "executed", reply
    result = reply["result"]["data"]
    job_id = result["provisioning"]["job_id"]
    employee_id = result["recruitment"]["employee_id"]
    assert result["provisioning"]["job_status"] == "pending"

    async def external_runtime(employee, policy):
        # The external Docker boundary is replaced; the rest uses actual services.
        with SessionLocal() as independent:
            row = independent.get(Employee, employee.id)
            assert row is not None
            row.title = "Independent writer proves hiring committed"
            independent.commit()
        return True, "test-only runtime transport", None

    monkeypatch.setattr(readiness, "ensure_runtime_instance", external_runtime)
    assert await runtime_provisioning.run_job(job_id)
    db.expire_all()
    assert db.get(ProvisioningJob, job_id).status == "partial"
    employee = db.get(Employee, employee_id)
    assert employee.person_id == market.person.id and Path(employee.workspace_path).is_dir()
    result = read(work, "inspect_readiness", {"employee_id": employee_id}, key="after-provision")
    assert result["ok"] and not result["data"]["ready_to_work"]
    progress = next(item for item in result["data"]["stages"] if item["stage"] == "provisioning")[
        "facts"
    ]["job"]
    assert progress["status"] == "partial"
    assert any(step["reason_code"] == "PROVIDER_UNAVAILABLE" for step in progress["steps"])
    api = client.get(f"/api/v1/employees/{employee_id}/readiness").json()
    api_progress = next(item for item in api["stages"] if item["stage"] == "provisioning")["facts"][
        "job"
    ]
    assert api_progress == progress
