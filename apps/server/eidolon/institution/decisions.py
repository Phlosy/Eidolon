"""The decision envelope: every gate crossing is a small fact trail.

    institution.decision.requested   gate, resolution, subject, proposer, amount
    institution.decision.signed      one signer's verdict (+ authority snapshot)
    institution.decision.recorded    the outcome once quorum / rejection decides it
    institution.decision.escalated   unanswered within timeout → the next decider

All facts of one decision share ``correlation_id = decision_id``. ``DecisionState``
is their fold. The system never signs an agent/human gate; a system resolution is
recorded in one step with the check it ran as the reason.
"""

from collections.abc import Mapping
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
ADVISED = "institution.decision.advised"
KINDS = frozenset({REQUESTED, SIGNED, RECORDED, ESCALATED, ADVISED})
VERDICTS = ("approve", "reject", "rework")
RECOMMENDATIONS = VERDICTS
BRIEF_SUMMARY_MAX = 300


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


@dataclass(frozen=True, slots=True)
class Brief:
    """The company's own opinion on a decision a human is asked to make (R7-a): what
    it recommends and why, in plain words, with the risks and the alternatives. Written
    by the advising agent; the human judges it instead of researching the facts."""

    recommendation: str  # approve | reject | rework
    summary: str  # plain language, <= BRIEF_SUMMARY_MAX chars
    rationale: str
    risks: tuple[str, ...]
    alternatives: tuple[str, ...]
    advisor: str | None  # person id
    advisor_role: str | None
    cost: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "recommendation": self.recommendation,
            "summary": self.summary,
            "rationale": self.rationale,
            "risks": list(self.risks),
            "alternatives": list(self.alternatives),
            "advisor": self.advisor,
            "advisor_role": self.advisor_role,
            "cost": self.cost,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "Brief":
        return cls(
            recommendation=str(d.get("recommendation") or ""),
            summary=str(d.get("summary") or ""),
            rationale=str(d.get("rationale") or ""),
            risks=tuple(str(r) for r in (d.get("risks") or ())),
            alternatives=tuple(str(a) for a in (d.get("alternatives") or ())),
            advisor=d.get("advisor"),
            advisor_role=d.get("advisor_role"),
            cost=d.get("cost"),
        )


def validate_brief(brief: Mapping[str, Any]) -> dict[str, Any]:
    """Coerce a model's answer into a brief or refuse it."""
    rec = str(brief.get("recommendation") or "").strip().lower()
    if rec not in RECOMMENDATIONS:
        raise DecisionError(f"brief.recommendation must be one of {RECOMMENDATIONS}")
    summary = " ".join(str(brief.get("summary") or "").split())
    if not summary:
        raise DecisionError("brief.summary is required")
    if len(summary) > BRIEF_SUMMARY_MAX:
        summary = summary[: BRIEF_SUMMARY_MAX - 1] + "…"
    risks = [" ".join(str(r).split()) for r in (brief.get("risks") or ()) if str(r).strip()]
    alts = [" ".join(str(a).split()) for a in (brief.get("alternatives") or ()) if str(a).strip()]
    return {
        "recommendation": rec,
        "summary": summary,
        "rationale": " ".join(str(brief.get("rationale") or "").split())[:2000],
        "risks": risks[:5],
        "alternatives": alts[:5],
        "cost": (str(brief["cost"]).strip()[:200] if brief.get("cost") else None),
    }


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
    brief: Brief | None = None

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
                if p.get("brief"):
                    state.brief = Brief.from_dict(p["brief"])
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
            elif fact.kind == ADVISED:
                state.brief = Brief.from_dict(p["brief"])
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
    brief: Mapping[str, Any] | None = None,
) -> tuple[NewFact, Resolution]:
    """Open a decision at ``ctx.gate``. For a human decider the subject must be a
    bound action (``confirmation.bind``), so the eventual yes applies to exactly it.
    ``brief`` (R7-a) is the proposer's own recommendation, validated."""
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
            "brief": (
                {
                    **validate_brief(brief),
                    "advisor": actor.id,
                    "advisor_role": ctx.proposer_role,
                }
                if brief
                else None
            ),
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


def advise(
    state: DecisionState,
    actor: Actor,
    *,
    role: str | None,
    brief: Mapping[str, Any],
) -> NewFact:
    """The company's opinion on an open decision, as a fact. One per advisor; a
    later one replaces the earlier in the fold (the human sees the latest)."""
    if state.decided:
        raise DecisionError(f"decision {state.decision_id} is already {state.status}")
    if actor.kind is ActorKind.system:
        raise DecisionError("the system holds no opinion; an agent or a person advises")
    body = {**validate_brief(brief), "advisor": actor.id, "advisor_role": role}
    return NewFact(
        company_id=state.company_id,
        actor=actor,
        kind=ADVISED,
        payload={"decision_id": str(state.decision_id), "gate": state.gate, "brief": body},
        idempotency_key=f"decision:{state.decision_id}:advised:{actor.id}:{state.escalations}",
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
