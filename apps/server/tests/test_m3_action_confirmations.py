"""U10 confirmation behavior through the real runtime bridge and isolated database."""

import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from threading import Barrier

import pytest
from sqlalchemy import func, select
from test_authentication import _register_and_verify
from test_m3_management_runtime import session_for
from test_m3_management_runtime import world as management_world

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.action_confirmation import ActionConfirmation
from app.models.auth import CompanyMembership, UserAuditEvent, UserSession
from app.models.base import utcnow
from app.models.decision import DecisionRecord, ToolAudit
from app.models.position import PositionAuthorityGrant
from app.models.project import Task
from app.work import action_confirmations as confirmations
from app.work import authority, decisions, tools
from app.work import contracts as C
from app.work import tool_executor as executor
from app.work.runtime_bridge import handle_reply

world = management_world


def registered_action(db, world, monkeypatch, *, confirmed=True):
    """A test-only effect; real hiring and money movement belong to U11."""
    actor = authority.resolve_actor_authority(db, world.manager.id)
    authority.grant_authority(
        db, position_definition_id=actor.position_definition_id, kind=C.AuthorityKind.spend_credits
    )

    def terms(db, ctx, args):
        task = db.get(Task, args["task_id"], populate_existing=True)
        if task is None or task.project_id != ctx.project_id or task.status != "in_review":
            raise tools.ToolError("target unavailable")
        return tools.ConfirmationTerms(
            target_ref=f"task:{task.id}", target_version=task.updated_at.isoformat(), amount=100
        )

    def handler(db, ctx, args):
        task = db.get(Task, args["task_id"], populate_existing=True)
        task.description = (task.description or "") + "|confirmed effect|"
        db.flush()
        return {"task_id": task.id}

    extra = {}
    if confirmed:
        extra["confirmation_policy"] = tools.ConfirmationPolicy(
            decision_kind=C.DecisionKind.purchase_agent, resolve_terms=terms
        )
    spec = tools.ToolSpec(
        name="test_confirmed_effect",
        description="Apply one fixed, explicitly confirmed test effect",
        side_effect=C.ToolSideEffect.high_impact,
        input_schema=tools.object_schema(
            {
                "project_id": {"type": "integer"},
                "task_id": {"type": "integer"},
                "amount": {"type": "integer"},
            },
            ("project_id", "task_id", "amount"),
        ),
        output_schema={"type": "object"},
        handler=handler,
        required_authority=C.AuthorityKind.spend_credits,
        authority_target=lambda db, ctx, args: C.AuthorityTarget(company_id=ctx.company_id),
        amount_arg="amount",
        decision_semantics=C.DecisionSemantics.required,
        **extra,
    )
    registry = tools.ToolRegistry(dict(executor.registry._specs))
    registry.register(spec)
    monkeypatch.setattr(executor, "registry", registry)
    return spec, registry


def request(world, *, key="confirmation", actions=None):
    return {
        "version": 1,
        "kind": "decision",
        "operation_key": key,
        "decision": {
            "decision_type": "purchase_agent",
            "reason": "Owner must review this fixed action",
            "scope": f"project:{world.project.id}",
            "actions": actions
            or [
                {
                    "tool": "test_confirmed_effect",
                    "args": {
                        "project_id": world.project.id,
                        "task_id": world.task.id,
                        "amount": 100,
                    },
                }
            ],
        },
    }


def test_high_impact_without_confirmation_descriptor_stays_unregistered(db, world, monkeypatch):
    _, registry = registered_action(db, world, monkeypatch, confirmed=False)
    with pytest.raises(tools.ToolError):
        tools.assert_registry_is_sound(registry)


