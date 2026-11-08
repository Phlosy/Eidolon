"""Briefs for human decisions (R7-a): the company's opinion travels with the question.

* ``Advisor`` — every pending decision a *human* must make and that carries no brief
  yet is put to the role the institution names as advisor (``GateRule.advisor``, or
  the decider's direct report — the CEO for the Owner); the agent reads the gate, the
  subject and the work it is about, and answers with a recommendation in plain words,
  its reasons, the risks and the alternatives. That becomes ``institution.decision.advised``.
* ``DeliveryProposer`` — when every task of an active project is done, the project's
  manager proposes delivery: opens the ``project.deliver`` gate bound to the action,
  with their own brief attached, so the Owner's inbox shows "we recommend accepting
  this, because …" instead of a form to fill.

Neither decides. The human still signs; the brief only makes the signing informed.
"""

from dataclasses import dataclass, field
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from eidolon.agent.managers import Deciders, RosterSource, _template, describe_decision
from eidolon.institution import (
    REQUESTED,
    DecisionError,
    GateContext,
    advise,
    bind,
    load,
    request,
)
from eidolon.kernel.facts import Actor, ActorKind
from eidolon.kernel.store import FactStore
from eidolon.logging import get_logger
from eidolon.work.projection import ProjectRow
from eidolon.work.service import WorkService

log = get_logger("eidolon.agent.briefing")

ADVISOR_SYSTEM = (
    "You advise the owner of a small software studio. A decision is waiting for the owner. "
    "Your job is to make the decision easy: say what the company recommends and why, in "
    "plain language a busy person can read in thirty seconds, then the risks and the "
    "alternatives. Do not explain internal mechanics. Answer with JSON only: "
    '{"recommendation": "approve" | "reject" | "rework", "summary": "<= 2 sentences, plain words", '
    '"rationale": "<why, 2-4 sentences>", "risks": ["..."], "alternatives": ["..."]}.'
)

DELIVERY_SYSTEM = (
    "You are the project manager of a small software studio. Every task of your project is "
    "done and reviewed. Propose the delivery to the owner: in plain language, what was built, "
    "how it was verified, what the owner should look at, and what is not included. Answer "
    'with JSON only: {"recommendation": "approve", "summary": "<= 2 sentences", '
    '"rationale": "<what was built and how it was checked>", "risks": ["..."], '
    '"alternatives": ["..."]}.'
)


def delivery_decision_id(project_id: UUID, attempt: int = 0) -> UUID:
    return uuid5(NAMESPACE_URL, f"eidolon:project:{project_id}:deliver:{attempt}")


@dataclass
class Advisor:
    session_factory: sessionmaker
    deciders: Deciders
    rosters: RosterSource
    svc: WorkService = field(default_factory=WorkService)
    store: FactStore = field(default_factory=FactStore)
    name: str = "advisor"

    def tick(self) -> int:
        with self.session_factory() as db:
            pending = [
                (f.company_id, f.correlation_id)
                for f in self.store.stream(db, kinds=[REQUESTED], limit=100_000)
                if f.payload.get("resolution", {}).get("decider") == "human"
            ]
        done = 0
        for company_id, decision_id in pending:
            if self._advise(company_id, decision_id):
                done += 1
        return done

    def _advise(self, company_id: UUID, decision_id: UUID) -> bool:
        with self.session_factory() as db:
            d = load(db, decision_id, store=self.store)
            if d.decided or d.brief is not None or d.resolution.decider != "human":
                return False
            template = _template(db, company_id)
            if template is None:
                return False
            role = template.advisor_for(d.gate, d.resolution.role)
            if role is None:
                return False
            roster = self.rosters.for_company(db, company_id)
            advisor = next((p for p in roster.people if p.role == role), None)
            if advisor is None:
                return False
            answer = self.deciders.ask(
                company_id=company_id,
                person=advisor,
                system=ADVISOR_SYSTEM,
                prompt=describe_decision(db, self.svc, d),
                run_key=f"decision:{decision_id}:advice:{d.escalations}",
                causation_id=d.last_fact_id,
            )
            if not answer:
                return False
            try:
                fact = advise(
                    d, Actor(ActorKind.agent, advisor.person_id), role=advisor.role, brief=answer
                )
            except DecisionError as exc:
                log.warning(
                    "advice.rejected", extra={"decision_id": str(decision_id), "reason": str(exc)}
                )
                return False
            self.store.append(db, fact)
            db.commit()
            log.info(
                "decision.advised",
                extra={
                    "decision_id": str(decision_id),
                    "gate": d.gate,
                    "advisor": advisor.person_id,
                    "recommendation": fact.payload["brief"]["recommendation"],
                },
            )
            return True


