"""Manager agents on the same loop (§4.8, R3-e): CEO, PM and Tech Lead decide
through the decision envelope exactly like QA reviews — an agent run produces a
structured answer, the agent *signs* the decision, the system records it.

    MissionIntake   proposed missions → mission.accept decided by the CEO agent;
                    an accepted mission gets its project (derived, not judged)
    Planner         projects in planning with no tasks → the PM agent writes the
                    plan (tasks, dependencies, inputs), tasks are created in the
                    PM's name, plan.approve is opened and decided by the Tech Lead
    Assigner        READY unassigned tasks → the Tech Lead agent picks from the
                    roster; task.assign is opened with that assignee and signed

Who holds which role comes from a ``Roster`` until identity/employment lands (R4).
"""

import asyncio
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from eidolon.agent import run as R
from eidolon.agent.blobs import BlobStore
from eidolon.agent.loop import AgentRunner
from eidolon.agent.project_tools import TOOLS_HINT, ProjectTools
from eidolon.agent.providers.base import Provider
from eidolon.agent.tools import FunctionTools
from eidolon.institution import (
    REQUESTED,
    DecisionError,
    DecisionState,
    GateContext,
    InstitutionTemplate,
    conclude,
    load,
    request,
    sign,
)
from eidolon.institution.adoption import current as current_institution
from eidolon.institution.authority import tool_gate
from eidolon.institution.schema import MICRO_PER_UNIT
from eidolon.kernel.facts import Actor, ActorKind
from eidolon.kernel.store import FactStore
from eidolon.logging import get_logger
from eidolon.work import graph
from eidolon.work.projection import MissionRow, ProjectRow, TaskRow
from eidolon.work.service import WorkService
from eidolon.work.state import ProjectState

log = get_logger("eidolon.agent.managers")
_JSON = re.compile(r"\{.*\}", re.DOTALL)

CEO_SYSTEM = (
    "You are the CEO of a small software studio. You decide whether the studio takes on a "
    "mission. Judge feasibility, clarity and value against the budget. Answer with JSON only: "
    '{"verdict": "approve" | "reject", "reason": "<one paragraph>"}.'
)
PM_SYSTEM = (
    "You are the product manager of a small software studio. Turn a mission into a plan of "
    "2 to 4 small, concrete engineering tasks for Python engineers working in separate "
    "workspaces. Each task must be doable alone with the standard library in one sitting. "
    "Use depends_on for ordering and inputs for tasks that need another task's files "
    "(inputs must be among depends_on). Each task has a kind: build (code and tests), "
    "document (a Markdown write-up for a named reader), design (a Markdown design decision) or "
    "research (Markdown findings); add a document or design task only when the mission asks "
    "for one. Answer with JSON only: "
    '{"tasks": [{"key": "t1", "title": "...", "description": "...", '
    '"acceptance_criteria": ["..."], "depends_on": ["t0"], "inputs": ["t0"], '
    '"role": "engineer", "kind": "build"}], "rationale": "<one paragraph>"}.'
)
TECH_LEAD_PLAN_SYSTEM = (
    "You are the tech lead of a small software studio reviewing a project plan. Approve it if "
    "the tasks are concrete, ordered sensibly and each is doable alone; otherwise ask for "
    'rework with specific objections. Answer with JSON only: {"verdict": "approve" | "rework", '
    '"reason": "<one paragraph>"}.'
)
TECH_LEAD_ASSIGN_SYSTEM = (
    "You are the tech lead of a small software studio assigning a task. Pick exactly one "
    "person from the roster whose role fits the task and who has the fewest open tasks. "
    'Answer with JSON only: {"assignee": "<person id from the roster>", '
    '"reason": "<one sentence>"}.'
)


@dataclass(frozen=True, slots=True)
class Person:
    person_id: str
    role: str


