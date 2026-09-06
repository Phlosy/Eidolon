"""RuntimeAdapter ABC + data types. See docs/architecture.md §4.1."""

import asyncio
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

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
    details: dict = field(default_factory=dict)


@dataclass
class TaskContext:
    task_id: int
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


@dataclass
class RuntimeSession:
    id: str
    instance: RuntimeInstance
    task_id: int
    status: str = "running"
    # Adapter-private state (queue / background task / produced artifacts).
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    background: asyncio.Task | None = None
    artifacts: list["ProducedArtifact"] = field(default_factory=list)


@dataclass
class RuntimeEvent:
    kind: RuntimeEventKind
    data: dict
    ts: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class ProducedArtifact:
    type: str
    title: str
    content: str


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
