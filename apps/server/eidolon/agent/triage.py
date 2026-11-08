"""Failure handling as a proposal (R7-d).

When a task fails, gets blocked (a reviewer escalated) or runs out of budget, the
responsible manager — the role the institution names as ``task.triage`` proposer, the
Tech Lead in the studio — looks at the project (R7-c eyes), decides what should happen
(reopen / reassign / cancel / split) and opens a ``task.triage`` decision with a brief.
The gate's decider (the PM in the studio; a human elsewhere or after a timeout) approves
or rejects; an approved disposition is enacted by ``work.triage.apply_triage``. What the
user sees is "the Tech Lead suggests handing this to eng-2 because …", never a red state
with no plan. A rejected proposal gets one more try (new attempt), then the task waits
for a person.
"""

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from eidolon.agent.managers import Deciders, RosterSource, _template, describe_decision
from eidolon.institution import (
    DecisionError,
    GateContext,
    GateError,
    bind,
    load,
    request,
    resolve,
)
from eidolon.institution.decisions import validate_brief
from eidolon.kernel.facts import Actor, ActorKind
from eidolon.kernel.store import FactStore
from eidolon.logging import get_logger
from eidolon.work import facts as F
from eidolon.work.projection import ProjectRow
from eidolon.work.service import WorkService
from eidolon.work.state import ProjectState, TaskState
from eidolon.work.triage import apply_triage, dependents, triage_decision_id, validate_disposition

log = get_logger("eidolon.agent.triage")

MAX_ATTEMPTS = 2
TRIAGE_SYSTEM = (
    "You are the tech lead of a small software studio. A task has failed or is blocked. "
    "Decide what should happen and propose it: reopen (same person tries again), reassign "
    "(another person from the roster), cancel (the task is not worth finishing), or split "
    "(replace it with 2-4 smaller tasks; only allowed when nothing depends on it). Explain "
    "in plain words for the owner. Answer with JSON only: "
    '{"disposition": "reopen" | "reassign" | "cancel" | "split", "assignee": "<person id, '
    'for reassign>", "new_tasks": [{"title": "...", "description": "...", '
    '"acceptance_criteria": ["..."]}], "reason": "<one sentence>", '
    '"summary": "<= 2 sentences, plain words", "rationale": "<why this and not the others>", '
    '"risks": ["..."], "alternatives": ["..."]}.'
)
TRIAGE_APPROVE_SYSTEM = (
    "You are the product manager of a small software studio. The tech lead proposes what "
    "to do with a failed task; the proposal and its reasoning are below. Approve it if it is "
    "sensible and proportionate; reject it with a reason if not. Answer with JSON only: "
    '{"verdict": "approve" | "reject", "reason": "<one paragraph>"}.'
)


