"""Worker-side execution of work: launching, running and reviewing tasks.

    RunLauncher        consumer of work.run.requested → work.task.started (a short
                       transaction; never holds the company chain lock across a run)
    TaskRunExecutor    job: every in_progress task whose run is not finished gets its
                       AgentRunner started or resumed; a completed run turns the
                       workspace files into artifacts and submits the task (in_review);
                       a failed run fails the task — nothing is retried on its own (R5)
    ReviewExecutor     job: every in_review task gets a task.review decision opened,
                       a Reviewer agent run whose JSON verdict the reviewer *signs*,
                       and the recorded verdict applied (RV1–RV8)

Jobs are idempotent per tick: each step is a fact with a key derived from the
task/run/decision, so a crash anywhere resumes from the log.
"""

import asyncio
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from eidolon.agent import run as R
from eidolon.agent.blobs import BlobStore
from eidolon.agent.loop import AgentRunner
from eidolon.agent.memory import MemoryRecall
from eidolon.agent.prompts import ROLES
from eidolon.agent.providers.base import Provider
from eidolon.agent.runner import RunnerTools
from eidolon.institution import (
    GateContext,
    InstitutionTemplate,
    conclude,
    load,
    request,
    sign,
    tool_gate,
)
from eidolon.institution.adoption import current as current_institution
from eidolon.kernel.facts import Actor, ActorKind, Fact
from eidolon.kernel.store import FactStore
from eidolon.logging import get_logger
from eidolon.work import facts as F
from eidolon.work.projection import TaskRow
from eidolon.work.service import WorkService
from eidolon.work.state import ProjectState, TaskState

log = get_logger("eidolon.agent.scheduler")
SKIP_DIRS = {".eidolon", "__pycache__", ".git", ".pytest_cache"}
EXCERPT = 2000


# -- launcher (consumer) -------------------------------------------------------------------


@dataclass
class RunLauncher:
    """``work.run.requested`` → ``work.task.started``. Pure bookkeeping, fast."""

    svc: WorkService = field(default_factory=WorkService)
    name: str = "run_launcher"
    handles: frozenset[str] = frozenset({F.RUN_REQUESTED})

    def handle(self, db: Session, fact: Fact) -> None:
        p = fact.payload
        project_id, task_id, run_id = UUID(p["project_id"]), UUID(p["task_id"]), UUID(p["run_id"])
        state = self.svc.project(db, project_id)
        task = state.task(task_id)
        if task.pending_run_id != run_id or task.status != "todo":
            return  # superseded (cancelled, re-planned, already started)
        self.svc.start_task(
            db, project_id, task_id, Actor.system(), run_id=run_id, person_id=p["assignee"]
        )
        log.info(
            "task.launched",
            extra={"task_id": str(task_id), "run_id": str(run_id), "assignee": p["assignee"]},
        )


# -- shared ---------------------------------------------------------------------------------


@dataclass
class Workspaces:
    root: Path

    def for_task(self, company_id: UUID, project_id: UUID, task_id: UUID) -> Path:
        path = self.root / str(company_id) / str(project_id) / str(task_id)
        path.mkdir(parents=True, exist_ok=True)
        return path


def task_prompt(
    state: ProjectState, task: TaskState, blobs: BlobStore, svc: WorkService | None = None
) -> str:
    """The engineer's brief: the task, its criteria, and the *content* of the inputs
    it declared (H6: handoff carries content, not just a pointer)."""
    parts = [f"Task: {task.title}", task.description.strip()]
    if task.acceptance_criteria:
        parts.append(
            "Acceptance criteria:\n" + "\n".join(f"- {c}" for c in task.acceptance_criteria)
        )
    inputs = [
        a
        for a in state.artifacts.values()
        if a.task_id in task.inputs and state.tasks[a.task_id].status == "done"
    ]
    if inputs:
        lines = []
        for art in sorted(inputs, key=lambda a: a.path):
            try:
                text = blobs.get_bytes(art.blob_ref).decode("utf-8", "replace")
            except Exception:  # noqa: BLE001 - a missing blob is reported, not fatal
                text = "(content unavailable)"
            lines.append(
                f"--- {art.path} (from task '{state.tasks[art.task_id].title}') ---\n"
                f"{text[:EXCERPT]}"
            )
        parts.append("Inputs from earlier tasks:\n" + "\n".join(lines))
    parts.append(
        "The workspace is yours. When the work is done, reply with a short summary of the "
        "files you created or changed."
    )
    return "\n\n".join(parts)