@dataclass
class Roster:
    people: list[Person]

    def holding(self, role: str) -> list[Person]:
        return [p for p in self.people if p.role == role]

    def one(self, role: str) -> Person:
        held = self.holding(role)
        if not held:
            raise LookupError(f"nobody holds role {role!r}")
        return held[0]

    def role_of(self, person_id: str) -> str | None:
        return next((p.role for p in self.people if p.person_id == person_id), None)

    def as_text(self, roles: tuple[str, ...] = ("engineer",)) -> str:
        return "\n".join(f"- {p.person_id} ({p.role})" for p in self.people if p.role in roles)


class RosterSource(Protocol):
    """Who holds which role in a company, as of now."""

    def for_company(self, db: Session, company_id: UUID) -> Roster: ...


@dataclass
class StaticRoster:
    roster: Roster

    def for_company(self, db: Session, company_id: UUID) -> Roster:
        return self.roster


class IdentityRoster:
    """The roster is the company's active employments (R4-a): no configuration,
    no second source of truth."""

    def __init__(self, service=None):
        from eidolon.identity import IdentityService

        self.service = service or IdentityService()

    def for_company(self, db: Session, company_id: UUID) -> Roster:
        try:
            state = self.service.company(db, company_id)
        except Exception:  # noqa: BLE001 - company not created through identity (tests / legacy)
            return Roster([])
        return Roster([Person(e.person_id, e.role) for e in state.active()])


def parse_json(text: str) -> dict[str, Any] | None:
    match = _JSON.search(text.strip().replace("```json", "").replace("```", ""))
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


@dataclass
class Deciders:
    """Runs a role's agent for one structured answer and signs decisions. With
    ``workspaces`` set (R7-c) a decision about a project gets read-only eyes on it:
    status, artifacts, workspace files, tests — gated by the role's authority."""

    session_factory: sessionmaker
    provider: Provider
    blobs: BlobStore
    model: str | None = None
    workspaces: Any | None = None  # WorkspaceSource; None = decide from the prompt alone
    svc: WorkService = field(default_factory=WorkService)
    max_steps_with_tools: int = 8

    def ask(
        self,
        *,
        company_id: UUID,
        person: Person,
        system: str,
        prompt: str,
        run_key: str,
        task_id: str | None = None,
        max_tokens: int = 4096,  # thinking-mode models spend output tokens before the JSON
        max_attempts: int = 3,
        causation_id: UUID | None = None,  # what called for the decision (R4-c cost trace)
        project_id: UUID | None = None,  # R7-c: the project the agent may look at
        template: InstitutionTemplate | None = None,  # gates the tools; caps the run's money
    ) -> dict[str, Any] | None:
        """One structured answer from ``person``'s agent. The run id derives from
        ``run_key`` and an attempt number: a completed run is reused (replay/resume),
        a failed one gets a fresh attempt, up to ``max_attempts``."""
        store = FactStore()
        tools, gate = self._eyes(project_id, template, person)
        if tools is not None:
            system = system + TOOLS_HINT
        budget = R.RunBudget(max_steps=self.max_steps_with_tools if tools else 2)
        if template is not None:
            with self.session_factory() as db:
                currency = _company_currency(db, company_id)
            budget = R.RunBudget(
                max_steps=budget.max_steps,
                max_cost_micro=template.budget_policy.run_default * MICRO_PER_UNIT,
                cost_currency=currency,
            )

        def existing(run_id: UUID) -> str | None:
            with self.session_factory() as db:
                facts = store.by_correlation(db, run_id, kinds=R.RUN_KINDS)
            return R.RunState.from_facts(run_id, facts).status if facts else None

        attempt, run_id = 1, uuid5(NAMESPACE_URL, f"eidolon:decision-run:{run_key}:1")
        while (
            existing(run_id) in ("failed", "budget_exceeded", "cancelled")
            and attempt < max_attempts
        ):
            attempt += 1
            run_id = uuid5(NAMESPACE_URL, f"eidolon:decision-run:{run_key}:{attempt}")

        async def go():
            runner = AgentRunner(
                self.session_factory,
                self.provider,
                self.blobs,
                tools if tools is not None else FunctionTools(),
                tool_gate=gate,
            )
            try:
                if existing(run_id) is not None:
                    return await runner.resume(run_id)
                spec = R.RunSpec(
                    run_id=run_id,
                    company_id=company_id,
                    person_id=person.person_id,
                    model=self.model or _default_model(),
                    system=system,
                    prompt=prompt,
                    task_id=task_id,
                    budget=budget,
                    max_tokens_per_step=max_tokens,
                    temperature=0.0,
                    causation_id=causation_id,
                )
                return await runner.start(spec)
            finally:
                if tools is not None:
                    await tools.close()

        outcome = asyncio.run(go())
        if outcome.status != "completed" or not outcome.final_text:
            log.warning(
                "decision.run_failed", extra={"person": person.person_id, "status": outcome.status}
            )
            return None
        data = parse_json(outcome.final_text)
        if data is None:
            log.warning(
                "decision.unparsable",
                extra={"person": person.person_id, "text": outcome.final_text[:200]},
            )
        return data

    def _eyes(self, project_id: UUID | None, template: InstitutionTemplate | None, person: Person):
        """Read-only project tools plus the role's gate — or (None, None) when there is
        no project to look at or no workspace root configured."""
        if project_id is None or self.workspaces is None:
            return None, None
        tools = ProjectTools(
            self.session_factory, self.svc, self.blobs, self.workspaces, project_id
        )
        gate = tool_gate(template, person.role, tools.registry) if template is not None else None
        return tools, gate

    def sign_and_conclude(
        self,
        db: Session,
        template: InstitutionTemplate,
        decision: DecisionState,
        person: Person,
        verdict: str,
        reason: str,
    ) -> DecisionState:
        store = FactStore()
        store.append(
            db,
            sign(
                template,
                decision,
                Actor(ActorKind.agent, person.person_id),
                role=person.role,
                verdict=verdict,
                reason=reason or verdict,
            ),
        )
        db.commit()
        decision = load(db, decision.decision_id, store=store)
        recorded = conclude(decision)
        if recorded is not None:
            store.append(db, recorded)
            db.commit()
            decision = load(db, decision.decision_id, store=store)
        return decision