def test_bound_proposal_waits_without_effect_and_replays_original_audit(db, world, monkeypatch):
    _, registry = registered_action(db, world, monkeypatch)
    tools.assert_registry_is_sound(registry)
    work = session_for(db, world, "management")
    before = world.task.description
    payload = json.dumps(request(world))
    result = handle_reply(work.id, payload)
    pending = result["results"][0]
    assert pending["ok"] is False and pending["reason"] == "confirmation_pending"
    assert pending["data"]["confirmation"]["status"] == "pending"
    assert result["decision_status"] == "EXECUTING"
    assert handle_reply(work.id, payload) == result
    db.expire_all()
    assert db.get(Task, world.task.id).description == before
    decision = db.get(DecisionRecord, result["decision_id"])
    assert decision.resolved_at is None
    audits = list(db.scalars(select(ToolAudit).where(ToolAudit.decision_id == decision.id)))
    assert len(audits) == 1 and audits[0].outcome == "confirmation_pending"


def test_confirmation_api_requires_real_authentication_even_in_development(client):
    assert client.get("/api/v1/action-confirmations").status_code == 401


def owner(client, db, world):
    payload = _register_and_verify(client)
    member = db.scalar(
        select(CompanyMembership).where(
            CompanyMembership.user_id == payload["user"]["id"],
        )
    )
    member.company_id = world.project.company_id
    db.commit()
    session = db.scalar(select(UserSession).where(UserSession.user_id == member.user_id))
    return dict(company_id=member.company_id, user_id=member.user_id, session_id=session.id)


def proposal(db, world, monkeypatch, *, actions=None):
    registered_action(db, world, monkeypatch)
    work = session_for(db, world, "management")
    payload = request(world, actions=actions)
    result = handle_reply(work.id, json.dumps(payload))
    row_id = result["results"][0]["data"]["confirmation"]["id"]
    return work, payload, result, row_id


def approve_api(client, row_id, *, action="approve", body=None):
    return client.post(
        f"/api/v1/action-confirmations/{row_id}/{action}",
        json=body or {},
        headers={"x-csrf-token": client.cookies.get("eidolon_csrf")},
    )


def test_owner_approval_after_session_end_has_one_effect_and_two_immutable_receipts(
    client,
    db,
    world,
    monkeypatch,
):
    work, payload, original, row_id = proposal(db, world, monkeypatch)
    identity = owner(client, db, world)
    work.status, work.ended_at = "completed", utcnow()
    db.commit()
    reply = approve_api(client, row_id)
    assert reply.status_code == 200, reply.text
    assert reply.json()["status"] == "executed"
    assert approve_api(client, row_id).json() == reply.json()
    db.expire_all()
    assert db.get(Task, world.task.id).description.count("|confirmed effect|") == 1
    row = db.get(ActionConfirmation, row_id)
    assert row.reviewer_user_id == identity["user_id"]
    assert row.proposal_audit_id != row.final_audit_id
    assert db.get(ToolAudit, row.proposal_audit_id).result_json == original["results"][0]["data"]
    assert db.get(ToolAudit, row.final_audit_id).runtime_operation_id is None
    assert db.get(DecisionRecord, row.decision_id).status == "APPLIED"
    assert decisions.action_summary(db, row.decision_id) == {
        "total": 1,
        "applied": 1,
        "by_outcome": {"applied": 1},
    }
    assert len(decisions.decision_view(db, db.get(DecisionRecord, row.decision_id))["actions"]) == 1
    assert len(decisions.tool_audits_for(db, row.decision_id)) == 2
    assert (
        db.scalar(
            select(func.count())
            .select_from(UserAuditEvent)
            .where(
                UserAuditEvent.action == "action_confirmation.approved",
            )
        )
        == 1
    )


