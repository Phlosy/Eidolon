"""Cost provenance (R4-c): from a ledger line back to the work that caused it, and
from a project forward to everything it cost.

    money.entry.posted ← money.cost.recorded ← agent.model.called ← agent.step.requested
                                                     ↓ correlation
                                             agent.run.started ← work.task.started /
                                                                  work.task.submitted /
                                                                  work.project.* / work.mission.*
                                                                  / institution.decision.*
                                                                  (whatever called for the run)
                                                     ↓ correlation of that cause
                                             project_id / mission_id / decision_id

Only causation and correlation are followed, so the walk is the same whether it
starts from the fact log, the journal projection, or a bundle. Nothing here is
stored: every answer is a query.
"""

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from eidolon.agent import run as R
from eidolon.agent.providers.accounting import KIND as MODEL_CALLED
from eidolon.kernel.facts import Fact
from eidolon.kernel.store import FactStore
from eidolon.money import facts as F
from eidolon.money.projection import AccountRow, JournalRow, run_spend_micro
from eidolon.work import facts as WF
from eidolon.work.projection import TaskRow


class TraceError(LookupError):
    pass


@dataclass
class CostTrace:
    """One line of spend explained. ``entry`` is None when tracing an unposted cost."""

    entry: Fact | None
    cost: Fact
    model_call: Fact
    step: Fact | None = None  # agent.step.requested
    run_started: Fact | None = None
    cause: Fact | None = None  # what called for the run (a work / institution fact)
    run_id: UUID | None = None
    task_id: UUID | None = None
    project_id: UUID | None = None
    mission_id: UUID | None = None
    decision_id: UUID | None = None
    task_title: str | None = None
    missing: list[str] = field(default_factory=list)  # links that could not be followed

    @property
    def booked_micro(self) -> int | None:
        booked = self.cost.payload.get("booked")
        return booked["amount_micro"] if booked else None

    @property
    def chain(self) -> list[Fact]:
        """The facts on the causal path, cause first."""
        return [
            f
            for f in (
                self.cause,
                self.run_started,
                self.step,
                self.model_call,
                self.cost,
                self.entry,
            )
            if f is not None
        ]

    def as_dict(self) -> dict[str, Any]:
        return {
            "entry_fact_id": str(self.entry.id) if self.entry else None,
            "entry_id": self.entry.payload["entry_id"] if self.entry else None,
            "cost_fact_id": str(self.cost.id),
            "model_call_fact_id": str(self.model_call.id),
            "step_fact_id": str(self.step.id) if self.step else None,
            "run_started_fact_id": str(self.run_started.id) if self.run_started else None,
            "cause_fact_id": str(self.cause.id) if self.cause else None,
            "cause_kind": self.cause.kind if self.cause else None,
            "run_id": str(self.run_id) if self.run_id else None,
            "person_id": self.run_started.payload["person_id"] if self.run_started else None,
            "task_id": str(self.task_id) if self.task_id else None,
            "task_title": self.task_title,
            "project_id": str(self.project_id) if self.project_id else None,
            "mission_id": str(self.mission_id) if self.mission_id else None,
            "decision_id": str(self.decision_id) if self.decision_id else None,
            "model": self.model_call.payload["model"],
            "provider": self.model_call.payload["provider"],
            "mode": self.model_call.payload.get("mode"),
            "usage": self.model_call.payload["usage"],
            "source": self.cost.payload.get("source"),
            "booked": self.cost.payload.get("booked"),
            "posted": self.cost.payload["posted"],
            "reason": self.cost.payload.get("reason"),
            "missing": list(self.missing),
        }


def resolve_start(db: Session, ref: UUID, *, store: FactStore | None = None) -> Fact:
    """``ref`` may be a fact id (entry / cost / model call) or a journal ``entry_id``."""
    store = store or FactStore()
    fact = store.get(db, ref)
    if fact is None:
        row = db.scalar(select(JournalRow).where(JournalRow.entry_id == ref))
        if row is not None:
            fact = store.get(db, row.entry_fact_id)
    if fact is None:
        raise TraceError(f"no fact or journal entry {ref}")
    if fact.kind not in (F.ENTRY_POSTED, F.COST_RECORDED, MODEL_CALLED):
        raise TraceError(
            f"{ref} is a {fact.kind}; a cost trace starts from {F.ENTRY_POSTED}, "
            f"{F.COST_RECORDED} or {MODEL_CALLED}"
        )
    return fact