def collect_artifacts(workspace: Path) -> list[tuple[str, bytes]]:
    out = []
    for path in sorted(workspace.rglob("*")):
        if not path.is_file() or any(
            part in SKIP_DIRS or part.startswith(".") for part in path.relative_to(workspace).parts
        ):
            continue
        out.append((str(path.relative_to(workspace)), path.read_bytes()))
    return out


def artifact_id_for(run_id: UUID, path: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"eidolon:run:{run_id}:artifact:{path}")


# -- task runs (job) ---------------------------------------------------------------------------


@dataclass
class TaskRunExecutor:
    session_factory: sessionmaker
    provider: Provider
    blobs: BlobStore
    workspaces: Workspaces
    svc: WorkService = field(default_factory=WorkService)
    store: FactStore = field(default_factory=FactStore)
    memory: MemoryRecall | None = None
    model: str | None = None
    max_steps: int = 16
    max_tokens_per_step: int = 4096
    tools_factory: Callable[[Path], Any] | None = None  # for tests; default RunnerTools
    name: str = "task_runs"

    def tick(self) -> int:
        """Run or resume every in-progress task once. Returns how many were touched."""
        with self.session_factory() as db:
            rows = db.scalars(
                select(TaskRow).where(
                    TaskRow.status == "in_progress", TaskRow.current_run_id.is_not(None)
                )
            ).all()
            pending = [(r.company_id, r.project_id, r.task_id, r.current_run_id) for r in rows]
        touched = 0
        for company_id, project_id, task_id, run_id in pending:
            if self._execute(company_id, project_id, task_id, run_id):
                touched += 1
        return touched

    def _execute(self, company_id: UUID, project_id: UUID, task_id: UUID, run_id: UUID) -> bool:
        with self.session_factory() as db:
            state = self.svc.project(db, project_id)
            task = state.task(task_id)
            if task.status != "in_progress" or task.current_run_id != run_id:
                return False  # projection lag; the fold is the truth
            template = _template(db, company_id)
        workspace = self.workspaces.for_task(company_id, project_id, task_id)
        outcome = asyncio.run(
            self._run(
                db_state=state, task=task, run_id=run_id, workspace=workspace, template=template
            )
        )
        with self.session_factory() as db:
            state = self.svc.project(db, project_id)
            task = state.task(task_id)
            if task.status != "in_progress" or task.current_run_id != run_id:
                return True
            if outcome.status == "completed":
                self._submit(db, state, task, run_id, workspace, outcome.final_text or "")
            else:
                self.svc.fail_task(
                    db,
                    project_id,
                    task_id,
                    Actor.system(),
                    run_id=run_id,
                    reason=f"run {outcome.status}: {outcome.error or ''}",
                )
            db.commit()
        log.info(
            "task.run_finished",
            extra={"task_id": str(task_id), "run_id": str(run_id), "status": outcome.status},
        )
        return True

    async def _run(
        self,
        *,
        db_state: ProjectState,
        task: TaskState,
        run_id: UUID,
        workspace: Path,
        template: InstitutionTemplate | None,
    ) -> R.RunOutcome:
        role = task.role or "engineer"
        tools = await (
            self.tools_factory(workspace) if self.tools_factory else RunnerTools.start(workspace)
        )
        try:
            gate = (
                tool_gate(template, role, tools.registry)
                if template is not None and hasattr(tools, "registry")
                else None
            )
            runner = AgentRunner(
                self.session_factory,
                self.provider,
                self.blobs,
                tools,
                memory=self.memory,
                tool_gate=gate,
            )
            existing = _run_exists(self.session_factory, self.store, run_id)
            if existing:
                return await runner.resume(run_id)
            spec = R.RunSpec(
                run_id=run_id,
                company_id=db_state.company_id,
                person_id=task.assignee or "unassigned",
                model=self.model or _default_model(),
                system=ROLES.get(role, ROLES["engineer"]),
                prompt=task_prompt(db_state, task, self.blobs),
                task_id=str(task.task_id),
                budget=R.RunBudget(max_steps=self.max_steps),
                max_tokens_per_step=self.max_tokens_per_step,
                temperature=0.0,
            )
            return await runner.start(spec)
        finally:
            close = getattr(tools, "close", None)
            if close:
                await close()

    def _submit(
        self,
        db: Session,
        state: ProjectState,
        task: TaskState,
        run_id: UUID,
        workspace: Path,
        summary: str,
    ) -> None:
        actor = Actor(ActorKind.agent, task.assignee or "unassigned")
        ids: list[UUID] = []
        for path, data in collect_artifacts(workspace):
            ref = self.blobs.put_bytes(data)
            aid = artifact_id_for(run_id, path)
            if aid not in state.artifacts:
                self.svc.produce_artifact(
                    db,
                    state.project_id,
                    task.task_id,
                    actor,
                    run_id=run_id,
                    path=path,
                    blob_ref=ref,
                    size=len(data),
                    media_type=_media(path),
                    artifact_id=aid,
                )
            ids.append(aid)
        # consumption of declared inputs is recorded with this run (H3)
        for art in state.artifacts.values():
            if art.task_id in task.inputs and state.tasks[art.task_id].status == "done":
                if not any(u.get("run_id") == str(run_id) for u in art.uses):
                    self.svc.consume_artifact(
                        db,
                        state.project_id,
                        art.artifact_id,
                        actor,
                        by_task_id=task.task_id,
                        run_id=run_id,
                    )
        state = self.svc.project(db, state.project_id)  # refresh so submit sees the artifacts
        self.svc.submit_task(
            db,
            state.project_id,
            task.task_id,
            actor,
            run_id=run_id,
            artifact_ids=ids,
            summary=summary,
        )


