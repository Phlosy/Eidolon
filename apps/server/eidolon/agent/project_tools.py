"""Eyes and hands for management agents (R7-c): read-only tools over one project.

A manager deciding about a project — the Tech Lead reviewing a plan or assigning a task,
the CEO briefing the Owner, the PM proposing delivery, a spend approver — should be able
to look at what actually exists instead of judging from a paragraph of prose. These tools
let a decision run see the project's state and artifacts, read files in its tasks'
workspaces and run their tests. Nothing here writes: no shell, no write_file. Every tool
carries a side-effect level and the authority it requires, so the institution's tool gate
(`tool_gate`) decides per role what the agent may touch — the same door the engineers'
runner tools go through.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy.orm import sessionmaker

from eidolon.agent.blobs import BlobMissing, BlobStore
from eidolon.agent.providers.base import ToolSpec
from eidolon.agent.tools import Execution, FunctionTools, ToolCall, ToolResult
from eidolon.agent.tools.registry import ToolDescriptor, ToolRegistry
from eidolon.work.service import WorkService
from eidolon.work.state import ProjectState, TaskState

MAX_CHARS = 16_000
TOOLS_HINT = (
    " You can look before you decide: the tools let you read the project's status and "
    "artifacts, list and read files in its tasks' workspaces, and run their tests. You "
    "cannot change anything. Use them when the facts matter; then give your final answer."
)


class WorkspaceSource(Protocol):
    def for_task(self, company_id: UUID, project_id: UUID, task_id: UUID) -> Path: ...


def _obj(props: dict[str, Any], required: Iterable[str] = ()) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": props,
        "required": list(required),
        "additionalProperties": False,
    }


TASK_ARG = {"type": "string", "description": "task key (t1, t2, …) or exact title"}
DESCRIPTORS: tuple[ToolDescriptor, ...] = (
    ToolDescriptor(
        ToolSpec(
            "project_status",
            "The project's tasks (status, assignee, reviews, rework count) and its artifacts.",
            _obj({}),
        ),
        "READ",
        "workspace.read",
    ),
    ToolDescriptor(
        ToolSpec(
            "list_artifacts",
            "Files the tasks produced: path, which task, size, media type.",
            _obj({}),
        ),
        "READ",
        "workspace.read",
    ),
    ToolDescriptor(
        ToolSpec(
            "read_artifact",
            "The content of a produced file, by its path (the latest one with that path).",
            _obj({"path": {"type": "string"}}, ["path"]),
        ),
        "READ",
        "workspace.read",
    ),
    ToolDescriptor(
        ToolSpec(
            "list_dir",
            "List a directory in a task's workspace (depth 1-4).",
            _obj(
                {
                    "task": TASK_ARG,
                    "path": {"type": "string", "default": "."},
                    "depth": {"type": "integer", "minimum": 1, "maximum": 4, "default": 2},
                },
                ["task"],
            ),
        ),
        "READ",
        "workspace.read",
    ),
    ToolDescriptor(
        ToolSpec(
            "read_file",
            "Read a file in a task's workspace (optionally a line range).",
            _obj(
                {
                    "task": TASK_ARG,
                    "path": {"type": "string"},
                    "offset": {"type": "integer", "minimum": 0, "default": 0},
                    "limit": {"type": "integer", "minimum": 1, "default": 400},
                },
                ["task", "path"],
            ),
        ),
        "READ",
        "workspace.read",
    ),
    ToolDescriptor(
        ToolSpec(
            "run_tests",
            "Run pytest in a task's workspace (sandboxed runner); returns the output.",
            _obj({"task": TASK_ARG, "path": {"type": "string", "default": "."}}, ["task"]),
        ),
        "WRITE",  # tests execute code; the runner marks it the same way
        "workspace.execute",
    ),
)
NAMES = tuple(d.name for d in DESCRIPTORS)


@dataclass
class ProjectTools:
    """``Tools`` for one project. State is re-read from facts on every call, so a
    long decision run sees tasks finishing while it thinks."""

    session_factory: sessionmaker
    svc: WorkService
    blobs: BlobStore
    workspaces: WorkspaceSource
    project_id: UUID
    runner_factory: Any | None = None  # tests: async (workspace) -> Tools; default RunnerTools
    registry: ToolRegistry = field(default_factory=ToolRegistry)
    _fn: FunctionTools = field(default_factory=FunctionTools)

    def __post_init__(self) -> None:
        self.registry.extend(DESCRIPTORS)
        handlers = {
            "project_status": self.project_status,
            "list_artifacts": self.list_artifacts,
            "read_artifact": self.read_artifact,
            "list_dir": self.list_dir,
            "read_file": self.read_file,
            "run_tests": self.run_tests,
        }
        for d in DESCRIPTORS:
            self._fn.add(d.spec, handlers[d.name])

    # -- Tools protocol --------------------------------------------------------------

    def specs(self) -> tuple[ToolSpec, ...]:
        return self.registry.specs()

    async def execute(self, execution: Execution) -> ToolResult:
        return await self._fn.execute(execution)

    async def close(self) -> None:
        return None

    # -- helpers -----------------------------------------------------------------------

    def _state(self) -> ProjectState:
        with self.session_factory() as db:
            return self.svc.project(db, self.project_id)

    def _task(self, state: ProjectState, ref: str) -> TaskState:
        ref = str(ref).strip()
        keyed = task_keys(state)
        for key, t in keyed.items():
            if ref == key or ref == str(t.task_id) or ref == t.title:
                return t
        titles = [t for t in keyed.values() if t.title.lower() == ref.lower()]
        if len(titles) == 1:
            return titles[0]
        raise LookupError(
            f"no task {ref!r}; tasks: " + ", ".join(f"{k} = {t.title}" for k, t in keyed.items())
        )

    def _workspace(self, state: ProjectState, task: TaskState) -> Path:
        # resolved once: on macOS the temp root is a symlink (/var → /private/var), and
        # every path we hand back is relative to this root, never the host's absolute one
        return self.workspaces.for_task(state.company_id, state.project_id, task.task_id).resolve()

    def _inside(self, workspace: Path, rel: str) -> Path:
        target = (workspace / (rel or ".")).resolve()
        if target != workspace and workspace not in target.parents:
            raise PermissionError(f"{rel!r} is outside the task workspace")
        return target

    @staticmethod
    def _bound(text: str) -> str:
        if len(text) <= MAX_CHARS:
            return text
        return text[:MAX_CHARS] + f"\n… [truncated, {len(text) - MAX_CHARS} more characters]"

    # -- tools ------------------------------------------------------------------------------

    def project_status(self) -> dict[str, Any]:
        s = self._state()
        return {
            "project": s.title,
            "status": s.status,
            "budget_units": s.budget,
            "manager": s.manager,
            "tasks": [
                {
                    "key": key,
                    "title": t.title,
                    "status": t.status,
                    "assignee": t.assignee,
                    "role": t.role,
                    "rework_count": t.rework_count,
                    "depends_on": [state_title(s, d) for d in t.depends_on],
                    "reviews": [
                        {
                            "reviewer": r.get("reviewer"),
                            "verdict": r.get("verdict"),
                            "reason": r.get("reason"),
                        }
                        for r in t.reviews
                    ],
                    "acceptance_criteria": t.acceptance_criteria,
                }
                for key, t in task_keys(s).items()
            ],
            "artifacts": [a.path for a in s.artifacts.values()],
        }

    def list_artifacts(self) -> list[dict[str, Any]]:
        s = self._state()
        return [
            {
                "path": a.path,
                "task": state_title(s, a.task_id),
                "size": a.size,
                "media_type": a.media_type,
            }
            for a in sorted(s.artifacts.values(), key=lambda a: (a.created_at or 0, a.path))
        ]

    def read_artifact(self, path: str) -> str:
        s = self._state()
        matches = [a for a in s.artifacts.values() if a.path == path]
        if not matches:
            raise LookupError(f"no artifact at {path!r}; see list_artifacts")
        art = sorted(matches, key=lambda a: a.created_at or 0)[-1]
        try:
            data = self.blobs.get_bytes(art.blob_ref)
        except BlobMissing:
            return f"[content of {path} is no longer stored]"
        return self._bound(data.decode("utf-8", errors="replace"))

    def list_dir(self, task: str, path: str = ".", depth: int = 2) -> str:
        s = self._state()
        t = self._task(s, task)
        ws = self._workspace(s, t)
        root = self._inside(ws, path)
        if not root.exists():
            return f"{path}: does not exist"
        lines: list[str] = []

        def walk(directory: Path, level: int) -> None:
            for child in sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name)):
                if child.name in {"__pycache__", ".pytest_cache", ".git"}:
                    continue
                rel = child.relative_to(ws)
                lines.append(f"{rel}/" if child.is_dir() else f"{rel} ({child.stat().st_size} B)")
                if child.is_dir() and level < depth:
                    walk(child, level + 1)

        walk(root, 1) if root.is_dir() else lines.append(str(root.relative_to(ws)))
        return self._bound("\n".join(lines) or "(empty)")

    def read_file(self, task: str, path: str, offset: int = 0, limit: int = 400) -> str:
        s = self._state()
        t = self._task(s, task)
        ws = self._workspace(s, t)
        target = self._inside(ws, path)
        if not target.is_file():
            raise FileNotFoundError(f"{path} is not a file in the workspace of {t.title!r}")
        text = target.read_text(encoding="utf-8", errors="replace").splitlines()
        chunk = text[offset : offset + limit]
        head = f"[{path}: lines {offset + 1}-{offset + len(chunk)} of {len(text)}]\n"
        return self._bound(head + "\n".join(chunk))

    async def run_tests(
        self, task: str, path: str = ".", _execution: Execution | None = None
    ) -> str:
        s = self._state()
        t = self._task(s, task)
        ws = self._workspace(s, t)
        self._inside(ws, path)
        if self.runner_factory is not None:
            tools = await self.runner_factory(ws)
        else:
            from eidolon.agent.runner import RunnerTools

            tools = await RunnerTools.start(ws)
        try:
            call = ToolCall(
                _execution.call.id if _execution else "run_tests", "run_tests", {"path": path}
            )
            result = await tools.execute(
                Execution(
                    call,
                    (
                        _execution.idempotency_key
                        if _execution
                        else f"{self.project_id}:{t.task_id}:tests"
                    ),
                    _execution.run_id if _execution else "",
                    _execution.step_no if _execution else 0,
                )
            )
        finally:
            close = getattr(tools, "close", None)
            if close:
                await close()
        return result.content if not result.is_error else f"[error] {result.content}"


def task_keys(state: ProjectState) -> dict[str, TaskState]:
    """Stable, id-free handles (t1, t2, …) in creation order. Ids never reach the model:
    whatever it echoes back into a tool call must be identical on replay."""
    # ``state.tasks`` keeps fact order (created in the plan's order): stable across replays
    return {f"t{i}": t for i, t in enumerate(state.tasks.values(), start=1)}


def state_title(state: ProjectState, task_id: UUID) -> str:
    t = state.tasks.get(task_id)
    return t.title if t else str(task_id)


def as_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


__all__ = ["DESCRIPTORS", "NAMES", "TOOLS_HINT", "ProjectTools", "WorkspaceSource", "task_keys"]