def cost_trace(db: Session, ref: UUID, *, store: FactStore | None = None) -> CostTrace:
    store = store or FactStore()
    start = resolve_start(db, ref, store=store)

    entry = cost = call = None
    if start.kind == F.ENTRY_POSTED:
        entry = start
        src = entry.payload.get("source") or {}
        if src.get("kind") != F.COST_RECORDED:
            raise TraceError(f"entry {entry.payload['entry_id']} does not book a model-call cost")
        cost = store.get(db, UUID(src["fact_id"]))
        if cost is None:
            raise TraceError(f"entry books missing cost fact {src['fact_id']}")
    elif start.kind == F.COST_RECORDED:
        cost = start
    else:
        call = start
        cost = next(
            (
                f
                for f in store.by_correlation(db, call.correlation_id, kinds=[F.COST_RECORDED])
                if f.payload["model_call_fact_id"] == str(call.id)
            ),
            None,
        )
        if cost is None:
            raise TraceError(f"model call {call.id} has not been metered yet")
    if call is None:
        call = store.get(db, UUID(cost.payload["model_call_fact_id"]))
        if call is None:
            raise TraceError(f"cost {cost.id} names missing model call")
    if entry is None and cost.payload["posted"]:
        entry = next(
            (
                f
                for f in store.by_correlation(db, cost.correlation_id, kinds=[F.ENTRY_POSTED])
                if (f.payload.get("source") or {}).get("fact_id") == str(cost.id)
            ),
            None,
        )
    trace = CostTrace(entry, cost, call)

    # call ← step (causation)
    if call.causation_id is not None:
        trace.step = store.get(db, call.causation_id)
        if trace.step is None or trace.step.kind != R.STEP_REQUESTED:
            trace.missing.append(f"step {call.causation_id}")
            trace.step = None
    else:
        trace.missing.append("step: the call was made outside a run")

    # run (correlation of the call)
    run_id = _uuid(cost.payload.get("run_id")) or _uuid(call.payload.get("run_id"))
    if run_id is not None:
        trace.run_id = run_id
        started = store.by_correlation(db, run_id, kinds=[R.RUN_STARTED])
        if started:
            trace.run_started = started[0]
            trace.task_id = _uuid(started[0].payload.get("task_id"))
            if started[0].causation_id is not None:
                trace.cause = store.get(db, started[0].causation_id)
                if trace.cause is None:
                    trace.missing.append(f"cause {started[0].causation_id}")
            else:
                trace.missing.append("cause: the run was started with no causing fact")
        else:
            trace.missing.append(f"run {run_id} has no agent.run.started fact")
    else:
        trace.missing.append("run: the call carried no run_id")

    # project / mission / decision: the correlation of whatever called for the run
    cause = trace.cause
    if cause is not None:
        if cause.kind.startswith("work.mission."):
            trace.mission_id = cause.correlation_id
        elif cause.kind.startswith("work."):
            trace.project_id = cause.correlation_id
        elif cause.kind.startswith("institution.decision."):
            trace.decision_id = cause.correlation_id
    if trace.task_id is not None:
        if trace.project_id is None:
            row = db.get(TaskRow, trace.task_id)
            if row is not None:
                trace.project_id = row.project_id
        if trace.project_id is not None:
            created = next(
                (
                    f
                    for f in store.by_correlation(db, trace.project_id, kinds=[WF.TASK_CREATED])
                    if f.payload["task_id"] == str(trace.task_id)
                ),
                None,
            )
            if created is not None:
                trace.task_title = created.payload["title"]
    return trace


# -- project roll-up ------------------------------------------------------------------


@dataclass
class RunCost:
    run_id: UUID
    person_id: str | None
    micro: int


@dataclass
class TaskCost:
    task_id: UUID
    title: str
    status: str
    micro: int
    runs: list[RunCost] = field(default_factory=list)