def _company_currency(db: Session, company_id: UUID) -> str | None:
    from eidolon.identity import IdentityError, IdentityService

    try:
        return IdentityService().company(db, company_id).currency
    except IdentityError:
        return None


def _template(db: Session, company_id: UUID) -> InstitutionTemplate | None:
    state = current_institution(db, company_id)
    return state.template if state else None


def _open(
    db: Session,
    template: InstitutionTemplate,
    ctx: GateContext,
    decision_id: UUID,
    actor: Actor,
    causation_id=None,
) -> DecisionState:
    store = FactStore()
    try:
        return load(db, decision_id, store=store)
    except DecisionError:
        requested, _ = request(
            template, ctx, actor, decision_id=decision_id, causation_id=causation_id
        )
        store.append(db, requested)
        db.commit()
        return load(db, decision_id, store=store)


# -- CEO: mission intake -------------------------------------------------------------------------


@dataclass
class MissionIntake:
    session_factory: sessionmaker
    deciders: Deciders
    rosters: RosterSource
    svc: WorkService = field(default_factory=WorkService)
    name: str = "mission_intake"

    def tick(self) -> int:
        with self.session_factory() as db:
            rows = db.scalars(select(MissionRow).where(MissionRow.status == "proposed")).all()
            pending = [(r.company_id, r.mission_id) for r in rows]
        return sum(1 for company_id, mission_id in pending if self._decide(company_id, mission_id))

    def _decide(self, company_id: UUID, mission_id: UUID) -> bool:
        with self.session_factory() as db:
            mission = self.svc.mission(db, mission_id)
            if mission.status != "proposed":
                return False
            template = _template(db, company_id)
            if template is None:
                return False
            roster = self.rosters.for_company(db, company_id)
            if not roster.holding("ceo") or not roster.holding("pm"):
                log.info("mission.waiting_for_management", extra={"mission_id": str(mission_id)})
                return False
            ceo = roster.one("ceo")
            decision_id = uuid5(NAMESPACE_URL, f"eidolon:mission:{mission_id}:accept")
            ctx = GateContext(
                "mission.accept",
                company_id,
                proposer="owner",
                subject={"mission_id": str(mission_id)},
                amount=mission.budget or 0,
            )
            decision = _open(
                db, template, ctx, decision_id, Actor.system(), causation_id=mission.last_fact_id
            )
            if decision.status == "pending" and (
                decision.resolution.decider != "agent"
                or ceo.role not in decision.resolution.allowed_roles
            ):
                # escalated to someone else, or a human gate: whoever decides it, the
                # ``decided`` branch below applies the outcome next tick
                log.info(
                    "mission.needs_human",
                    extra={"mission_id": str(mission_id), "decider": decision.resolution.decider},
                )
                return False
            if decision.status == "pending" and not decision.signatures:
                prompt = (
                    f"Mission: {mission.title}\n\nGoal:\n{mission.goal}\n\n"
                    + (
                        "Success criteria:\n"
                        + "\n".join(f"- {c}" for c in mission.success_criteria)
                        + "\n\n"
                        if mission.success_criteria
                        else ""
                    )
                    + (
                        "Constraints:\n" + "\n".join(f"- {c}" for c in mission.constraints) + "\n\n"
                        if mission.constraints
                        else ""
                    )
                    + f"Budget: {mission.budget if mission.budget is not None else 'unspecified'}"
                    + " units."
                )
                answer = self.deciders.ask(
                    company_id=company_id,
                    person=ceo,
                    system=CEO_SYSTEM,
                    prompt=prompt,
                    run_key=f"mission:{mission_id}:ceo",
                    causation_id=mission.last_fact_id,
                )
                if not answer or answer.get("verdict") not in ("approve", "reject"):
                    return False
                decision = self.deciders.sign_and_conclude(
                    db, template, decision, ceo, answer["verdict"], str(answer.get("reason", ""))
                )
            if decision.status == "pending":
                return False
            fact = self.svc.decide_mission(
                db,
                mission_id,
                Actor(ActorKind.agent, ceo.person_id),
                decision_id=decision_id,
                reason=decision.signatures[-1].reason if decision.signatures else None,
            )
            db.commit()
            if decision.status == "approved":
                pm = roster.one("pm")
                project_id = uuid5(NAMESPACE_URL, f"eidolon:mission:{mission_id}:project")
                try:
                    self.svc.create_project(
                        db,
                        company_id,
                        Actor.system(),
                        title=mission.title,
                        budget=mission.budget or template.budget_policy.project_default,
                        mission_id=mission_id,
                        manager=pm.person_id,
                        project_id=project_id,
                    )
                    db.commit()
                except Exception as exc:  # noqa: BLE001 - already created (idempotent key) or invalid
                    log.info(
                        "project.create_skipped",
                        extra={"mission_id": str(mission_id), "error": str(exc)},
                    )
            log.info(
                "mission.decided",
                extra={
                    "mission_id": str(mission_id),
                    "status": decision.status,
                    "fact": str(fact.id),
                },
            )
            return True