# -- reviews (job) ---------------------------------------------------------------------------------


_JSON = re.compile(r"\{.*\}", re.DOTALL)


def parse_verdict(text: str) -> tuple[str, str] | None:
    match = _JSON.search(text.strip().replace("```json", "").replace("```", ""))
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return None
    verdict = str(data.get("verdict", "")).lower()
    if verdict not in F.REVIEW_VERDICTS:
        return None
    return verdict, str(data.get("reason") or "").strip()[:2000]


def review_prompt(state: ProjectState, task: TaskState, blobs: BlobStore) -> str:
    parts = [f"Review task: {task.title}", task.description.strip()]
    if task.acceptance_criteria:
        parts.append(
            "Acceptance criteria:\n" + "\n".join(f"- {c}" for c in task.acceptance_criteria)
        )
    submitted = [
        a
        for a in state.artifacts.values()
        if a.task_id == task.task_id and a.run_id == task.current_run_id
    ]
    if submitted:
        lines = []
        for art in sorted(submitted, key=lambda a: a.path):
            try:
                text = blobs.get_bytes(art.blob_ref).decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                text = "(content unavailable)"
            lines.append(f"--- {art.path} ---\n{text[:EXCERPT]}")
        parts.append("Submitted files (also present in the workspace):\n" + "\n".join(lines))
    else:
        parts.append("No files were submitted.")
    parts.append(
        "Check the work against the criteria (run the tests with run_tests if any), then "
        "answer with the JSON verdict only."
    )
    return "\n\n".join(parts)


