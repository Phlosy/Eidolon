"""Identity (R4-a): users / companies / persons / employments as facts, the company
fold, the projection, hiring guarded by institution roles and the hire gate, and the
roster derived from employments."""

from uuid import uuid4

import pytest
from sqlalchemy import select, text

from eidolon.agent.managers import IdentityRoster, Roster, StaticRoster, default_roster
from eidolon.db import SessionLocal
from eidolon.identity import (
    PLATFORM,
    CompanyRow,
    EmploymentRow,
    IdentityError,
    IdentityProjection,
    IdentityService,
    PersonRow,
    UserRow,
    active_employments,
)
from eidolon.identity import facts as F
from eidolon.institution import adopt, staffing
from eidolon.institution.templates import SOFTWARE_STUDIO_V1 as T
from eidolon.kernel.consumers import process, rebuild
from eidolon.kernel.facts import Actor, ActorKind
from eidolon.kernel.store import FactStore

store = FactStore()
ids = IdentityService(store)
OWNER = Actor(ActorKind.human, "owner-1")
CEO = Actor(ActorKind.agent, "ceo-1")


def found(db, *, adopt_institution=True):
    user_id = uuid4()
    ids.register_user(
        db, email=f"o-{user_id.hex[:6]}@example.com", display_name="Owner", user_id=user_id
    )
    ids.create_person(db, OWNER, "owner-1", name="Owner", kind="human", user_id=user_id)
    created, employment = ids.create_company(
        db,
        owner_user_id=user_id,
        owner_person_id="owner-1",
        name="Studio",
        slug=f"studio-{user_id.hex[:6]}",
    )
    if adopt_institution:
        store.append(db, adopt(created.company_id, OWNER, T))
    db.commit()
    return user_id, created.company_id, employment


def test_fact_validation():
    with pytest.raises(IdentityError, match="email"):
        F.user_registered(Actor.system(), uuid4(), email="nope", display_name="x")
    with pytest.raises(IdentityError, match="slug"):
        F.company_created(
            OWNER, uuid4(), name="S", slug="Bad Slug", currency="CNY", owner_user_id=uuid4()
        )
    with pytest.raises(IdentityError, match="currency"):
        F.company_created(
            OWNER, uuid4(), name="S", slug="ok", currency="rmb", owner_user_id=uuid4()
        )
    with pytest.raises(IdentityError, match="user_id"):
        F.person_created(OWNER, "p1", name="P", kind="human")
    with pytest.raises(IdentityError, match="no user account"):
        F.person_created(OWNER, "a1", name="A", kind="agent", user_id=uuid4())
    with pytest.raises(IdentityError, match="person_id"):
        F.person_created(OWNER, "bad id!", name="A", kind="agent")
    with pytest.raises(IdentityError, match="role"):
        F.employment_started(OWNER, uuid4(), uuid4(), person_id="a1", role="Tech Lead")
    assert (
        F.user_registered(Actor.system(), uuid4(), email="A@B.CO", display_name="A").payload[
            "email"
        ]
        == "a@b.co"
    )
    assert F.person_created(OWNER, "a1", name="A", kind="agent").company_id == PLATFORM


def test_company_fold_and_guarded_hiring(db):
    user_id, company, owner_emp = found(db)
    state = ids.company(db, company)
    assert state.name == "Studio" and state.currency == "CNY" and state.owner_user_id == user_id
    assert state.role_of("owner-1") == "owner" and owner_emp.payload["role"] == "owner"

    with pytest.raises(IdentityError, match="no person"):
        staffing.hire(db, company, OWNER, person_id="ghost", role="engineer")
    ids.create_person(db, OWNER, "eng-1", name="Ken", kind="agent", persona={"style": "terse"})
    with pytest.raises(IdentityError, match="decision_id"):
        staffing.hire(
            db, company, CEO, person_id="eng-1", role="engineer"
        )  # agents need the hire gate
    with pytest.raises(IdentityError, match="does not exist"):
        staffing.hire(db, company, OWNER, person_id="eng-1", role="wizard")
    with pytest.raises(IdentityError, match="for humans"):
        staffing.hire(db, company, OWNER, person_id="eng-1", role="owner")
    hired = staffing.hire(
        db, company, OWNER, person_id="eng-1", role="engineer", title="Engineer I"
    )
    db.commit()
    assert hired.kind == F.EMPLOYMENT_STARTED and hired.correlation_id == company
    with pytest.raises(IdentityError, match="already employed"):
        staffing.hire(db, company, OWNER, person_id="eng-1", role="qa")
    decision = uuid4()
    ids.create_person(db, OWNER, "qa-1", name="Margaret", kind="agent")
    by_ceo = staffing.hire(db, company, CEO, person_id="qa-1", role="qa", decision_id=decision)
    db.commit()
    assert by_ceo.decision_id == decision and by_ceo.payload["decision_id"] == str(decision)
    state = ids.company(db, company)
    assert {e.person_id: e.role for e in state.active()} == {
        "owner-1": "owner",
        "eng-1": "engineer",
        "qa-1": "qa",
    }
    assert ids.person(db, "eng-1").persona == {"style": "terse"}
    assert ids.person(db, "nobody") is None

    emp = next(e for e in state.active() if e.person_id == "eng-1")
    with pytest.raises(IdentityError, match="decision_id"):
        ids.end_employment(db, company, CEO, employment_id=emp.employment_id, reason="x")
    with pytest.raises(IdentityError, match="owner"):
        ids.end_employment(
            db,
            company,
            OWNER,
            employment_id=owner_emp.correlation_id
            if False
            else next(e.employment_id for e in state.active() if e.role == "owner"),
            reason="x",
        )
    ids.end_employment(db, company, OWNER, employment_id=emp.employment_id, reason="contract over")
    db.commit()
    state = ids.company(db, company)
    assert state.role_of("eng-1") is None and state.employments[emp.employment_id].status == "ended"
    with pytest.raises(IdentityError, match="no active"):
        ids.end_employment(db, company, OWNER, employment_id=emp.employment_id, reason="again")
    # rehire after leaving is a new employment
    staffing.hire(db, company, OWNER, person_id="eng-1", role="engineer")
    db.commit()
    assert ids.company(db, company).role_of("eng-1") == "engineer"


