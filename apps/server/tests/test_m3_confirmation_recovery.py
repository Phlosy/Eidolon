"""Restart approved fixed actions through the real recruitment and credit services."""

import asyncio
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.exc import IntegrityError
from test_m3_action_confirmations import owner
from test_m3_management_runtime import world as _world
from test_m3_recruitment_flow import market as _market
from test_m3_recruitment_flow import propose, request
from test_talent_trade import _wallet

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.action_confirmation import ActionConfirmation
from app.models.auth import CompanyMembership, UserSession
from app.models.base import utcnow
from app.models.decision import DecisionRecord
from app.models.event import Event
from app.models.lifecycle import ProvisioningJob
from app.models.organization import Employee
from app.models.project import WorkSession
from app.work import action_confirmations as confirmations
from app.work import recovery
from app.work.runtime_bridge import handle_reply
from app.workflow.orchestrator import Orchestrator

world = _world
market = _market


@pytest.fixture()
def approved(client, db, world, market):
    identity = owner(client, db, world)
    work, _, row_id = propose(db, world, market)
    confirmations.review(db, row_id, **identity, approve=True)
    return work, row_id, identity


def scan(**kwargs):
    kwargs.setdefault("through_id", recovery.confirmation_boundary())
    return recovery.reconcile_approved_actions(**kwargs)


def facts(db, market, row_id):
    db.expire_all()
    row = db.get(ActionConfirmation, row_id)
    employees = list(db.scalars(select(Employee.id).where(Employee.person_id == market.person.id)))
    jobs = list(
        db.scalars(select(ProvisioningJob.id).where(ProvisioningJob.employee_id.in_(employees)))
    )
    return {
        "balance": _wallet(market.account_id)["available"],
        "employees": employees,
        "jobs": jobs,
        "events": list(
            db.scalars(
                select(Event.id)
                .where(Event.type.in_(("person.recruited", "talent.purchased")))
                .order_by(Event.id)
            )
        ),
        "status": row.status,
        "proposal": row.proposal_audit_id,
        "receipt": row.final_audit_id,
        "decision": db.get(DecisionRecord, row.decision_id).status,
    }


PROCESS = """
import os, sys, json
from dataclasses import asdict
from sqlalchemy import event
from app.core.database import SessionLocal
from app.work import action_confirmations, recovery
row_id, company_id, user_id, session_id = map(int, sys.argv[1:5])
mode = sys.argv[5]
if mode == "approve_crash":
    with SessionLocal() as db:
        action_confirmations.review(db, row_id, company_id=company_id,
            user_id=user_id, session_id=session_id, approve=True)
    os._exit(23)
def factory():
    db = SessionLocal()
    if mode == "effect_crash":
        @event.listens_for(db, "after_commit")
        def crash_after_outer_commit(session):
            if not session.in_nested_transaction():
                os._exit(24)
    return db
batch = recovery.reconcile_approved_actions(through_id=row_id, session_factory=factory)
print(json.dumps([asdict(item) for item in batch.items]))
"""


def test_process_death_after_approval_and_effect_commit_never_buys_twice(client, db, world, market):
    identity = owner(client, db, world)
    work, _, row_id = propose(db, world, market)
    work.status, work.ended_at = "completed", utcnow()
    db.commit()
    original = facts(db, market, row_id)

    def child(mode):
        return subprocess.run(
            [
                sys.executable,
                "-c",
                PROCESS,
                str(row_id),
                str(identity["company_id"]),
                str(identity["user_id"]),
                str(identity["session_id"]),
                mode,
            ],
            text=True,
            capture_output=True,
            timeout=20,
        )

    approval = child("approve_crash")
    assert approval.returncode == 23, approval.stderr
    approved_facts = facts(db, market, row_id)
    assert approved_facts == {**original, "status": "approved"}
    effect = child("effect_crash")
    assert effect.returncode == 24, effect.stderr
    saved = facts(db, market, row_id)
    assert saved["status"] == "executed" and saved["decision"] == "APPLIED"
    assert saved["balance"] == original["balance"] - 1200
    assert len(saved["employees"]) == len(saved["jobs"]) == 1
    assert len(saved["events"]) == 2
    assert saved["receipt"] is not None and saved["receipt"] != saved["proposal"]
    assert db.get(ProvisioningJob, saved["jobs"][0]).status == "pending"
    for _ in range(2):
        resumed = child("scan")
        assert resumed.returncode == 0, resumed.stderr
        assert json.loads(resumed.stdout) == [
            {
                "confirmation_id": row_id,
                "outcome": "already_resolved",
                "final_audit_id": saved["receipt"],
            }
        ]
        assert facts(db, market, row_id) == saved
    assert db.get(WorkSession, work.id).status == "completed"