@pytest.mark.parametrize("auth_required", [False, True])
def test_confirmation_csrf_is_required_in_both_auth_modes(
    client,
    db,
    world,
    monkeypatch,
    auth_required,
):
    _, _, _, row_id = proposal(db, world, monkeypatch)
    owner(client, db, world)
    monkeypatch.setattr(settings, "auth_required", auth_required)
    path = f"/api/v1/action-confirmations/{row_id}/approve"
    assert client.post(path, json={}).status_code == 403
    client.cookies.set("eidolon_csrf", "forged", domain="testserver.local", path="/")
    assert client.post(path, json={}, headers={"x-csrf-token": "forged"}).status_code == 403
    db.expire_all()
    assert db.get(ActionConfirmation, row_id).status == "pending"
    assert "|confirmed effect|" not in (db.get(Task, world.task.id).description or "")


def test_nonowner_crosscompany_and_action_substitution_are_denied(client, db, world, monkeypatch):
    _, _, _, row_id = proposal(db, world, monkeypatch)
    auth = _register_and_verify(client)
    assert client.get(f"/api/v1/action-confirmations/{row_id}").status_code == 404
    assert client.get("/api/v1/action-confirmations").json() == []
    assert approve_api(client, row_id).status_code == 404
    member = db.scalar(
        select(CompanyMembership).where(
            CompanyMembership.user_id == auth["user"]["id"],
        )
    )
    member.company_id, member.role = world.project.company_id, "MEMBER"
    db.commit()
    assert approve_api(client, row_id).status_code == 403
    member.role = "OWNER"
    db.commit()
    assert approve_api(client, row_id, body={"amount": 1}).status_code == 422
    assert db.get(ActionConfirmation, row_id, populate_existing=True).status == "pending"


def test_rejection_is_terminal_without_effect_and_queries_are_shared(
    client, db, world, monkeypatch
):
    work, _, _, row_id = proposal(db, world, monkeypatch)
    owner(client, db, world)
    rejected = approve_api(client, row_id, action="reject")
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "rejected"
    assert approve_api(client, row_id, action="reject").json() == rejected.json()
    assert approve_api(client, row_id).status_code == 409
    read = handle_reply(
        work.id,
        json.dumps(
            {
                "version": 1,
                "kind": "read",
                "operation_key": "inspect-confirmation",
                "tool": "inspect_action_confirmation",
                "args": {"confirmation_id": row_id},
            }
        ),
    )["results"][0]
    assert read["ok"] is True
    assert read["data"]["confirmation"]["status"] == rejected.json()["status"]
    assert read["data"]["confirmation"]["arguments"] == rejected.json()["arguments"]
    db.expire_all()
    assert "|confirmed effect|" not in (db.get(Task, world.task.id).description or "")
    assert db.get(DecisionRecord, rejected.json()["decision_id"]).status == "FAILED"


@pytest.mark.parametrize("approved", [False, True])
def test_expiry_survives_query_and_never_applies(client, db, world, monkeypatch, approved):
    _, _, _, row_id = proposal(db, world, monkeypatch)
    identity = owner(client, db, world)
    if approved:
        confirmations.review(db, row_id, **identity, approve=True)
    deadline = db.get(ActionConfirmation, row_id).expires_at
    from app.models.base import as_utc

    monkeypatch.setattr(confirmations, "utcnow", lambda: as_utc(deadline) + timedelta(seconds=1))
    queried = client.get(f"/api/v1/action-confirmations/{row_id}").json()
    assert queried["deadline_passed"] is True
    assert queried["status"] == ("approved" if approved else "pending")
    assert confirmations.expire_due(db, limit=1) == 1
    assert confirmations.expire_due(db) == 0
    assert approve_api(client, row_id).json()["status"] == "expired"
    db.expire_all()
    assert "|confirmed effect|" not in (db.get(Task, world.task.id).description or "")