@dataclass
class ProjectCosts:
    project_id: UUID
    currency: str | None
    tasks: list[TaskCost]
    management: list[RunCost]  # runs the project called for that belong to no task

    @property
    def task_micro(self) -> int:
        return sum(t.micro for t in self.tasks)

    @property
    def management_micro(self) -> int:
        return sum(r.micro for r in self.management)

    @property
    def total_micro(self) -> int:
        return self.task_micro + self.management_micro

    def as_dict(self) -> dict[str, Any]:
        return {
            "project_id": str(self.project_id),
            "currency": self.currency,
            "total_micro": self.total_micro,
            "task_micro": self.task_micro,
            "management_micro": self.management_micro,
            "tasks": [
                {
                    "task_id": str(t.task_id),
                    "title": t.title,
                    "status": t.status,
                    "micro": t.micro,
                    "runs": [
                        {"run_id": str(r.run_id), "person_id": r.person_id, "micro": r.micro}
                        for r in t.runs
                    ],
                }
                for t in self.tasks
            ],
            "management": [
                {"run_id": str(r.run_id), "person_id": r.person_id, "micro": r.micro}
                for r in self.management
            ],
        }


def project_costs(db: Session, project_id: UUID, *, store: FactStore | None = None) -> ProjectCosts:
    """What a project cost so far: per task, per run, plus the management runs the
    project itself called for (planning, review, assignment) that carry no task."""
    store = store or FactStore()
    tasks = list(
        db.scalars(
            select(TaskRow).where(TaskRow.project_id == project_id).order_by(TaskRow.created_at)
        )
    )
    company_id = tasks[0].company_id if tasks else None
    currency = None
    out: list[TaskCost] = []
    for task in tasks:
        rows = db.execute(
            select(JournalRow.run_id, JournalRow.amount_micro, JournalRow.currency)
            .join(
                AccountRow,
                (AccountRow.company_id == JournalRow.company_id)
                & (AccountRow.code == JournalRow.account_code),
            )
            .where(
                JournalRow.task_id == task.task_id,
                JournalRow.side == "debit",
                AccountRow.kind == "expense",
            )
            .order_by(JournalRow.last_seq)
        ).all()
        by_run: dict[UUID | None, int] = {}
        for run_id, micro, cur in rows:
            currency = currency or cur
            by_run[run_id] = by_run.get(run_id, 0) + micro
        runs = [RunCost(rid, _person(db, store, rid), micro) for rid, micro in by_run.items()]
        out.append(TaskCost(task.task_id, task.title, task.status, sum(by_run.values()), runs))

    management: list[RunCost] = []
    if company_id is not None:
        caused_by_project = {f.id for f in store.by_correlation(db, project_id)}
        for started in store.stream(
            db, kinds=[R.RUN_STARTED], company_id=company_id, limit=100_000
        ):
            if started.causation_id in caused_by_project and not started.payload.get("task_id"):
                run_id = UUID(started.payload["run_id"])
                micro = run_spend_micro(db, run_id)
                if micro:
                    management.append(RunCost(run_id, started.payload.get("person_id"), micro))
    return ProjectCosts(project_id, currency, out, management)


def ledger_fact_ids(db: Session, project_id: UUID, *, store: FactStore | None = None) -> list[UUID]:
    """The ``money.*`` facts behind a project's spend (entries and the costs they
    book), for a bundle that should carry the ledger with the work."""
    costs = project_costs(db, project_id, store=store)
    run_ids = [r.run_id for t in costs.tasks for r in t.runs] + [r.run_id for r in costs.management]
    ids: list[UUID] = []
    seen: set[UUID] = set()
    for run_id in run_ids:
        if run_id is None:
            continue
        for row in db.scalars(
            select(JournalRow).where(JournalRow.run_id == run_id).order_by(JournalRow.last_seq)
        ):
            for fid in (row.cause_fact_id, row.entry_fact_id):
                if fid is not None and fid not in seen:
                    seen.add(fid)
                    ids.append(fid)
    return ids


def _person(db: Session, store: FactStore, run_id: UUID | None) -> str | None:
    if run_id is None:
        return None
    started = store.by_correlation(db, run_id, kinds=[R.RUN_STARTED])
    return started[0].payload.get("person_id") if started else None


def _uuid(value) -> UUID | None:
    if not value:
        return None
    try:
        return UUID(str(value))
    except ValueError:
        return None