def test_concurrent_scans_reuse_one_final_receipt(db, market, approved):
    _, row_id, _ = approved
    before = facts(db, market, row_id)
    barrier = Barrier(2)

    def recover():
        barrier.wait(timeout=5)
        return scan(through_id=row_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.submit(recover), pool.submit(recover)
        batches = [first.result(timeout=10), second.result(timeout=10)]
    assert sorted(item.outcome for batch in batches for item in batch.items) == [
        "already_resolved",
        "executed",
    ]
    saved = facts(db, market, row_id)
    assert len({item.final_audit_id for batch in batches for item in batch.items}) == 1
    assert saved["balance"] == before["balance"] - 1200 and len(saved["employees"]) == 1


@pytest.mark.parametrize(
    "change,expected",
    [
        ("session", "authentication_required"),
        ("owner", "company_owner_required"),
        ("expired", "expired"),
        ("target", "failed"),
        ("funds", "failed"),
        ("authority", "failed"),
    ],
)
def test_recovery_rechecks_original_authorization_and_terms(
    db, market, approved, monkeypatch, change, expected
):
    work, row_id, identity = approved
    if change == "session":
        db.get(UserSession, identity["session_id"]).revoked_at = utcnow()
    elif change == "owner":
        member = db.scalar(
            select(CompanyMembership).where(CompanyMembership.user_id == identity["user_id"])
        )
        member.role = "MEMBER"
    elif change == "target":
        market.listing.status = "withdrawn"
    elif change == "funds":
        from app.economy.contracts import EconomicActor
        from app.services.economy.monetary import MonetaryAuthority

        MonetaryAuthority(db).burn(
            actor=EconomicActor.company(identity["company_id"]), amount=10000, reason="Other spend"
        )
    elif change == "authority":
        from app.models.position import PositionAuthorityGrant
        from app.work import authority

        actor = authority.resolve_actor_authority(db, work.employee_id)
        grants = db.scalars(
            select(PositionAuthorityGrant).where(
                PositionAuthorityGrant.position_definition_id == actor.position_definition_id
            )
        )
        for grant in grants:
            db.delete(grant)
    db.commit()
    if change == "expired":
        expiry = db.get(ActionConfirmation, row_id).expires_at
        from app.models.base import as_utc

        monkeypatch.setattr(confirmations, "utcnow", lambda: as_utc(expiry))
    before = facts(db, market, row_id)
    result = scan(through_id=row_id)
    assert result.items[0].outcome == expected
    saved = facts(db, market, row_id)
    assert saved["balance"] == before["balance"] and saved["employees"] == []
    assert saved["status"] == ("approved" if change in {"session", "owner"} else expected)


async def test_existing_startup_loop_recovers_approved_action_with_dispatch_disabled(
    db, market, approved, monkeypatch
):
    _, row_id, _ = approved
    monkeypatch.setattr(settings, "orchestrator_dispatch_enabled", False)
    calls = []
    original = recovery.reconcile_approved_actions

    def record(**kwargs):
        result = original(**kwargs)
        calls.append(result)
        return result

    monkeypatch.setattr(recovery, "reconcile_approved_actions", record)
    runner = Orchestrator()
    runner.start()
    runner.notify({"type": "sweep"})
    try:
        async with asyncio.timeout(3):
            while not calls:
                await asyncio.sleep(0.01)
        await runner._handle({"type": "sweep"})
        assert len(calls) == 1 and calls[0].complete
    finally:
        await runner.stop()
    assert facts(db, market, row_id)["status"] == "executed"


def test_pending_page_is_read_only_and_later_approval_obeys_captured_boundary(
    client, db, world, market
):
    identity = owner(client, db, world)
    work, _, first = propose(db, world, market)
    boundary = recovery.confirmation_boundary()
    assert boundary == first
    reply = handle_reply(work.id, json.dumps(request(world, market, key="later-approved")))
    second = reply["results"][0]["data"]["confirmation"]["id"]
    confirmations.review(db, second, **identity, approve=True)

    def read_only():
        session = SessionLocal()

        @event.listens_for(session.connection(), "before_cursor_execute")
        def no_write(_conn, _cursor, statement, _params, _context, _many):
            assert not statement.upper().startswith(
                ("BEGIN IMMEDIATE", "INSERT", "UPDATE", "DELETE")
            )

        return session

    original = scan(through_id=boundary, session_factory=read_only)
    assert original.complete and original.cursor == first
    assert [item.outcome for item in original.items] == ["not_approved"]
    assert facts(db, market, second)["status"] == "approved"
    page = scan(through_id=second, limit=1)
    assert page.cursor == first and not page.complete
    result = scan(through_id=second, after_id=first, limit=1)
    assert result.complete and result.items[0].outcome == "executed"
    assert db.get(ActionConfirmation, first).status == "pending"
    empty = scan(through_id=second, after_id=second)
    assert empty.complete and empty.items == ()


@pytest.mark.parametrize(
    "bounds", [{"limit": True}, {"limit": 101}, {"after_id": 2, "through_id": 1}]
)
def test_confirmation_scan_validates_bounds_before_reading(bounds):
    with pytest.raises(ValueError, match="recovery page"):
        recovery.reconcile_approved_actions(**{"through_id": 1, **bounds})


def test_confirmation_scan_is_company_scoped(db, market, approved):
    from app.core.request_context import (
        RequestIdentity,
        reset_request_identity,
        set_request_identity,
    )

    _, row_id, identity = approved
    before = facts(db, market, row_id)
    token = set_request_identity(RequestIdentity(1, identity["company_id"] + 1000, "owner", 1))
    try:
        assert recovery.confirmation_boundary() == 0
        result = scan(through_id=row_id)
        assert result.complete and result.items == ()
    finally:
        reset_request_identity(token)
    assert facts(db, market, row_id) == before


def test_approval_source_is_rechecked_after_the_read_only_page(db, market, approved, monkeypatch):
    _, row_id, identity = approved
    begin = recovery.begin_observation

    def finish_before_lock(session):
        with SessionLocal() as writer:
            confirmations.execute(writer, row_id, **identity)
        begin(session)

    monkeypatch.setattr(recovery, "begin_observation", finish_before_lock)
    before = facts(db, market, row_id)
    result = scan(through_id=row_id)
    assert result.items[0].outcome == "already_resolved"
    saved = facts(db, market, row_id)
    assert saved["balance"] == before["balance"] - 1200
    assert len(saved["employees"]) == len(saved["jobs"]) == 1


def test_receipt_storage_failure_rolls_back_purchase_and_allows_one_retry(db, market, approved):
    _, row_id, _ = approved
    before = facts(db, market, row_id)
    db.execute(
        text(
            "CREATE TRIGGER reject_recovered_action BEFORE UPDATE ON action_confirmations "
            "WHEN NEW.status='executed' "
            "BEGIN SELECT RAISE(ABORT, 'receipt unavailable'); END"
        )
    )
    db.commit()
    with pytest.raises(IntegrityError, match="receipt unavailable"):
        scan(through_id=row_id)
    assert facts(db, market, row_id) == before
    db.execute(text("DROP TRIGGER reject_recovered_action"))
    db.commit()
    assert scan(through_id=row_id).items[0].outcome == "executed"
    saved = facts(db, market, row_id)
    assert scan(through_id=row_id).items[0].outcome == "already_resolved"
    assert facts(db, market, row_id) == saved
    assert saved["balance"] == before["balance"] - 1200