@pytest.mark.parametrize(
    "change",
    [
        "grant",
        "employee",
        "manager",
        "version",
        "resource",
        "amount",
        "policy",
        "approval",
        "person",
        "company",
        "assignment",
    ],
)
def test_approval_is_not_permission_to_use_changed_facts(client, db, world, monkeypatch, change):
    _, _, _, row_id = proposal(db, world, monkeypatch)
    identity = owner(client, db, world)
    confirmations.review(db, row_id, **identity, approve=True)
    if change == "grant":
        grants = db.scalars(
            select(PositionAuthorityGrant).where(
                PositionAuthorityGrant.authority_kind == C.AuthorityKind.spend_credits.value,
            )
        )
        for grant in grants:
            grant.effective_to = utcnow()
    elif change == "employee":
        world.manager.lifecycle_status = "inactive"
    elif change == "person":
        from factories import make_person

        world.manager.person_id = make_person(db, slug="u10-replacement-person").id
    elif change == "company":
        from app.models.organization import Company

        company = Company(name="New employer", slug="u10-new-employer")
        db.add(company)
        db.flush()
        world.manager.company_id = company.id
    elif change == "assignment":
        from app.repositories.position import primary_assignment_at

        primary_assignment_at(db, world.manager.id).effective_to = utcnow()
    elif change == "manager":
        world.project.management_employee_id = world.worker.id
    elif change == "version":
        world.task.description = "changed after approval"
    elif change == "resource":
        world.task.status = "cancelled"
    elif change == "approval":
        # Another current Owner session retries, but the original approval is revoked.
        db.get(UserSession, identity["session_id"]).revoked_at = utcnow()
        original = db.get(UserSession, identity["session_id"])
        replacement = UserSession(
            user_id=identity["user_id"],
            token_hash="replacement",
            csrf_token="replacement",
            expires_at=original.expires_at,
            last_seen_at=utcnow(),
        )
        db.add(replacement)
        db.flush()
        identity["session_id"] = replacement.id
    else:
        spec = executor.registry.get("test_confirmed_effect")
        policy = replace(
            spec.confirmation_policy,
            **(
                {"version": "2"}
                if change == "policy"
                else {
                    "resolve_terms": lambda db, ctx, args: tools.ConfirmationTerms(
                        target_ref=f"task:{world.task.id}",
                        target_version="price-changed",
                        amount=101,
                    ),
                }
            ),
        )
        executor.registry._specs[spec.name] = replace(spec, confirmation_policy=policy)
    db.commit()
    row = confirmations.execute(db, row_id, **identity)
    assert row.status == "failed"
    assert row.final_audit_id is not None
    if change in {"employee", "person", "company", "manager"}:
        # Identity failed before current Authority could be evaluated. Do not
        # relabel the original successful grant snapshot as today's decision.
        assert db.get(ToolAudit, row.final_audit_id).authority_json is None
    db.expire_all()
    assert "|confirmed effect|" not in (db.get(Task, world.task.id).description or "")


def test_concurrent_approval_and_lost_response_apply_once(client, db, world, monkeypatch):
    _, _, _, row_id = proposal(db, world, monkeypatch)
    identity = owner(client, db, world)
    barrier = Barrier(2)

    def approve():
        with SessionLocal() as connection:
            barrier.wait(timeout=10)
            confirmations.review(connection, row_id, **identity, approve=True)
            return confirmations.execute(connection, row_id, **identity).final_audit_id

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: approve(), range(2)))
    assert results[0] == results[1]
    # Discard both responses, then reconstruct from the only persisted receipt.
    assert approve_api(client, row_id).json()["final_audit_id"] == results[0]
    db.expire_all()
    assert db.get(Task, world.task.id).description.count("|confirmed effect|") == 1
    assert db.scalar(select(func.count()).select_from(ActionConfirmation)) == 1