# -- PM + Tech Lead: planning ----------------------------------------------------------------------


@dataclass
class Planner:
    session_factory: sessionmaker
    deciders: Deciders
    rosters: RosterSource
    svc: WorkService = field(default_factory=WorkService)
    name: str = "planner"

    def tick(self) -> int:
        with self.session_factory() as db:
            rows = db.scalars(select(ProjectRow).where(ProjectRow.status == "planning")).all()
            pending = [(r.company_id, r.project_id) for r in rows]
        return sum(1 for company_id, project_id in pending if self._plan(company_id, project_id))

    def _plan(self, company_id: UUID, project_id: UUID) -> bool:
        with self.session_factory() as db:
            state = self.svc.project(db, project_id)
            if state.status != "planning":
                return False
            template = _template(db, company_id)
            if template is None:
                return False
            roster = self.rosters.for_company(db, company_id)
            if not roster.holding("pm") or not roster.holding("tech_lead"):
                log.info("plan.waiting_for_management", extra={"project_id": str(project_id)})
                return False
            pm = roster.one("pm")
            tl = roster.one("tech_lead")
            mission = self.svc.mission(db, state.mission_id) if state.mission_id else None
            if not state.tasks:
                brief = f"Project: {state.title}\nBudget: {state.budget} units.\n"
                if mission:
                    brief += f"\nMission goal:\n{mission.goal}\n"
                    if mission.success_criteria:
                        brief += (
                            "\nSuccess criteria:\n"
                            + "\n".join(f"- {c}" for c in mission.success_criteria)
                            + "\n"
                        )
                    if mission.constraints:
                        brief += (
                            "\nConstraints:\n"
                            + "\n".join(f"- {c}" for c in mission.constraints)
                            + "\n"
                        )
                brief += "\nEngineers available: " + ", ".join(
                    p.person_id for p in roster.holding("engineer")
                )
                answer = self.deciders.ask(
                    company_id=company_id,
                    person=pm,
                    system=PM_SYSTEM,
                    prompt=brief,
                    run_key=f"project:{project_id}:plan",
                    max_tokens=2048,
                    causation_id=state.last_fact_id,
                )
                tasks = (answer or {}).get("tasks")
                if not isinstance(tasks, list) or not tasks:
                    return False
                if not self._create_tasks(db, state, pm, tasks):
                    return False
                state = self.svc.project(db, project_id)
            decision_id = uuid5(
                NAMESPACE_URL, f"eidolon:project:{project_id}:plan:{len(state.tasks)}"
            )
            ctx = GateContext(
                "plan.approve",
                company_id,
                proposer=pm.person_id,
                proposer_role=pm.role,
                subject={"project_id": str(project_id)},
                facts={"tasks_count": len(state.tasks)},
            )
            decision = _open(
                db,
                template,
                ctx,
                decision_id,
                Actor(ActorKind.agent, pm.person_id),
                causation_id=state.last_fact_id,
            )
            if decision.status == "pending" and (
                decision.resolution.decider != "agent"
                or tl.role not in decision.resolution.allowed_roles
            ):
                log.info(
                    "plan.needs_other_decider",
                    extra={
                        "project_id": str(project_id),
                        "resolution": decision.resolution.as_dict(),
                    },
                )
                return False
            if decision.status == "pending" and not any(
                s.person_id == tl.person_id for s in decision.signatures
            ):
                listing = "\n".join(
                    f"- {t.title}: {t.description} | "
                    f"criteria: {'; '.join(t.acceptance_criteria)} | "
                    f"depends on: {', '.join(state.tasks[d].title for d in t.depends_on) or '-'}"
                    for t in state.tasks.values()
                )
                prompt = f"Project: {state.title}\n\nProposed tasks:\n{listing}"
                answer = self.deciders.ask(
                    company_id=company_id,
                    person=tl,
                    system=TECH_LEAD_PLAN_SYSTEM,
                    prompt=prompt,
                    run_key=f"project:{project_id}:plan-review:{len(state.tasks)}",
                    causation_id=state.last_fact_id,
                    project_id=project_id,
                    template=template,
                )
                if not answer or answer.get("verdict") not in ("approve", "rework"):
                    return False
                decision = self.deciders.sign_and_conclude(
                    db, template, decision, tl, answer["verdict"], str(answer.get("reason", ""))
                )
            if decision.status == "approved":
                self.svc.approve_plan(
                    db, project_id, Actor(ActorKind.agent, pm.person_id), decision_id=decision_id
                )
                db.commit()
                log.info(
                    "plan.approved",
                    extra={"project_id": str(project_id), "tasks": len(state.tasks)},
                )
                return True
            if decision.status == "rework":
                log.warning(
                    "plan.rework",
                    extra={"project_id": str(project_id), "reason": decision.signatures[-1].reason},
                )
            return False

    def _create_tasks(self, db: Session, state: ProjectState, pm: Person, tasks: list) -> bool:
        keys = {}
        for i, t in enumerate(tasks):
            key = str(t.get("key") or f"t{i + 1}")
            keys[key] = uuid5(NAMESPACE_URL, f"eidolon:project:{state.project_id}:task:{key}")
        actor = Actor(ActorKind.agent, pm.person_id)
        for i, t in enumerate(tasks):
            key = str(t.get("key") or f"t{i + 1}")
            deps = [keys[d] for d in t.get("depends_on", []) if d in keys and d != key]
            inputs = [keys[d] for d in t.get("inputs", []) if d in keys and d != key]
            inputs = [d for d in inputs if d in deps] or inputs
            try:
                self.svc.create_task(
                    db,
                    state.project_id,
                    actor,
                    task_id=keys[key],
                    title=str(t.get("title") or key)[:300],
                    description=str(t.get("description") or ""),
                    acceptance_criteria=[str(c) for c in t.get("acceptance_criteria", [])][:10],
                    depends_on=deps,
                    inputs=inputs,
                    role=str(t.get("role") or "engineer"),
                    kind=str(t.get("kind") or "build").lower(),
                )
            except (graph.DagError, ValueError) as exc:
                log.warning(
                    "plan.task_rejected",
                    extra={"project_id": str(state.project_id), "task": key, "error": str(exc)},
                )
                db.rollback()
                return False
        db.commit()
        return True


