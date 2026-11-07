"""Which institution a company runs, and how it changes — as facts (§5.4).

    institution.template.adopted   the company starts running a template (full template inline)
    institution.template.changed   a new version: fork + customise, validated; a decision
                          (decision_id) or a human stands behind it

``current(db, company)`` folds these; decisions made under an older version keep
referencing that version's fingerprint, so history stays explainable.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from eidolon.institution.schema import InstitutionTemplate, SchemaError
from eidolon.institution.templates import BUILTIN
from eidolon.institution.validate import assert_valid
from eidolon.kernel.facts import Actor, ActorKind, Fact, NewFact
from eidolon.kernel.store import FactStore

ADOPTED = "institution.template.adopted"
CHANGED = "institution.template.changed"
KINDS = frozenset({ADOPTED, CHANGED})


class InstitutionError(ValueError):
    pass


def adopt(
    company_id: UUID,
    actor: Actor,
    template: InstitutionTemplate | str,
    *,
    decision_id: UUID | None = None,
) -> NewFact:
    """Start running ``template`` (a built-in id or a full template). Setting the
    company's rules is a management act: a human, or a decision, must stand behind it."""
    if isinstance(template, str):
        template = BUILTIN[template] if template in BUILTIN else None
        if template is None:
            raise InstitutionError(f"unknown template id; built-in: {sorted(BUILTIN)}")
    _require_mandate(actor, decision_id, "adopting an institution")
    assert_valid(template)
    return NewFact(
        company_id=company_id,
        actor=actor,
        kind=ADOPTED,
        payload={
            "template_id": template.id,
            "version": template.version,
            "fingerprint": template.fingerprint(),
            "template": template.as_dict(),
        },
        idempotency_key=f"institution:{company_id}:adopted:{template.id}:{template.version}",
        decision_id=decision_id,
    )


def customize(
    db: Session,
    company_id: UUID,
    actor: Actor,
    changes: Mapping[str, Any],
    *,
    reason: str,
    decision_id: UUID | None = None,
    store: FactStore | None = None,
) -> NewFact:
    """Fork the company's current institution with ``changes`` into the next version."""
    _require_mandate(actor, decision_id, "changing an institution")
    if not reason or not reason.strip():
        raise InstitutionError("a change needs a reason")
    state = current(db, company_id, store=store)
    if state is None:
        raise InstitutionError(f"company {company_id} has not adopted an institution")
    try:
        new = state.template.with_changes(changes)
    except SchemaError as exc:
        raise InstitutionError(str(exc)) from exc
    assert_valid(new)
    return NewFact(
        company_id=company_id,
        actor=actor,
        kind=CHANGED,
        payload={
            "template_id": new.id,
            "version": new.version,
            "fingerprint": new.fingerprint(),
            "previous_fingerprint": state.fingerprint,
            "changes": dict(changes),
            "reason": reason,
            "template": new.as_dict(),
        },
        idempotency_key=f"institution:{company_id}:changed:{new.version}:{new.fingerprint()[:16]}",
        decision_id=decision_id,
        causation_id=state.fact_id,
    )


def _require_mandate(actor: Actor, decision_id: UUID | None, what: str) -> None:
    if decision_id is None and actor.kind is not ActorKind.human:
        raise InstitutionError(f"{what} needs a decision_id unless a human does it")


@dataclass(frozen=True, slots=True)
class InstitutionState:
    template: InstitutionTemplate
    fingerprint: str
    fact_id: UUID  # the fact that put this version in force
    adopted_fact_id: UUID
    changes: int  # how many institution.template.changed facts since adoption


def fold(facts: list[Fact]) -> InstitutionState | None:
    state: InstitutionState | None = None
    for fact in sorted(facts, key=lambda f: f.seq):
        if fact.kind == ADOPTED:
            template = InstitutionTemplate.from_dict(fact.payload["template"])
            state = InstitutionState(template, fact.payload["fingerprint"], fact.id, fact.id, 0)
        elif fact.kind == CHANGED and state is not None:
            template = InstitutionTemplate.from_dict(fact.payload["template"])
            state = InstitutionState(
                template,
                fact.payload["fingerprint"],
                fact.id,
                state.adopted_fact_id,
                state.changes + 1,
            )
    return state


def current(
    db: Session, company_id: UUID, *, store: FactStore | None = None
) -> InstitutionState | None:
    store = store or FactStore()
    facts = store.stream(db, kinds=KINDS, company_id=company_id, limit=10_000)
    return fold(facts)
