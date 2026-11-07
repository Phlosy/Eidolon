"""The decision envelope: every gate crossing is a small fact trail.

    institution.decision.requested   gate, resolution, subject, proposer, amount
    institution.decision.signed      one signer's verdict (+ authority snapshot)
    institution.decision.recorded    the outcome once quorum / rejection decides it
    institution.decision.escalated   unanswered within timeout → the next decider

All facts of one decision share ``correlation_id = decision_id``. ``DecisionState``
is their fold. The system never signs an agent/human gate; a system resolution is
recorded in one step with the check it ran as the reason.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from eidolon.institution import confirmation
from eidolon.institution.authority import snapshot
from eidolon.institution.gates import GateContext, Resolution, escalation_of, resolve
from eidolon.institution.schema import InstitutionTemplate
from eidolon.kernel.facts import Actor, ActorKind, Fact, NewFact
from eidolon.kernel.store import FactStore

REQUESTED = "institution.decision.requested"
SIGNED = "institution.decision.signed"
RECORDED = "institution.decision.recorded"
ESCALATED = "institution.decision.escalated"
KINDS = frozenset({REQUESTED, SIGNED, RECORDED, ESCALATED})
VERDICTS = ("approve", "reject", "rework")


class DecisionError(ValueError):
    pass


# -- state -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Signature:
    person_id: str
    role: str | None
    verdict: str
    reason: str | None
    fact_id: UUID


@dataclass
class DecisionState:
    decision_id: UUID
    company_id: UUID
    gate: str
    resolution: Resolution
    subject: dict[str, Any]
    proposer: str | None
    author: str | None
    amount: int | None
    requested_at: datetime
    status: str = "pending"  # pending | approved | rejected | rework
    signatures: list[Signature] = field(default_factory=list)
    escalations: int = 0
    last_fact_id: UUID | None = None
    recorded_fact_id: UUID | None = None

    @property
    def decided(self) -> bool:
        return self.status != "pending"

    def approvals(self) -> int:
        return sum(1 for s in self.signatures if s.verdict == "approve")

    @classmethod
    def from_facts(cls, decision_id: UUID, facts: list[Fact]) -> "DecisionState":
        state: DecisionState | None = None
        for fact in sorted(facts, key=lambda f: f.seq):
            if fact.kind not in KINDS or fact.payload.get("decision_id") != str(decision_id):
                continue
            p = fact.payload
            if fact.kind == REQUESTED:
                state = cls(
                    decision_id=decision_id,
                    company_id=fact.company_id,
                    gate=p["gate"],
                    resolution=Resolution.from_dict(p["resolution"]),
                    subject=dict(p.get("subject") or {}),
                    proposer=p.get("proposer"),
                    author=p.get("author"),
                    amount=p.get("amount"),
                    requested_at=fact.created_at,
                )
            elif state is None:
                raise DecisionError(f"decision {decision_id}: {fact.kind} before {REQUESTED}")
            elif fact.kind == SIGNED:
                state.signatures.append(
                    Signature(p["person_id"], p.get("role"), p["verdict"], p.get("reason"), fact.id)
                )
            elif fact.kind == RECORDED:
                state.status = p["verdict_status"]
                state.recorded_fact_id = fact.id
            elif fact.kind == ESCALATED:
                state.resolution = Resolution.from_dict(p["resolution"])
                state.escalations += 1
                state.requested_at = fact.created_at
            state.last_fact_id = fact.id
        if state is None:
            raise DecisionError(f"decision {decision_id}: no {REQUESTED} fact")
        return state


def load(db: Session, decision_id: UUID, *, store: FactStore | None = None) -> DecisionState:
    store = store or FactStore()
    return DecisionState.from_facts(decision_id, store.by_correlation(db, decision_id, kinds=KINDS))


# -- facts -----------------------------------------------------------------------------


def request(
    template: InstitutionTemplate,
    ctx: GateContext,
    actor: Actor,
    *,
    decision_id: UUID | None = None,
    causation_id: UUID | None = None,
) -> tuple[NewFact, Resolution]:
    """Open a decision at ``ctx.gate``. For a human decider the subject must be a
    bound action (``confirmation.bind``), so the eventual yes applies to exactly it."""
    resolution = resolve(template, ctx)
    decision_id = decision_id or uuid4()
    subject = dict(ctx.subject)
    if resolution.decider == "human" and "action_hash" not in subject:
        raise DecisionError(
            f"gate {ctx.gate} is decided by a human: bind the concrete action "
            "(institution.confirmation.bind) as the subject"
        )
    fact = NewFact(
        company_id=ctx.company_id,
        actor=actor,
        kind=REQUESTED,
        payload={
            "decision_id": str(decision_id),
            "gate": ctx.gate,
            "resolution": resolution.as_dict(),
            "subject": subject,
            "proposer": ctx.proposer,
            "proposer_role": ctx.proposer_role,
            "author": ctx.author,
            "amount": ctx.amount,
            "facts": dict(ctx.facts),
        },
        idempotency_key=f"decision:{decision_id}:requested",
        causation_id=causation_id,
        correlation_id=decision_id,
    )
    return fact, resolution


def system_record(
    template: InstitutionTemplate,
    ctx: GateContext,
    *,
    check: str,
    decision_id: UUID | None = None,
    causation_id: UUID | None = None,
) -> tuple[NewFact, NewFact]:
    """A system-decided gate: requested and recorded together, with the mechanical
    check that passed as the reason (never a judgment)."""
    requested, resolution = request(
        template, ctx, Actor.system(), decision_id=decision_id, causation_id=causation_id
    )
    if resolution.decider != "system":
        raise DecisionError(
            f"gate {ctx.gate} resolves to {resolution.decider} ({resolution.role}); "
            "the system may not decide it"
        )
    did = UUID(requested.payload["decision_id"])
    recorded = NewFact(
        company_id=ctx.company_id,
        actor=Actor.system(),
        kind=RECORDED,
        payload={
            "decision_id": str(did),
            "gate": ctx.gate,
            "verdict_status": "approved",
            "reason": f"system check: {check}",
            "signatures": [],
            "template_fingerprint": resolution.template_fingerprint,
        },
        idempotency_key=f"decision:{did}:recorded",
        correlation_id=did,
    )
    return requested, recorded


def sign(
    template: InstitutionTemplate,
    state: DecisionState,
    actor: Actor,
    *,
    role: str | None,
    verdict: str,
    reason: str | None = None,
    action: dict[str, Any] | None = None,
) -> NewFact:
    """One decider's verdict. Checks: the decision is open; the actor's kind matches
    the decider; the role may sign; separation of duties; a reason if required; no
    double signing; for human gates the action matches the bound hash."""
    res = state.resolution
    if res.decider == "system":
        raise DecisionError("a system-decided gate takes no signatures")
    if state.decided:
        raise DecisionError(f"decision {state.decision_id} is already {state.status}")
    if verdict not in VERDICTS:
        raise DecisionError(f"verdict must be one of {VERDICTS}")
    if actor.kind is ActorKind.system:
        raise DecisionError("the system never signs a decision")
    if actor.kind.value != res.decider:
        raise DecisionError(f"gate {state.gate} needs a {res.decider}; actor is {actor.kind.value}")
    if role not in res.allowed_roles:
        raise DecisionError(
            f"role {role!r} may not decide {state.gate} (allowed: {list(res.allowed_roles)})"
        )
    person = actor.id or ""
    if "proposer!=approver" in res.separation and state.proposer and person == state.proposer:
        raise DecisionError("separation of duties: the proposer may not approve")
    if "author!=reviewer" in res.separation and state.author and person == state.author:
        raise DecisionError("separation of duties: the author may not review their own work")
    if any(s.person_id == person for s in state.signatures):
        raise DecisionError(f"{person} already signed decision {state.decision_id}")
    if res.require_reason and not (reason and reason.strip()):
        raise DecisionError(f"gate {state.gate} requires a reason")
    if res.decider == "human":
        if action is None or not confirmation.matches(state.subject, action):
            raise DecisionError("a human confirmation must restate the exact bound action")
    snap = snapshot(template, person, role)
    return NewFact(
        company_id=state.company_id,
        actor=actor,
        kind=SIGNED,
        payload={
            "decision_id": str(state.decision_id),
            "gate": state.gate,
            "person_id": person,
            "role": role,
            "verdict": verdict,
            "reason": reason,
            "authority": snap.as_dict(),
            "signature_no": len(state.signatures) + 1,
        },
        idempotency_key=f"decision:{state.decision_id}:signed:{person}",
        causation_id=state.last_fact_id,
        correlation_id=state.decision_id,
    )


def conclude(state: DecisionState) -> NewFact | None:
    """``recorded`` once the signatures settle it: any reject/rework ends it at once;
    ``quorum`` approvals approve it. Otherwise None (still pending)."""
    if state.decided:
        return None
    negative = next((s for s in state.signatures if s.verdict != "approve"), None)
    if negative is not None:
        status = "rejected" if negative.verdict == "reject" else "rework"
    elif state.approvals() >= state.resolution.quorum:
        status = "approved"
    else:
        return None
    return NewFact(
        company_id=state.company_id,
        actor=Actor.system(),
        kind=RECORDED,
        payload={
            "decision_id": str(state.decision_id),
            "gate": state.gate,
            "verdict_status": status,
            "reason": "; ".join(
                f"{s.person_id}({s.role}): {s.verdict}" + (f" — {s.reason}" if s.reason else "")
                for s in state.signatures
            ),
            "signatures": [s.fact_id.hex for s in state.signatures],
            "template_fingerprint": state.resolution.template_fingerprint,
        },
        idempotency_key=f"decision:{state.decision_id}:recorded",
        causation_id=state.last_fact_id,
        correlation_id=state.decision_id,
    )


def escalate(
    template: InstitutionTemplate,
    state: DecisionState,
    *,
    now: datetime | None = None,
    force: bool = False,
) -> NewFact | None:
    """Past its timeout (or ``force``), move an open decision to the next decider."""
    if state.decided:
        return None
    res = state.resolution
    now = now or datetime.now(UTC)
    if not force:
        if res.timeout_s is None:
            return None
        if (now - state.requested_at).total_seconds() < res.timeout_s:
            return None
    nxt = escalation_of(template, res)
    if nxt is None:
        return None
    return NewFact(
        company_id=state.company_id,
        actor=Actor.system(),
        kind=ESCALATED,
        payload={
            "decision_id": str(state.decision_id),
            "gate": state.gate,
            "from": {"decider": res.decider, "role": res.role},
            "resolution": nxt.as_dict(),
            "reason": "forced" if force else f"no answer within {res.timeout_s}s",
            "escalation_no": state.escalations + 1,
        },
        idempotency_key=f"decision:{state.decision_id}:escalated:{state.escalations + 1}",
        causation_id=state.last_fact_id,
        correlation_id=state.decision_id,
    )


# -- convenience -------------------------------------------------------------------------


def write(db: Session, fact: NewFact, *, store: FactStore | None = None) -> Fact:
    return (store or FactStore()).append(db, fact)