# -- Tech Lead: assignment -------------------------------------------------------------------------


@dataclass
class Assigner:
    session_factory: sessionmaker
    deciders: Deciders
    rosters: RosterSource
    svc: WorkService = field(default_factory=WorkService)
    name: str = "assigner"

    def tick(self) -> int:
        with self.session_factory() as db:
            rows = db.scalars(
                select(TaskRow).where(TaskRow.status == "todo", TaskRow.assignee.is_(None))
            ).all()
            pending = sorted({(r.company_id, r.project_id) for r in rows})
        done = 0
        for company_id, project_id in pending:
            done += self._assign_project(company_id, project_id)
        return done

    def _assign_project(self, company_id: UUID, project_id: UUID) -> int:
        with self.session_factory() as db:
            state = self.svc.project(db, project_id)
            if state.status != "active":
                return 0
            template = _template(db, company_id)
            if template is None:
                return 0
            roster = self.rosters.for_company(db, company_id)
            if not roster.holding("tech_lead"):
                return 0
            tl = roster.one("tech_lead")
            open_counts: dict[str, int] = {}
            for t in state.tasks.values():
                if t.assignee and t.status not in ("done", "cancelled"):
                    open_counts[t.assignee] = open_counts.get(t.assignee, 0) + 1
            done = 0
            for task in graph.awaiting_assignment(state.nodes()):
                ts = state.tasks[task.task_id]
                roster_lines = "\n".join(
                    f"- {p.person_id} ({p.role}, open tasks: {open_counts.get(p.person_id, 0)})"
                    for p in roster.holding(ts.role or "engineer")
                )
                prompt = f"Task: {ts.title}\n{ts.description}\n\nRoster:\n{roster_lines}"
                answer = self.deciders.ask(
                    company_id=company_id,
                    person=tl,
                    system=TECH_LEAD_ASSIGN_SYSTEM,
                    prompt=prompt,
                    run_key=f"task:{ts.task_id}:assign:{ts.last_fact_id}",
                    task_id=str(ts.task_id),
                    causation_id=ts.last_fact_id,
                    project_id=state.project_id,
                    template=template,
                )
                assignee = (answer or {}).get("assignee")
                if not assignee or roster.role_of(str(assignee)) is None:
                    continue
                decision_id = uuid5(
                    NAMESPACE_URL, f"eidolon:task:{ts.task_id}:assign:{ts.last_fact_id}"
                )
                ctx = GateContext(
                    "task.assign",
                    company_id,
                    proposer=tl.person_id,
                    subject={"task_id": str(ts.task_id), "assignee": str(assignee)},
                )
                decision = _open(
                    db,
                    template,
                    ctx,
                    decision_id,
                    Actor(ActorKind.agent, tl.person_id),
                    causation_id=ts.last_fact_id,
                )
                if decision.status == "pending" and not decision.signatures:
                    if tl.role not in decision.resolution.allowed_roles:
                        continue
                    decision = self.deciders.sign_and_conclude(
                        db, template, decision, tl, "approve", str(answer.get("reason", ""))
                    )
                if decision.status != "approved":
                    continue
                self.svc.assign_task(
                    db,
                    project_id,
                    ts.task_id,
                    Actor(ActorKind.agent, tl.person_id),
                    assignee=str(assignee),
                    decision_id=decision_id,
                )
                db.commit()
                open_counts[str(assignee)] = open_counts.get(str(assignee), 0) + 1
                done += 1
                log.info("task.assigned", extra={"task_id": str(ts.task_id), "assignee": assignee})
            return done


