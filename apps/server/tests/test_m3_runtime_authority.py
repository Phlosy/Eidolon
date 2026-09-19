"""A long-lived caller must resolve current authorization facts on every action."""

from datetime import timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select, update

from app.core.database import SessionLocal
from app.models.base import utcnow
from app.models.organization import Company
from app.models.position import (
    PositionAssignment,
    PositionAuthorityGrant,
    PositionDefinition,
    PositionSlot,
)
from app.repositories import organization as org_repo
from app.repositories import position as position_repo
from app.work import authority, decisions, tool_executor
from app.work import contracts as C


@pytest.fixture()
def actor(db, default_company_id):
    employee = org_repo.get_employee_by_role(db, default_company_id, "ceo")
    assignment = position_repo.active_primary_assignment(db, employee.id)
    slot = db.get(PositionSlot, assignment.position_slot_id)
    definition = db.get(PositionDefinition, slot.position_definition_id)
    grant = db.scalar(
        select(PositionAuthorityGrant).where(
            PositionAuthorityGrant.position_definition_id == definition.id,
            PositionAuthorityGrant.authority_kind == C.AuthorityKind.plan_project_work.value,
            PositionAuthorityGrant.effective_to.is_(None),
        )
    )
    assert grant is not None
    return SimpleNamespace(
        employee=employee, assignment=assignment, slot=slot, definition=definition, grant=grant
    )


def _authorize(db, actor, *, at=None):
    return authority.authorizes(
        db,
        employee_id=actor.employee.id,
        kind=C.AuthorityKind.plan_project_work,
        at=at,
    )


@pytest.mark.parametrize("start_offset,allowed", [(-1, True), (0, True), (1, False)])
def test_primary_assignment_authority_starts_at_effective_from(db, actor, start_offset, allowed):
    moment = utcnow()
    actor.assignment.effective_from = moment + timedelta(seconds=start_offset)
    actor.grant.effective_from = moment - timedelta(seconds=10)
    db.commit()
    result = _authorize(db, actor, at=moment)
    assert result.allowed is allowed
    if not allowed:
        assert result.reason == "no_active_assignment"


def test_authority_at_uses_the_instant_instead_of_discarding_its_timezone(db, actor):
    moment = utcnow()
    actor.assignment.effective_from = moment + timedelta(hours=1)
    db.commit()
    same_instant = moment.astimezone(timezone(timedelta(hours=8)))
    assert not _authorize(db, actor, at=same_instant).allowed


@pytest.mark.parametrize("end_offset,allowed", [(-1, False), (0, False), (1, True)])
def test_primary_assignment_authority_uses_exclusive_end(db, actor, end_offset, allowed):
    moment = utcnow()
    actor.assignment.effective_from = moment - timedelta(seconds=20)
    actor.assignment.effective_to = moment + timedelta(seconds=end_offset)
    actor.grant.effective_from = moment - timedelta(seconds=10)
    db.commit()
    assert _authorize(db, actor, at=moment).allowed is allowed


def test_cached_grant_is_not_authority_after_another_connection_revokes_it(db, actor):
    assert _authorize(db, actor).allowed
    grant_id = actor.grant.id
    db.commit()
    with SessionLocal() as other:
        other.execute(
            update(PositionAuthorityGrant)
            .where(PositionAuthorityGrant.id == grant_id)
            .values(effective_to=utcnow())
        )
        other.commit()
    assert actor.grant.effective_to is None  # Prove the caller still holds a stale row.
    result = _authorize(db, actor)
    assert not result.allowed
    assert result.reason == "no_grant"


def test_cached_slot_cannot_keep_grants_from_its_previous_definition(db, actor, default_company_id):
    replacement = PositionDefinition(
        company_id=default_company_id,
        code="u7-empty-authority",
        name="No management grant",
    )
    db.add(replacement)
    db.commit()
    replacement_id = replacement.id
    slot_id = actor.slot.id
    previous_definition = actor.slot.position_definition_id
    assert _authorize(db, actor).allowed
    db.commit()
    with SessionLocal() as other:
        other.execute(
            update(PositionSlot)
            .where(PositionSlot.id == slot_id)
            .values(position_definition_id=replacement_id)
        )
        other.commit()
    assert actor.slot.position_definition_id == previous_definition
    result = _authorize(db, actor)
    assert not result.allowed
    assert result.reason == "no_grant"


@pytest.mark.parametrize("moved", ["slot", "definition"])
def test_foreign_company_position_cannot_grant_authority(db, actor, moved):
    foreign = Company(name="Foreign authority", slug="u7-foreign-authority")
    db.add(foreign)
    db.flush()
    getattr(actor, moved).company_id = foreign.id
    db.commit()
    assert not _authorize(db, actor).allowed


def test_decision_snapshot_does_not_name_a_future_assignment(db, actor):
    actor.assignment.effective_from = utcnow() + timedelta(days=1)
    db.commit()
    context = tool_executor.context_for_employee(db, actor.employee)
    record = decisions.open_decision(
        db,
        context,
        decision_type=C.DecisionKind.decompose_project,
        reason="Propose work without claiming a future position",
        scope=f"company:{actor.employee.company_id}",
    )
    assert record.acting_position_assignment_id is None
    assert record.acting_position_definition_id is None


def test_overlapping_primary_assignments_do_not_establish_authority(db, actor):
    moment = utcnow()
    db.add(
        PositionAssignment(
            employee_id=actor.employee.id,
            position_slot_id=actor.slot.id,
            assignment_type="primary",
            is_primary=True,
            effective_from=moment - timedelta(seconds=1),
            effective_to=moment + timedelta(days=1),
        )
    )
    db.commit()
    assert not _authorize(db, actor).allowed


def test_cached_grant_amount_is_refreshed_on_every_authorization(db, actor):
    grant = db.scalar(
        select(PositionAuthorityGrant).where(
            PositionAuthorityGrant.position_definition_id == actor.definition.id,
            PositionAuthorityGrant.authority_kind == C.AuthorityKind.spend_credits.value,
            PositionAuthorityGrant.effective_to.is_(None),
        )
    )
    assert grant is not None and grant.max_amount > 1
    original_amount = grant.max_amount
    assert authority.authorizes(
        db,
        employee_id=actor.employee.id,
        kind=C.AuthorityKind.spend_credits,
        amount=original_amount,
    ).allowed
    db.commit()
    with SessionLocal() as other:
        # Even a direct repair of the source row must not leave the old limit
        # usable in a long-lived identity map. Normal changes close/open grants.
        other.execute(
            update(PositionAuthorityGrant)
            .where(PositionAuthorityGrant.id == grant.id)
            .values(max_amount=1)
        )
        other.commit()
    assert grant.max_amount == original_amount
    result = authority.authorizes(
        db,
        employee_id=actor.employee.id,
        kind=C.AuthorityKind.spend_credits,
        amount=original_amount,
    )
    assert not result.allowed
    assert result.reason == "amount_exceeds_grant"
