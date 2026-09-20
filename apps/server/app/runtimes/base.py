"""RuntimeAdapter ABC + data types. See docs/architecture.md §4.1."""

import asyncio
import hashlib
import math
import re
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from app.models.enums import RuntimeType


class RuntimeStatus(StrEnum):
    stopped = "stopped"
    running = "running"
    error = "error"


class RuntimeEventKind(StrEnum):
    thinking = "thinking"
    message = "message"
    tool_call = "tool_call"
    artifact = "artifact"
    status = "status"
    error = "error"
    completed = "completed"


@dataclass
class EmployeeRef:
    id: int
    slug: str
    name: str
    role: str


@dataclass
class RuntimeInstance:
    employee_id: int
    profile: str
    home_path: str
    status: RuntimeStatus = RuntimeStatus.stopped
    # Adapter-private connection details (container name, base URL, key ref ...).
    details: dict = field(default_factory=dict, repr=False)


@dataclass
class TaskContext:
    # Management/review handles carry no worker task. Their host owns the
    # explicit Project/ReviewRequest binding through the WorkSession.
    task_id: int | None
    project_id: int
    title: str
    kind: str
    description: str = ""
    acceptance_criteria: str = ""
    employee_name: str = ""
    employee_role: str = ""
    # v0.2 learning retrieval: assignee's relevant private knowledge + skills.
    prior_knowledge: list[str] = field(default_factory=list)
    validated_skills: list[str] = field(default_factory=list)
    # M2.6 handoff（H6）：上游 Task 的产物引用 + **有界**内容摘要。
    # 每一项是 `app/work/handoff.py::InputArtifactRef.as_dict()`。
    # 它是**输入**，不是知识：这里不做检索、不做裁剪判断，由 handoff 层决定。
    input_artifacts: list[dict] = field(default_factory=list)


@dataclass
class RuntimeSession:
    id: str
    instance: RuntimeInstance
    task_id: int | None
    status: str = "running"
    # Adapter-private state (queue / background task / produced artifacts).
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    background: asyncio.Task | None = None
    artifacts: list["ProducedArtifact"] = field(default_factory=list)
    turn_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    remote_run_id: str | None = None
    cancel_confirmed: bool | None = None
    completed_turns: dict[str, tuple[str, "RuntimeReply"]] = field(default_factory=dict, repr=False)
    # One finite host loop per local session. Durable attempts/recovery are separate.
    conversation_started: bool = False
    # Installed by the host before sending. Adapters know no database/domain types.
    attempt: "RuntimeAttemptObserver | None" = field(default=None, repr=False)
    remote_session_ref: str | None = None


class RuntimeProtocolError(RuntimeError):
    """A controlled response was unavailable or could not be authenticated to its run."""


class RuntimeAttemptObserver(Protocol):
    request_key: str
    idempotency_key: str

    def accepted(self, run_id: str, remote_session_ref: str) -> None: ...

    def terminal(self, status: str, source: str) -> None: ...

    def observe(self, event: "RuntimeEvent") -> None: ...


def accept_remote_run(session: RuntimeSession, run_id: str, remote_session_ref: str) -> None:
    if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,256}", run_id):
        raise RuntimeProtocolError("run_id_invalid")
    session.remote_run_id = run_id
    session.remote_session_ref = remote_session_ref
    session.cancel_confirmed = None
    if session.attempt is not None:
        # A failed durable acknowledgement aborts the path before remote waiting.
        session.attempt.accepted(run_id, remote_session_ref)


def observe_remote_terminal(session: RuntimeSession, status: str, source: str) -> None:
    if session.attempt is not None:
        session.attempt.terminal(status, source)


@dataclass(frozen=True)
class RuntimeRunReference:
    local_session_ref: str
    remote_run_id: str
    remote_session_ref: str


@dataclass(frozen=True)
class RuntimeRunStatus:
    status: str  # running / completed / failed / unknown; never a domain status
    source: str


@dataclass(frozen=True)
class RuntimeReply:
    session_id: str
    request_id: str
    run_id: str
    text: str
    usage: dict | None = None


def controlled_request_key(
    session: RuntimeSession, request_id: str, timeout: float, *, validate_only: bool = False
) -> str:
    if session.status != "running":
        raise RuntimeProtocolError("session_not_running")
    if session.background and not session.background.done():
        raise RuntimeProtocolError("session_already_streaming")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", request_id):
        raise RuntimeProtocolError("invalid_request_id")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", session.id):
        raise RuntimeProtocolError("invalid_session_id")
    if not math.isfinite(timeout) or not 0 < timeout <= 900:
        raise RuntimeProtocolError("invalid_turn_timeout")
    if session.attempt is not None and not validate_only:
        if session.attempt.request_key != request_id:
            raise RuntimeProtocolError("attempt_request_mismatch")
        return session.attempt.idempotency_key
    # Both runtimes scope idempotency beyond a conversation. Include its identity.
    return hashlib.sha256(f"{session.id}\0{request_id}".encode()).hexdigest()