@dataclass
class DeliveryProposer:
    session_factory: sessionmaker
    deciders: Deciders
    rosters: RosterSource
    svc: WorkService = field(default_factory=WorkService)
    store: FactStore = field(default_factory=FactStore)
    name: str = "delivery_proposer"

    def tick(self) -> int:
        with self.session_factory() as db:
            rows = db.scalars(select(ProjectRow).where(ProjectRow.status == "active")).all()
            pending = [(r.company_id, r.project_id) for r in rows]
        done = 0
        for company_id, project_id in pending:
            if self._propose(company_id, project_id):
                done += 1
        return done

    def _propose(self, company_id: UUID, project_id: UUID) -> bool:
        with self.session_factory() as db:
            state = self.svc.project(db, project_id)
            if state.status != "active" or not state.tasks:
                return False
            if not all(t.status == "done" for t in state.tasks.values()):
                return False
            template = _template(db, company_id)
            if template is None or not state.manager:
                return False
            # one open proposal at a time; a rejected one gets a new attempt number
            attempt = 0
            while True:
                did = delivery_decision_id(project_id, attempt)
                try:
                    existing = load(db, did, store=self.store)
                except DecisionError:
                    break
                if existing.status in ("pending", "approved"):
                    return False
                attempt += 1
            roster = self.rosters.for_company(db, company_id)
            pm = next((p for p in roster.people if p.person_id == state.manager), None)
            if pm is None:
                return False
            tasks = "\n".join(
                f"- {t.title}: {t.description[:200]} | reviews: "
                + ", ".join(f"{r.get('reviewer')} {r.get('verdict')}" for r in t.reviews)
                for t in state.tasks.values()
            )
            artifacts = ", ".join(sorted({a.path for a in state.artifacts.values()})) or "-"
            prompt = (
                f"Project: {state.title}\nBudget: {state.budget} units.\n\n"
                f"Tasks:\n{tasks}\n\nArtifacts: {artifacts}"
            )
            answer = self.deciders.ask(
                company_id=company_id,
                person=pm,
                system=DELIVERY_SYSTEM,
                prompt=prompt,
                run_key=f"project:{project_id}:deliver-proposal:{attempt}",
                causation_id=state.last_fact_id,
            )
            if not answer:
                return False
            action = {"kind": "project.deliver", "project_id": str(project_id)}
            try:
                requested, _ = request(
                    template,
                    GateContext(
                        "project.deliver",
                        company_id,
                        proposer=pm.person_id,
                        proposer_role=pm.role,
                        subject={**bind(action), "project_id": str(project_id)},
                    ),
                    Actor(ActorKind.agent, pm.person_id),
                    decision_id=did,
                    causation_id=state.last_fact_id,
                    brief={**answer, "recommendation": "approve"},
                )
            except DecisionError as exc:
                log.warning(
                    "delivery.proposal_rejected",
                    extra={"project_id": str(project_id), "reason": str(exc)},
                )
                return False
            self.store.append(db, requested)
            db.commit()
            log.info(
                "delivery.proposed",
                extra={"project_id": str(project_id), "decision_id": str(did), "by": pm.person_id},
            )
            return True