def test_company_without_institution_accepts_any_role_code(db):
    _, company, _ = found(db, adopt_institution=False)
    ids.create_person(db, OWNER, "x-1", name="X", kind="agent")
    assert (
        staffing.hire(db, company, OWNER, person_id="x-1", role="wizard").payload["role"]
        == "wizard"
    )


def test_create_company_requires_the_owners_human_person(db):
    user_id = uuid4()
    ids.register_user(
        db, email=f"u-{user_id.hex[:6]}@example.com", display_name="U", user_id=user_id
    )
    ids.create_person(db, OWNER, "bot-1", name="Bot", kind="agent")
    with pytest.raises(IdentityError, match="human person"):
        ids.create_company(db, owner_user_id=user_id, owner_person_id="bot-1", name="S", slug="s-1")
    ids.create_person(db, OWNER, "h-1", name="H", kind="human", user_id=uuid4())
    with pytest.raises(IdentityError, match="human person"):
        ids.create_company(db, owner_user_id=user_id, owner_person_id="h-1", name="S", slug="s-1")


def test_projection_matches_fold_and_rebuilds(db):
    user_id, company, _ = found(db)
    ids.create_person(db, OWNER, "eng-1", name="Ken", kind="agent")
    emp = staffing.hire(db, company, OWNER, person_id="eng-1", role="engineer")
    db.commit()
    process(SessionLocal, IdentityProjection())
    assert (
        db.get(UserRow, user_id).email.startswith("o-")
        and db.get(UserRow, user_id).status == "active"
    )
    assert (
        db.get(CompanyRow, company).slug.startswith("studio-")
        and db.get(CompanyRow, company).owner_user_id == user_id
    )
    assert (
        db.get(PersonRow, "eng-1").kind == "agent"
        and db.get(PersonRow, "owner-1").user_id == user_id
    )
    rows = active_employments(db, company)
    assert [(r.person_id, r.role) for r in rows] == [("owner-1", "owner"), ("eng-1", "engineer")]
    ids.end_employment(
        db,
        company,
        OWNER,
        employment_id=__import__("uuid").UUID(emp.payload["employment_id"]),
        reason="done",
    )
    db.commit()
    process(SessionLocal, IdentityProjection())
    db.expire_all()
    row = db.get(EmploymentRow, __import__("uuid").UUID(emp.payload["employment_id"]))
    assert row.status == "ended" and row.end_reason == "done" and row.ended_at is not None
    assert [r.person_id for r in active_employments(db, company)] == ["owner-1"]
    db.execute(
        text(
            "UPDATE identity_employments SET status = 'active'; "
            "UPDATE identity_users SET display_name = 'x'"
        )
    )
    db.commit()
    assert rebuild(SessionLocal, IdentityProjection()) >= 5
    db.expire_all()
    assert [r.person_id for r in active_employments(db, company)] == ["owner-1"]
    assert db.get(UserRow, user_id).display_name == "Owner"
    assert (
        db.scalar(
            select(EmploymentRow.status).where(EmploymentRow.employment_id == row.employment_id)
        )
        == "ended"
    )


def test_roster_is_derived_from_employments(db):
    _, company, _ = found(db)
    for pid, role in (("ceo-1", "ceo"), ("eng-1", "engineer"), ("eng-2", "engineer")):
        ids.create_person(db, OWNER, pid, name=pid, kind="agent")
        staffing.hire(db, company, OWNER, person_id=pid, role=role)
    db.commit()
    roster = IdentityRoster(ids).for_company(db, company)
    assert roster.one("ceo").person_id == "ceo-1" and [
        p.person_id for p in roster.holding("engineer")
    ] == ["eng-1", "eng-2"]
    assert roster.role_of("owner-1") == "owner" and roster.role_of("nobody") is None
    assert roster.as_text() == "- eng-1 (engineer)\n- eng-2 (engineer)"
    assert IdentityRoster(ids).for_company(db, uuid4()).people == []  # unknown company: nobody
    assert StaticRoster(default_roster()).for_company(db, company).one("pm").person_id == "pm-1"
    with pytest.raises(LookupError):
        Roster([]).one("ceo")