@dataclass
class Triage:
    session_factory: sessionmaker
    deciders: Deciders
    rosters: RosterSource
    svc: WorkService = field(default_factory=WorkService)
    store: FactStore = field(default_factory=FactStore)
    name: str = "triage"

    def tick(self) -> int:
        with self.session_factory() as db:
            rows = db.scalars(select(ProjectRow).where(ProjectRow.status == "active")).all()
            projects = [(r.company_id, r.project_id) for r in rows]
        done = 0
        for company_id, project_id in projects:
            done += self._project(company_id, project_id)
        return done

    def _project(self, company_id: UUID, project_id: UUID) -> int:
        done = 0
        with self.session_factory() as db:
            template = _template(db, company_id)
            if template is None or "task.triage" not in template.gates:
                return 0  # an institution adopted before R7-d: nothing to propose with
            state = self.svc.project(db, project_id)
            if state.status != "active":
                return 0
            roster = self.rosters.for_company(db, company_id)
            for task in state.tasks.values():
                if task.status not in ("failed", "blocked"):
                    continue
                pending = self._open_decision(db, task)
                if pending is _Exhausted:
                    continue  # every proposal was rejected: the task waits for a person
                if pending is None:
                    if not self._propose(db, template, state, task, roster, company_id):
                        continue
                    done += 1
                    pending = self._open_decision(db, task)  # fall through: approve + enact now
                    if pending is None or pending is _Exhausted:
                        continue
                if pending.status == "pending" and pending.resolution.decider == "agent":
                    if self._approve(db, template, pending, roster, company_id):
                        done += 1
                        pending = load(db, pending.decision_id, store=self.store)
                if pending.status == "approved":
                    apply_triage(db, self.svc, pending, Actor.system())
                    db.commit()
                    log.info(
                        "triage.applied",
                        extra={
                            "task_id": str(task.task_id),
                            "disposition": pending.subject.get("disposition"),
                            "decision_id": str(pending.decision_id),
                        },
                    )
                    done += 1
        return done

    def _open_decision(self, db, task: TaskState):
        """The live proposal for this failure, if any: pending or approved-but-unapplied.
        Rejected ones count against the attempt cap and are otherwise ignored."""
        for attempt in range(MAX_ATTEMPTS):
            did = triage_decision_id(task.task_id, task.last_fact_id, attempt)
            try:
                d = load(db, did, store=self.store)
            except DecisionError:
                return None
            if d.status in ("pending", "approved"):
                return d
        return _Exhausted

    def _propose(self, db, template, state: ProjectState, task: TaskState, roster, company_id):
        attempt = 0
        while attempt < MAX_ATTEMPTS:
            try:
                load(
                    db,
                    triage_decision_id(task.task_id, task.last_fact_id, attempt),
                    store=self.store,
                )
                attempt += 1
            except DecisionError:
                break
        if attempt >= MAX_ATTEMPTS:
            return False
        try:
            resolution = resolve(template, GateContext("task.triage", company_id))
        except GateError as exc:
            log.warning("triage.no_rule", extra={"company_id": str(company_id), "reason": str(exc)})
            return False
        rule = template.gates["task.triage"]
        proposer_role = (
            rule.conditions.get("proposer")
            or resolve(template, GateContext("task.assign", company_id)).role
        )
        proposer = next((p for p in roster.people if p.role == proposer_role), None)
        if proposer is None:
            return False
        candidates = {
            p.person_id: p.role for p in roster.people if p.role == (task.role or "engineer")
        }
        answer = self.deciders.ask(
            company_id=company_id,
            person=proposer,
            system=TRIAGE_SYSTEM,
            prompt=self._prompt(db, state, task, candidates, attempt),
            run_key=f"task:{task.task_id}:triage:{task.last_fact_id}:{attempt}",
            task_id=str(task.task_id),
            causation_id=task.last_fact_id,
            project_id=state.project_id,
            template=template,
        )
        if not answer:
            return False
        try:
            subject = validate_disposition(state, task, answer, candidates=candidates)
            brief = validate_brief(
                {
                    "recommendation": "approve",
                    "summary": answer.get("summary") or answer.get("reason"),
                    "rationale": answer.get("rationale") or "",
                    "risks": answer.get("risks") or [],
                    "alternatives": answer.get("alternatives") or [],
                }
            )
            action = {
                "kind": "task.triage",
                "task_id": subject["task_id"],
                "disposition": subject["disposition"],
                "assignee": subject.get("assignee"),
            }
            requested, _ = request(
                template,
                GateContext(
                    "task.triage",
                    company_id,
                    proposer=proposer.person_id,
                    proposer_role=proposer.role,
                    subject={**bind(action), **subject},
                ),
                Actor(ActorKind.agent, proposer.person_id),
                decision_id=triage_decision_id(task.task_id, task.last_fact_id, attempt),
                causation_id=task.last_fact_id,
                brief=brief,
            )
        except (F.WorkError, DecisionError) as exc:
            log.warning(
                "triage.proposal_rejected",
                extra={"task_id": str(task.task_id), "reason": str(exc)},
            )
            return False
        self.store.append(db, requested)
        db.commit()
        log.info(
            "triage.proposed",
            extra={
                "task_id": str(task.task_id),
                "disposition": subject["disposition"],
                "by": proposer.person_id,
                "decider": resolution.decider,
            },
        )
        return True

    def _approve(self, db, template, decision, roster, company_id) -> bool:
        signer = next(
            (
                p
                for p in roster.people
                if p.role in decision.resolution.allowed_roles
                and p.person_id != decision.proposer
                and not any(s.person_id == p.person_id for s in decision.signatures)
            ),
            None,
        )
        if signer is None:
            return False
        prompt = describe_decision(db, self.svc, decision)
        if decision.brief:
            prompt += (
                f"\n\nProposal by {decision.proposer}: {decision.subject.get('disposition')}"
                + (
                    f" to {decision.subject['assignee']}"
                    if decision.subject.get("assignee")
                    else ""
                )
                + f"\nReason: {decision.subject.get('reason')}\nSummary: {decision.brief.summary}"
                f"\nRationale: {decision.brief.rationale}"
            )
        answer = self.deciders.ask(
            company_id=company_id,
            person=signer,
            system=TRIAGE_APPROVE_SYSTEM,
            prompt=prompt,
            run_key=f"decision:{decision.decision_id}:triage-approve:{decision.escalations}",
            causation_id=decision.last_fact_id,
            project_id=UUID(str(decision.subject["project_id"])),
            template=template,
        )
        verdict = (answer or {}).get("verdict")
        if verdict not in ("approve", "reject"):
            return False
        self.deciders.sign_and_conclude(
            db, template, decision, signer, verdict, str(answer.get("reason") or verdict)
        )
        return True

    def _prompt(self, db, state: ProjectState, task: TaskState, candidates, attempt: int) -> str:
        facts = self.store.by_correlation(db, state.project_id, kinds=F.PROJECT_KINDS)
        mine = [f for f in facts if f.payload.get("task_id") == str(task.task_id)]
        failures = [f for f in mine if f.kind in (F.TASK_FAILED, F.TASK_BLOCKED, F.TASK_REVIEWED)][
            -3:
        ]
        lines = [
            f"Task: {task.title}",
            task.description,
            "Acceptance criteria: " + ("; ".join(task.acceptance_criteria) or "-"),
            f"Status: {task.status}; assignee: {task.assignee or '-'}; "
            f"rework rounds: {task.rework_count}",
            "What happened:",
        ]
        for f in failures:
            what = f.kind.rsplit(".", 1)[-1]
            detail = f.payload.get("reason") or f.payload.get("verdict") or ""
            lines.append(f"- {what}: {detail}"[:400])
        deps = dependents(state, task.task_id)
        lines.append(
            "Tasks depending on this one: " + (", ".join(t.title for t in deps) if deps else "none")
        )
        open_tasks = {
            pid: sum(
                1
                for t in state.tasks.values()
                if t.assignee == pid and t.status not in ("done", "cancelled")
            )
            for pid in candidates
        }
        lines.append(
            "Roster for this role:\n"
            + "\n".join(
                f"- {pid} ({role}, open tasks: {open_tasks[pid]})"
                for pid, role in candidates.items()
            )
        )
        if attempt:
            lines.append(
                f"Your previous proposal for this failure was rejected ({attempt} so far); "
                "propose something else."
            )
        return "\n".join(lines)


class _Exhausted:
    """Sentinel: every attempt for this failure was rejected; a person must act."""

    status = "exhausted"
    resolution = None


__all__ = ["MAX_ATTEMPTS", "TRIAGE_APPROVE_SYSTEM", "TRIAGE_SYSTEM", "Triage"]