@dataclass
class ReviewExecutor:
    session_factory: sessionmaker
    provider: Provider
    blobs: BlobStore
    workspaces: Workspaces
    reviewer: str = "qa-1"
    role: str = "qa"
    svc: WorkService = field(default_factory=WorkService)
    store: FactStore = field(default_factory=FactStore)
    model: str | None = None
    max_steps: int = 8
    tools_factory: Callable[[Path], Any] | None = None
    name: str = "reviews"

    def tick(self) -> int:
        with self.session_factory() as db:
            rows = db.scalars(select(TaskRow).where(TaskRow.status == "in_review")).all()
            pending = [(r.company_id, r.project_id, r.task_id) for r in rows]
        touched = 0
        for company_id, project_id, task_id in pending:
            if self._review(company_id, project_id, task_id):
                touched += 1
        return touched

    def _review(self, company_id: UUID, project_id: UUID, task_id: UUID) -> bool:
        with self.session_factory() as db:
            state = self.svc.project(db, project_id)
            task = state.task(task_id)
            if task.status != "in_review":
                return False
            template = _template(db, company_id)
            if template is None:
                log.warning(
                    "review.skipped",
                    extra={"task_id": str(task_id), "reason": "no institution adopted"},
                )
                return False
            decision_id = uuid5(
                NAMESPACE_URL, f"eidolon:task:{task_id}:review:{task.current_run_id}"
            )
            try:
                decision = load(db, decision_id, store=self.store)
            except Exception:  # noqa: BLE001 - not requested yet
                requested, _ = request(
                    template,
                    GateContext(
                        "task.review",
                        company_id,
                        author=task.assignee,
                        subject={"task_id": str(task_id), "run_id": str(task.current_run_id)},
                    ),
                    Actor.system(),
                    decision_id=decision_id,
                    causation_id=task.last_fact_id,
                )
                self.store.append(db, requested)
                db.commit()
                decision = load(db, decision_id, store=self.store)
            if decision.status == "pending" and not decision.signatures:
                if self.role not in decision.resolution.allowed_roles:
                    log.warning(
                        "review.unassignable",
                        extra={
                            "task_id": str(task_id),
                            "allowed": list(decision.resolution.allowed_roles),
                        },
                    )
                    return False
                if task.assignee == self.reviewer:
                    log.warning("review.separation", extra={"task_id": str(task_id)})
                    return False
                verdict = self._run_reviewer(state, task, template, decision_id)
                if verdict is None:
                    return False  # RV1: no verdict means no verdict; the task waits
                v, reason = verdict
                signed = sign(
                    template,
                    decision,
                    Actor(ActorKind.agent, self.reviewer),
                    role=self.role,
                    verdict={"pass": "approve", "rework": "rework", "escalate": "reject"}[v],
                    reason=reason or v,
                )
                self.store.append(db, signed)
                db.commit()
                decision = load(db, decision_id, store=self.store)
            recorded = conclude(decision)
            if recorded is not None:
                self.store.append(db, recorded)
                db.commit()
                decision = load(db, decision_id, store=self.store)
            if decision.status == "pending":
                return False
            verdict = {"approved": "pass", "rework": "rework", "rejected": "escalate"}[
                decision.status
            ]
            reason = decision.signatures[-1].reason if decision.signatures else None
            self.svc.review_task(
                db,
                project_id,
                task_id,
                Actor(ActorKind.agent, self.reviewer),
                decision_id=decision_id,
                verdict=verdict,
                reviewer=self.reviewer,
                reason=reason,
            )
            db.commit()
            log.info("task.reviewed", extra={"task_id": str(task_id), "verdict": verdict})
            return True

    def _run_reviewer(
        self, state: ProjectState, task: TaskState, template: InstitutionTemplate, decision_id: UUID
    ) -> tuple[str, str] | None:
        workspace = self.workspaces.for_task(state.company_id, state.project_id, task.task_id)
        run_id = uuid5(NAMESPACE_URL, f"eidolon:decision:{decision_id}:reviewer-run")

        async def go():
            tools = await (
                self.tools_factory(workspace)
                if self.tools_factory
                else RunnerTools.start(workspace)
            )
            try:
                gate = (
                    tool_gate(template, self.role, tools.registry)
                    if hasattr(tools, "registry")
                    else None
                )
                runner = AgentRunner(
                    self.session_factory, self.provider, self.blobs, tools, tool_gate=gate
                )
                if _run_exists(self.session_factory, self.store, run_id):
                    return await runner.resume(run_id)
                spec = R.RunSpec(
                    run_id=run_id,
                    company_id=state.company_id,
                    person_id=self.reviewer,
                    model=self.model or _default_model(),
                    system=ROLES["qa"],
                    prompt=review_prompt(state, task, self.blobs),
                    task_id=str(task.task_id),
                    budget=R.RunBudget(max_steps=self.max_steps),
                    max_tokens_per_step=1024,
                    temperature=0.0,
                )
                return await runner.start(spec)
            finally:
                close = getattr(tools, "close", None)
                if close:
                    await close()

        outcome = asyncio.run(go())
        if outcome.status != "completed" or not outcome.final_text:
            log.warning(
                "review.run_failed", extra={"task_id": str(task.task_id), "status": outcome.status}
            )
            return None
        parsed = parse_verdict(outcome.final_text)
        if parsed is None:
            log.warning(
                "review.unparsable",
                extra={"task_id": str(task.task_id), "text": outcome.final_text[:200]},
            )
        return parsed


# -- helpers --------------------------------------------------------------------------------------


def _template(db: Session, company_id: UUID) -> InstitutionTemplate | None:
    state = current_institution(db, company_id)
    return state.template if state else None


def _run_exists(factory: sessionmaker, store: FactStore, run_id: UUID) -> bool:
    with factory() as db:
        return bool(store.by_correlation(db, run_id, kinds=[R.RUN_STARTED]))


def _default_model() -> str:
    from eidolon.agent.providers import default_model

    return default_model()


def _media(path: str) -> str:
    return "text/x-python" if path.endswith(".py") else "text/plain"


__all__ = [
    "ReviewExecutor",
    "RunLauncher",
    "TaskRunExecutor",
    "Workspaces",
    "collect_artifacts",
    "parse_verdict",
    "review_prompt",
    "task_prompt",
]
