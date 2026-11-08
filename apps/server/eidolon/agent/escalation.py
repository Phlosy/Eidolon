"""Timeouts and escalation (R6-b): an unanswered decision moves to the next decider.

Two jobs:

* ``Escalator`` — every pending decision whose gate has a ``timeout_s`` is escalated
  once that much time has passed since it was requested (or re-escalated since the
  last escalation): ``institution.decision.escalated`` carries the new resolution.
  The clock is injectable so the rule can be tested without waiting hours.
* ``EscalatedDecider`` — an escalated decision that now names an *agent* role is
  put to a holder of that role with the gate, the subject and the work it is about;
  the answer is signed through the same envelope as every other decision. Human
  targets wait in the decisions inbox; the manager jobs apply whatever was decided.

Neither job decides anything: one moves the question, the other asks the person
the institution says to ask.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import sessionmaker

from eidolon.agent.managers import (
    Deciders,
    RosterSource,
    _template,
    decision_project,
    describe_decision,
)
from eidolon.institution import REQUESTED, VERDICTS, escalate, load
from eidolon.kernel.store import FactStore
from eidolon.logging import get_logger
from eidolon.work.service import WorkService

log = get_logger("eidolon.agent.escalation")

ESCALATED_SYSTEM = (
    "You are a manager in a software studio. A decision was escalated to you because the "
    "person who should have decided it did not answer in time. Decide it now on its merits. "
    "Answer with JSON only: "
    '{"verdict": "approve" | "reject" | "rework", "reason": "<one sentence>"}. '
    "Use rework only for plans and reviews that should be redone; otherwise approve or reject."
)


def _pending(store: FactStore, db) -> list[tuple[UUID, UUID]]:
    return [
        (f.company_id, f.correlation_id) for f in store.stream(db, kinds=[REQUESTED], limit=100_000)
    ]


@dataclass
class Escalator:
    session_factory: sessionmaker
    store: FactStore = field(default_factory=FactStore)
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    name: str = "escalator"

    def tick(self) -> int:
        with self.session_factory() as db:
            pending = _pending(self.store, db)
        done = 0
        for company_id, decision_id in pending:
            if self._escalate(company_id, decision_id):
                done += 1
        return done

    def _escalate(self, company_id: UUID, decision_id: UUID) -> bool:
        with self.session_factory() as db:
            state = load(db, decision_id, store=self.store)
            if state.decided:
                return False
            template = _template(db, company_id)
            if template is None:
                return False
            fact = escalate(template, state, now=self.clock())
            if fact is None:
                return False
            self.store.append(db, fact)
            db.commit()
            log.info(
                "decision.escalated",
                extra={
                    "decision_id": str(decision_id),
                    "gate": state.gate,
                    "to": fact.payload["resolution"].get("role"),
                    "escalation_no": fact.payload["escalation_no"],
                },
            )
            return True


@dataclass
class EscalatedDecider:
    session_factory: sessionmaker
    deciders: Deciders
    rosters: RosterSource
    svc: WorkService = field(default_factory=WorkService)
    store: FactStore = field(default_factory=FactStore)
    name: str = "escalated_decider"

    def tick(self) -> int:
        with self.session_factory() as db:
            pending = _pending(self.store, db)
        done = 0
        for company_id, decision_id in pending:
            if self._decide(company_id, decision_id):
                done += 1
        return done

    def _decide(self, company_id: UUID, decision_id: UUID) -> bool:
        with self.session_factory() as db:
            d = load(db, decision_id, store=self.store)
            res = d.resolution
            if d.decided or res.branch != "escalated" or res.decider != "agent":
                return False
            template = _template(db, company_id)
            if template is None:
                return False
            roster = self.rosters.for_company(db, company_id)
            signed_by = {s.person_id for s in d.signatures}
            signer = next(
                (
                    p
                    for p in roster.people
                    if p.role in res.allowed_roles
                    and p.person_id not in signed_by
                    and p.person_id not in (d.proposer, d.author)
                ),
                None,
            )
            if signer is None:
                return False
            prompt = describe_decision(db, self.svc, d)
            answer = self.deciders.ask(
                company_id=company_id,
                person=signer,
                system=ESCALATED_SYSTEM,
                prompt=prompt,
                run_key=f"decision:{decision_id}:escalated:{d.escalations}",
                causation_id=d.last_fact_id,
                project_id=decision_project(db, d),
                template=template,
            )
            verdict = (answer or {}).get("verdict")
            if verdict not in VERDICTS:
                return False
            if verdict == "rework" and d.gate not in ("plan.approve", "task.review"):
                verdict = "reject"
            d = self.deciders.sign_and_conclude(
                db, template, d, signer, verdict, str(answer.get("reason", ""))
            )
            log.info(
                "decision.escalated_decided",
                extra={"decision_id": str(decision_id), "gate": d.gate, "status": d.status},
            )
            return True
