"""A run is nothing but its facts (v2-architecture §4.2).

``RunSpec`` is what a caller asks for. ``RunState`` is derived by folding the
run's ``agent.*`` facts in order — the only persistent state; crash recovery is
``RunState.from_facts`` followed by "continue". Full contexts, responses and
tool results are blobs referenced from the facts by hash.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from eidolon.agent.blobs import BlobStore
from eidolon.agent.providers.base import Message, ToolSpec, Usage
from eidolon.kernel.facts import Fact

# -- fact vocabulary of a run -----------------------------------------------------

RUN_STARTED = "agent.run.started"
STEP_REQUESTED = "agent.step.requested"
STEP_COMPLETED = "agent.step.completed"
TOOL_REQUESTED = "agent.tool.requested"
TOOL_COMPLETED = "agent.tool.completed"
RUN_COMPLETED = "agent.run.completed"
RUN_FAILED = "agent.run.failed"
RUN_CANCEL_REQUESTED = "agent.run.cancel_requested"
RUN_CANCELLED = "agent.run.cancelled"
RUN_BUDGET_EXCEEDED = "agent.run.budget_exceeded"

RUN_KINDS = frozenset(
    {
        RUN_STARTED,
        STEP_REQUESTED,
        STEP_COMPLETED,
        TOOL_REQUESTED,
        TOOL_COMPLETED,
        RUN_COMPLETED,
        RUN_FAILED,
        RUN_CANCEL_REQUESTED,
        RUN_CANCELLED,
        RUN_BUDGET_EXCEEDED,
    }
)
TERMINAL = frozenset({"completed", "failed", "cancelled", "budget_exceeded"})


@dataclass(frozen=True, slots=True)
class RunBudget:
    max_steps: int = 20
    max_total_tokens: int | None = None  # input + output across the run

    def as_dict(self) -> dict[str, Any]:
        return {"max_steps": self.max_steps, "max_total_tokens": self.max_total_tokens}


@dataclass(frozen=True, slots=True)
class RunSpec:
    run_id: UUID
    company_id: UUID
    person_id: str  # the agent's identity; becomes the actor of every fact
    model: str
    system: str
    prompt: str  # the first user message
    task_id: str | None = None
    budget: RunBudget = RunBudget()
    max_tokens_per_step: int = 4096
    temperature: float | None = None
    causation_id: UUID | None = None  # the fact that called for this run (R4-c trace)


@dataclass(frozen=True, slots=True)
class PendingTool:
    call_id: str
    name: str
    idempotency_key: str


@dataclass
class RunState:
    run_id: UUID
    company_id: UUID
    person_id: str
    model: str
    system_ref: str
    prompt_ref: str
    tool_names: tuple[str, ...]
    budget: RunBudget
    max_tokens_per_step: int
    temperature: float | None
    task_id: str | None
    status: str = "running"
    cancel_requested: bool = False
    steps_completed: int = 0
    last_step_no: int = 0
    last_attempt: int = 0
    pending_step: tuple[int, int] | None = None  # (step_no, attempt) requested, not completed
    current_calls: list[dict[str, Any]] = field(default_factory=list)  # from last step.completed
    tools_requested: dict[str, str] = field(default_factory=dict)  # call_id -> idempotency_key
    tools_completed: set[str] = field(default_factory=set)
    history_refs: list[tuple[str, str]] = field(default_factory=list)  # (assistant|tool, ref)
    usage_total: Usage = Usage()
    last_fact_id: UUID | None = None
    last_stop_reason: str | None = None
    final_ref: str | None = None
    error: str | None = None

    # -- derived views ----------------------------------------------------------

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL

    def pending_tools(self) -> list[PendingTool]:
        """Tool calls of the last completed step that have no ``tool.completed`` yet,
        in the model's order (requested or not)."""
        out = []
        for call in self.current_calls:
            if call["id"] in self.tools_completed:
                continue
            key = self.tools_requested.get(call["id"]) or tool_key(
                self.run_id, self.last_step_no, call["id"]
            )
            out.append(PendingTool(call["id"], call["name"], key))
        return out

    def messages(self, blobs: BlobStore) -> tuple[Message, ...]:
        """The conversation so far: initial messages, then each step's assistant turn
        and the tool results that followed it."""
        out = [Message.from_dict(m) for m in blobs.get_json(self.prompt_ref)]
        for _role, ref in self.history_refs:
            out.append(Message.from_dict(blobs.get_json(ref)))
        return tuple(out)

    def system(self, blobs: BlobStore) -> str:
        return blobs.get_json(self.system_ref)

    # -- folding ----------------------------------------------------------------

    @classmethod
    def from_facts(cls, run_id: UUID, facts: Iterable[Fact]) -> "RunState":
        state: RunState | None = None
        for fact in sorted(facts, key=lambda f: f.seq):
            if fact.kind not in RUN_KINDS:
                continue
            p = fact.payload
            if p.get("run_id") != str(run_id):
                continue
            if fact.kind == RUN_STARTED:
                state = cls(
                    run_id=run_id,
                    company_id=fact.company_id,
                    person_id=p["person_id"],
                    model=p["model"],
                    system_ref=p["system_ref"],
                    prompt_ref=p["prompt_ref"],
                    tool_names=tuple(p["tools"]),
                    budget=RunBudget(**p["budget"]),
                    max_tokens_per_step=p["max_tokens_per_step"],
                    temperature=p.get("temperature"),
                    task_id=p.get("task_id"),
                )
            elif state is None:
                raise ValueError(f"run {run_id}: {fact.kind} before {RUN_STARTED}")
            elif fact.kind == STEP_REQUESTED:
                state.pending_step = (p["step_no"], p["attempt"])
                state.last_step_no = p["step_no"]
                state.last_attempt = p["attempt"]
            elif fact.kind == STEP_COMPLETED:
                state.pending_step = None
                state.steps_completed += 1
                state.last_step_no = p["step_no"]
                state.last_attempt = p["attempt"]
                state.current_calls = list(p["tool_calls"])
                state.tools_requested = {}
                state.tools_completed = set()
                state.history_refs.append(("assistant", p["response_ref"]))
                state.last_stop_reason = p["stop_reason"]
                u = p["usage"]
                state.usage_total = Usage(
                    state.usage_total.input_tokens + u["input_tokens"],
                    state.usage_total.output_tokens + u["output_tokens"],
                    state.usage_total.cache_read_tokens + u["cache_read_tokens"],
                    state.usage_total.cache_write_tokens + u["cache_write_tokens"],
                )
            elif fact.kind == TOOL_REQUESTED:
                state.tools_requested[p["call_id"]] = p["idempotency_key"]
            elif fact.kind == TOOL_COMPLETED:
                state.tools_completed.add(p["call_id"])
                state.history_refs.append(("tool", p["result_ref"]))
            elif fact.kind == RUN_COMPLETED:
                state.status = "completed"
                state.final_ref = p.get("final_ref")
            elif fact.kind == RUN_FAILED:
                state.status = "failed"
                state.error = p.get("error")
            elif fact.kind == RUN_CANCEL_REQUESTED:
                state.cancel_requested = True
            elif fact.kind == RUN_CANCELLED:
                state.status = "cancelled"
            elif fact.kind == RUN_BUDGET_EXCEEDED:
                state.status = "budget_exceeded"
            state.last_fact_id = fact.id
        if state is None:
            raise ValueError(f"run {run_id}: no {RUN_STARTED} fact")
        return state


@dataclass(frozen=True, slots=True)
class RunOutcome:
    run_id: UUID
    status: str
    steps: int
    final_text: str | None
    usage: Usage
    error: str | None = None


# -- idempotency keys: pure functions of run / step / call -------------------------


def step_key(run_id: UUID, step_no: int, attempt: int, phase: str) -> str:
    return f"run:{run_id}:step:{step_no}:{attempt}:{phase}"


def tool_key(run_id: UUID, step_no: int, call_id: str) -> str:
    return f"run:{run_id}:step:{step_no}:tool:{call_id}"


def run_key(run_id: UUID, phase: str) -> str:
    return f"run:{run_id}:{phase}"


def tool_specs_summary(specs: Sequence[ToolSpec]) -> list[str]:
    return [t.name for t in specs]