def test_early_approval_does_not_terminate_incomplete_envelope(client, db, world, monkeypatch):
    from app.work.runtime_operations import RuntimeExecution

    registered_action(db, world, monkeypatch)
    work = session_for(db, world, "management")
    identity = owner(client, db, world)
    first = request(world)["decision"]["actions"][0]
    second = {
        "tool": "create_task",
        "args": {"project_id": world.project.id, "title": "Remaining action"},
    }
    original = RuntimeExecution.execute_action

    def approve_between_actions(self, index, action, **kwargs):
        result = original(self, index, action, **kwargs)
        if index == 0:
            row_id = result.data["confirmation"]["id"]
            with SessionLocal() as other:
                confirmations.review(other, row_id, **identity, approve=True)
                confirmations.execute(other, row_id, **identity)
                decision = other.get(DecisionRecord, kwargs["decision_id"])
                assert decision.status == "EXECUTING" and decision.resolved_at is None
        return result

    monkeypatch.setattr(RuntimeExecution, "execute_action", approve_between_actions)
    result = handle_reply(work.id, json.dumps(request(world, actions=[first, second])))
    assert result["decision_status"] == "APPLIED"
    assert result["results"][1]["ok"] is True
    db.expire_all()
    assert (
        db.scalar(select(func.count()).select_from(Task).where(Task.title == "Remaining action"))
        == 1
    )
    assert decisions.action_summary(db, result["decision_id"])["total"] == 2


def test_identical_actions_are_distinct_but_replay_does_not_duplicate_proposals(
    db, world, monkeypatch
):
    action = request(world)["decision"]["actions"][0]
    work, payload, result, _ = proposal(db, world, monkeypatch, actions=[action, deepcopy(action)])
    assert handle_reply(work.id, json.dumps(payload)) == result
    db.expire_all()
    rows = list(db.scalars(select(ActionConfirmation).order_by(ActionConfirmation.action_index)))
    assert [row.action_index for row in rows] == [0, 1]
    assert rows[0].id != rows[1].id
    assert rows[0].arguments_digest == rows[1].arguments_digest