def _default_model() -> str:
    from eidolon.agent.providers import default_model

    return default_model()


def default_roster(config: Mapping[str, str] | None = None) -> Roster:
    """Until R4: one agent per studio role, ids by convention."""
    return Roster(
        [
            Person("ceo-1", "ceo"),
            Person("pm-1", "pm"),
            Person("tl-1", "tech_lead"),
            Person("eng-1", "engineer"),
            Person("eng-2", "engineer"),
            Person("qa-1", "qa"),
        ]
    )


__all__ = [
    "Assigner",
    "Deciders",
    "MissionIntake",
    "Person",
    "Planner",
    "Roster",
    "default_roster",
    "parse_json",
]


# -- describing a decision to an agent (R6-b / R7-a) ------------------------------------------


def decision_project(db: Session, d: DecisionState) -> UUID | None:
    """The project a decision is about, from its subject (project_id, or the task's)."""
    from eidolon.work.projection import TaskRow

    if d.subject.get("project_id"):
        try:
            return UUID(str(d.subject["project_id"]))
        except ValueError:
            return None
    if d.subject.get("task_id"):
        try:
            row = db.get(TaskRow, UUID(str(d.subject["task_id"])))
        except ValueError:
            return None
        return row.project_id if row else None
    return None


def describe_decision(db: Session, svc: WorkService, d: DecisionState) -> str:
    """The gate, the subject, and what we know about the work it names — the prompt
    both the escalated decider and the advisor read."""
    import json as _json

    from eidolon.work.facts import WorkError
    from eidolon.work.projection import TaskRow

    plain = {k: v for k, v in dict(d.subject).items() if k != "action_hash"}
    lines = [
        f"Gate: {d.gate}" + (f" (escalated {d.escalations}×)" if d.escalations else ""),
        f"Subject: {_json.dumps(plain, ensure_ascii=False)}",
    ]
    if d.proposer:
        lines.append(f"Proposed by: {d.proposer}")
    if d.amount is not None:
        lines.append(f"Amount: {d.amount} units")
    project_id = d.subject.get("project_id")
    task_id = d.subject.get("task_id")
    try:
        if project_id is None and task_id is not None:
            row = db.get(TaskRow, UUID(str(task_id)))
            project_id = row.project_id if row else None
        if project_id is not None:
            state = svc.project(db, UUID(str(project_id)))
            lines.append(f"Project: {state.title} ({state.status}), budget {state.budget} units")
            for t in state.tasks.values():
                mark = " <- this one" if str(t.task_id) == str(task_id) else ""
                lines.append(f"- {t.title} [{t.status}] assignee={t.assignee}{mark}")
                if str(t.task_id) == str(task_id):
                    lines.append(f"  {t.description}")
                    if t.acceptance_criteria:
                        lines.append("  criteria: " + "; ".join(t.acceptance_criteria))
                    for r in t.reviews:
                        lines.append(f"  review by {r.get('reviewer')}: {r.get('verdict')}")
            if state.artifacts:
                lines.append(
                    "Artifacts: " + ", ".join(sorted({a.path for a in state.artifacts.values()}))
                )
        mission_id = d.subject.get("mission_id")
        if mission_id:
            m = svc.mission(db, UUID(str(mission_id)))
            lines.append(f"Mission: {m.title} — {m.goal}")
    except (WorkError, ValueError):
        pass
    return "\n".join(lines)