def complete_reply_text(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.encode()) > 65_536:
        raise RuntimeProtocolError("invalid_complete_reply")
    return value


def cached_controlled_reply(
    session: RuntimeSession, request_id: str, prompt: str
) -> RuntimeReply | None:
    """Called under turn_lock: replay locally, never evict a used request identity."""
    cached = session.completed_turns.get(request_id)
    if cached is not None:
        digest, reply = cached
        if hashlib.sha256(prompt.encode()).hexdigest() != digest:
            raise RuntimeProtocolError("request_reuse_conflict")
        return reply
    if len(session.completed_turns) >= 32:
        raise RuntimeProtocolError("controlled_turn_limit")
    return None


def remember_controlled_reply(
    session: RuntimeSession, prompt: str, reply: RuntimeReply
) -> RuntimeReply:
    # Check after all transport cleanup awaits, immediately before releasing the
    # turn lock. A cancellation intent invalidates even an otherwise valid reply.
    if session.status != "running":
        raise RuntimeProtocolError("session_not_running")
    session.completed_turns[reply.request_id] = (hashlib.sha256(prompt.encode()).hexdigest(), reply)
    return reply


@dataclass
class RuntimeEvent:
    kind: RuntimeEventKind
    data: dict
    ts: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class ArtifactOrigin:
    local_session_ref: str
    remote_run_id: str
    remote_session_ref: str
    source_ref: str
    size_bytes: int
    sha256: str


@dataclass
class ProducedArtifact:
    type: str
    title: str
    content: str
    # Transport evidence only; the host must validate its saved attempt before acceptance.
    origin: ArtifactOrigin | None = None


@dataclass
class RuntimeInfo:
    type: RuntimeType
    version: str = "unknown"
    details: dict = field(default_factory=dict)


@dataclass
class RuntimeCapabilities:
    """Honest per-runtime feature flags surfaced via GET /runtime-types."""

    chat: bool = False
    task: bool = False
    filesystem: bool = False
    terminal: bool = False
    web: bool = False
    memory: bool = False
    skills: bool = False
    scheduler: bool = False
    streaming: bool = False
    artifacts: bool = False
    # 诚实标记：该 runtime 是否真的把行为投影送进 agent 上下文（T1 内联）。
    # 没有这条能力位，“投影是否生效”只能靠读代码猜 —— 见 docs/employee-brain-behavior-policy.md §8。
    brain_projection: bool = False


class RuntimeAdapter(ABC):
    type: RuntimeType
    implemented: bool = False
    supports_controlled_turn: bool = False

    @abstractmethod
    async def create_instance(self, employee: EmployeeRef, config: dict) -> RuntimeInstance: ...

    @abstractmethod
    async def start(self, instance: RuntimeInstance) -> None: ...

    @abstractmethod
    async def stop(self, instance: RuntimeInstance) -> None: ...

    @abstractmethod
    async def get_status(self, instance: RuntimeInstance) -> RuntimeStatus: ...

    @abstractmethod
    async def create_session(
        self, instance: RuntimeInstance, task: TaskContext
    ) -> RuntimeSession: ...

    @abstractmethod
    async def send_task(self, session: RuntimeSession, prompt: str, context: dict) -> None: ...

    @abstractmethod
    async def send_message(self, session: RuntimeSession, message: str) -> None: ...

    async def complete_turn(
        self, session: RuntimeSession, prompt: str, *, request_id: str, timeout: float = 60
    ) -> RuntimeReply:
        """Return one complete reply on the host-controlled channel.

        Ordinary task streams and tool telemetry cannot implement this capability.
        A completed turn leaves the session running for the next host receipt.
        """
        raise RuntimeProtocolError("controlled_turn_unsupported")

    async def query_run(
        self, instance: RuntimeInstance, reference: RuntimeRunReference
    ) -> RuntimeRunStatus:
        """Read-only reconciliation; never starts or resumes a remote execution."""
        raise RuntimeProtocolError("run_query_unsupported")

    @abstractmethod
    def stream_events(self, session: RuntimeSession) -> AsyncIterator[RuntimeEvent]: ...

    @abstractmethod
    async def cancel_task(self, session: RuntimeSession) -> None: ...

    @abstractmethod
    async def get_artifacts(self, session: RuntimeSession) -> list[ProducedArtifact]: ...

    @abstractmethod
    async def get_runtime_info(self, instance: RuntimeInstance) -> RuntimeInfo: ...

    def get_capabilities(self) -> RuntimeCapabilities:
        """Honest capability flags. Default: nothing (unimplemented adapters)."""
        return RuntimeCapabilities()

    def supported_providers(self) -> list[str]:
        """Provider types this runtime can be configured with."""
        return []

    # Tested runtime version range for update compatibility checks (None = unknown).
    tested_min_version: str | None = None
    tested_max_version: str | None = None

    def detect(self) -> bool:
        """Whether this runtime is usable on the local machine. Default False."""
        return False