@pytest.mark.parametrize("failed_write", ["INSERT INTO tool_audits", "UPDATE action_confirmations"])
def test_final_audit_failure_rolls_back_effect_and_keeps_approval_retryable(
    client, db, world, monkeypatch, failed_write
):
    _, _, _, row_id = proposal(db, world, monkeypatch)
    identity = owner(client, db, world)
    confirmations.review(db, row_id, **identity, approve=True)
    from sqlalchemy import event
    from sqlalchemy.exc import OperationalError

    def fail_audit(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith(failed_write):
            raise OperationalError(
                statement, parameters, RuntimeError("injected audit storage failure")
            )

    event.listen(db.get_bind(), "before_cursor_execute", fail_audit)
    try:
        with pytest.raises(OperationalError):
            confirmations.execute(db, row_id, **identity)
        db.rollback()
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", fail_audit)
    row = db.get(ActionConfirmation, row_id)
    assert row.status == "approved" and row.final_audit_id is None
    assert "|confirmed effect|" not in (db.get(Task, world.task.id).description or "")
    assert confirmations.execute(db, row_id, **identity).status == "executed"


@pytest.mark.parametrize("approved", [False, True])
def test_waiting_and_approved_proposals_are_readable_in_a_fresh_process(
    client,
    db,
    world,
    monkeypatch,
    approved,
):
    import subprocess
    import sys

    _, _, _, row_id = proposal(db, world, monkeypatch)
    identity = owner(client, db, world)
    if approved:
        confirmations.review(db, row_id, **identity, approve=True)
    code = """
import json, sys
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.work import action_confirmations as c
from app.work.tools import json_safe
with Session(create_engine(sys.argv[1])) as db:
    row = c.get_for_company(db, int(sys.argv[2]), int(sys.argv[3]))
    print(json.dumps(json_safe(c.view(db, row))))
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            code,
            str(db.get_bind().url),
            str(identity["company_id"]),
            str(row_id),
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    saved = json.loads(result.stdout)
    assert saved["status"] == ("approved" if approved else "pending")
    assert saved["proposal_audit_id"] is not None and saved["final_audit_id"] is None
    assert saved["arguments"]["amount"] == 100
    assert saved["reviewer_user_id"] == (identity["user_id"] if approved else None)


def test_original_and_final_receipts_and_confirmation_terms_are_immutable(
    client, db, world, monkeypatch
):
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    _, _, _, row_id = proposal(db, world, monkeypatch)
    identity = owner(client, db, world)
    confirmations.review(db, row_id, **identity, approve=True)
    row = confirmations.execute(db, row_id, **identity)
    statements = [
        ("UPDATE action_confirmations SET amount = 1 WHERE id = :id", row_id),
        ("UPDATE action_confirmations SET status = 'pending' WHERE id = :id", row_id),
        ("UPDATE action_confirmations SET reviewer_user_id = 999 WHERE id = :id", row_id),
        ("DELETE FROM action_confirmations WHERE id = :id", row_id),
        ("UPDATE tool_audits SET result_json = '{}' WHERE id = :id", row.proposal_audit_id),
        ("UPDATE tool_audits SET result_json = '{}' WHERE id = :id", row.final_audit_id),
        ("DELETE FROM tool_audits WHERE id = :id", row.final_audit_id),
    ]
    for sql, identifier in statements:
        with pytest.raises(IntegrityError):
            db.execute(text(sql), {"id": identifier})
        db.rollback()
    assert db.get(ActionConfirmation, row_id).status == "executed"


def test_confirmation_requires_authority_and_original_bound_decision(db, world, monkeypatch):
    registered_action(db, world, monkeypatch)
    work = session_for(db, world, "management")
    # A caller-created decision id is not the correlated runtime envelope.
    ctx = executor.context_for_work_session(db, work.id)
    decision = decisions.open_decision(
        db,
        ctx,
        decision_type="purchase_agent",
        reason="Uncorrelated",
        scope=f"project:{world.project.id}",
    )
    decision.status = "EXECUTING"
    db.commit()
    result = executor.execute_tool(
        db,
        name="test_confirmed_effect",
        args=request(world)["decision"]["actions"][0]["args"],
        context=ctx,
        decision_id=decision.id,
    )
    assert not result.ok
    assert db.scalar(select(func.count()).select_from(ActionConfirmation)) == 0
    for grant in db.scalars(
        select(PositionAuthorityGrant).where(
            PositionAuthorityGrant.authority_kind == "spend_credits",
        )
    ):
        grant.effective_to = utcnow()
    db.commit()
    result = handle_reply(work.id, json.dumps(request(world)))
    assert result["results"][0]["reason"] == "not_authorized"
    assert db.scalar(select(func.count()).select_from(ActionConfirmation)) == 0


def test_tool_reclassified_as_ordinary_write_cannot_reuse_old_approval(
    client, db, world, monkeypatch
):
    spec, registry = registered_action(db, world, monkeypatch)
    # Use an existing, in-scope ordinary tool name so all real scope gates apply.
    spec = replace(spec, name="update_task")
    registry._specs[spec.name] = spec
    work = session_for(db, world, "management")
    payload = request(world)
    payload["decision"]["actions"][0]["tool"] = "update_task"
    result = handle_reply(work.id, json.dumps(payload))
    row_id = result["results"][0]["data"]["confirmation"]["id"]
    identity = owner(client, db, world)
    confirmations.review(db, row_id, **identity, approve=True)
    registry._specs[spec.name] = replace(
        spec, side_effect=C.ToolSideEffect.write, confirmation_policy=None
    )
    assert confirmations.execute(db, row_id, **identity).status == "failed"
    assert "|confirmed effect|" not in (db.get(Task, world.task.id).description or "")


@pytest.mark.parametrize("command", ["approve", "reject", "execute", "expire"])
def test_expired_commands_close_the_request_without_effect(client, db, world, monkeypatch, command):
    from app.models.base import as_utc

    _, _, _, row_id = proposal(db, world, monkeypatch)
    owner(client, db, world)
    deadline = as_utc(db.get(ActionConfirmation, row_id).expires_at)
    monkeypatch.setattr(confirmations, "utcnow", lambda: deadline + timedelta(seconds=1))
    response = approve_api(client, row_id, action=command)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "expired"
    assert client.get(f"/api/v1/action-confirmations/{row_id}").json()["status"] == "expired"
    db.expire_all()
    assert "|confirmed effect|" not in (db.get(Task, world.task.id).description or "")


def test_balance_exhausted_after_approval_rolls_back_partial_domain_effect(
    client, db, world, monkeypatch
):
    from app.economy.contracts import EconomicActor
    from app.models.organization import Company
    from app.services.economy.accounts import AccountService
    from app.services.economy.ledger import InsufficientFunds, LedgerService
    from app.services.economy.monetary import MonetaryAuthority
    from app.services.economy.rewards import RewardService

    actor = EconomicActor.company(world.project.company_id)
    monetary = MonetaryAuthority(db)
    monetary.mint(actor=actor, amount=100, reason="U10 test funding", commit=True)
    payee_company = Company(name="U10 effect recipient", slug="u10-effect-recipient")
    db.add(payee_company)
    db.flush()
    accounts = AccountService(db)
    payer_id = accounts.ensure_account(actor).id
    payee_id = accounts.ensure_account(EconomicActor.company(payee_company.id)).id
    db.commit()
    spec, registry = registered_action(db, world, monkeypatch)

    def effect(db, ctx, args):
        result = spec.handler(db, ctx, args)
        try:
            LedgerService(db).transfer(
                payer_account_id=payer_id,
                payee_account_id=payee_id,
                amount=args["amount"],
                idempotency_key="u10-confirmed-effect",
                commit=False,
            )
        except InsufficientFunds as exc:
            raise tools.ToolError("insufficient_funds") from exc
        return result

    registry._specs[spec.name] = replace(spec, handler=effect)
    work = session_for(db, world, "management")
    result = handle_reply(work.id, json.dumps(request(world)))
    row_id = result["results"][0]["data"]["confirmation"]["id"]
    identity = owner(client, db, world)
    confirmations.review(db, row_id, **identity, approve=True)
    balance = RewardService(db).company_available_balance(world.project.company_id)
    monetary.burn(actor=actor, amount=balance, reason="U10 competing spend", commit=True)
    row = confirmations.execute(db, row_id, **identity)
    assert row.status == "failed"
    assert confirmations.view(db, row)["result"]["reason"] == "domain_rejected"
    assert confirmations.view(db, row)["result"]["error"] == "insufficient_funds"
    db.expire_all()
    assert "|confirmed effect|" not in (db.get(Task, world.task.id).description or "")
    assert LedgerService(db).ledger_balance(payee_id) == 0


def test_live_runtime_replay_keeps_original_waiting_receipt_after_execution(
    client, db, world, monkeypatch
):
    work, payload, original, row_id = proposal(db, world, monkeypatch)
    owner(client, db, world)
    assert approve_api(client, row_id).json()["status"] == "executed"
    replay = handle_reply(work.id, json.dumps(payload))
    assert replay["results"] == original["results"]
    assert replay["decision_status"] == "APPLIED"
    changed = deepcopy(payload)
    changed["decision"]["actions"][0]["args"]["amount"] = 99
    from app.work.runtime_bridge import BridgeError

    with pytest.raises(BridgeError) as exc:
        handle_reply(work.id, json.dumps(changed))
    assert exc.value.reason == "operation_conflict"
    db.expire_all()
    assert db.get(Task, world.task.id).description.count("|confirmed effect|") == 1


def test_proposal_amount_must_equal_server_quote(db, world, monkeypatch):
    registered_action(db, world, monkeypatch)
    work = session_for(db, world, "management")
    payload = request(world)
    payload["decision"]["actions"][0]["args"]["amount"] = 99
    result = handle_reply(work.id, json.dumps(payload))
    assert result["results"][0]["reason"] == "confirmation_amount_mismatch"
    assert db.scalar(select(func.count()).select_from(ActionConfirmation)) == 0