# -- spend approval (R6-a) ----------------------------------------------------------------------

SPEND_SYSTEM = (
    "You are the project manager of a software studio. A project has reached the part of "
    "its budget where every further tranche of spend needs your approval. Judge whether the "
    "spend so far is proportionate to progress and whether the next tranche is worth it. "
    'Answer with JSON only: {"verdict": "approve" | "reject", "reason": "<one sentence>"}.'
)


@dataclass
class SpendApprover:
    """``spend.approve`` decisions in the agent tier: the role the gate names (PM in
    software-studio-v1) looks at the project's spend and signs. Human tiers wait in
    the decisions inbox; the system tier was recorded by the guard itself."""

    session_factory: sessionmaker
    deciders: Deciders
    rosters: RosterSource
    svc: WorkService = field(default_factory=WorkService)
    store: FactStore = field(default_factory=FactStore)
    name: str = "spend_approver"

    def tick(self) -> int:
        with self.session_factory() as db:
            requested = self.store.stream(db, kinds=[REQUESTED], limit=100_000)
            pending = [
                (f.company_id, f.correlation_id)
                for f in requested
                if f.payload.get("gate") == "spend.approve"
                and f.payload.get("resolution", {}).get("decider") == "agent"
            ]
        done = 0
        for company_id, decision_id in pending:
            if self._approve(company_id, decision_id):
                done += 1
        return done

    def _approve(self, company_id: UUID, decision_id: UUID) -> bool:
        with self.session_factory() as db:
            decision = load(db, decision_id, store=self.store)
            if decision.status != "pending" or decision.signatures:
                return False
            template = _template(db, company_id)
            if template is None:
                return False
            roster = self.rosters.for_company(db, company_id)
            signer = next(
                (p for p in roster.people if p.role in decision.resolution.allowed_roles), None
            )
            if signer is None:
                return False
            s = decision.subject
            project_id = UUID(s["project_id"])
            state = self.svc.project(db, project_id)
            tasks = "\n".join(f"- {t.title} [{t.status}]" for t in state.tasks.values())
            prompt = (
                f"Project: {state.title}\nBudget: {s.get('budget_units')} units. "
                f"Spent so far: {s.get('spent_units')} units. "
                f"Requested tranche: {s.get('tranche_units')} units "
                f"(tranche #{s.get('tranche')}).\n"
                f"Tasks:\n{tasks}"
            )
            answer = self.deciders.ask(
                company_id=company_id,
                person=signer,
                system=SPEND_SYSTEM,
                prompt=prompt,
                run_key=f"decision:{decision_id}:spend",
                causation_id=decision.last_fact_id,
                project_id=project_id,
                template=template,
            )
            if not answer or answer.get("verdict") not in ("approve", "reject"):
                return False
            decision = self.deciders.sign_and_conclude(
                db, template, decision, signer, answer["verdict"], str(answer.get("reason", ""))
            )
            log.info(
                "spend.decided",
                extra={"decision_id": str(decision_id), "status": decision.status},
            )
            return True
