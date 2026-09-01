"""RuntimeInstanceManager ABC (v0.2).

Owns the lifecycle of persisted runtime_instances rows: provision → start →
healthcheck → stop/restart/destroy. One instance per employee (enforced by a
unique constraint on runtime_instances.employee_id).
"""

from abc import ABC, abstractmethod

from sqlalchemy.orm import Session

from app.models.organization import Employee
from app.models.provider import Provider
from app.models.runtime import RuntimeInstance


class RuntimeInstanceManager(ABC):
    @abstractmethod
    async def create_instance(
        self,
        db: Session,
        employee: Employee,
        *,
        runtime_type: str,
        deployment_mode: str,
        provider: Provider | None = None,
        model: str | None = None,
        cpu_limit: float = 2.0,
        memory_limit_mb: int = 4096,
    ) -> RuntimeInstance: ...

    @abstractmethod
    async def start_instance(self, db: Session, instance: RuntimeInstance) -> RuntimeInstance: ...

    @abstractmethod
    async def stop_instance(self, db: Session, instance: RuntimeInstance) -> RuntimeInstance: ...

    @abstractmethod
    async def restart_instance(self, db: Session, instance: RuntimeInstance) -> RuntimeInstance: ...

    @abstractmethod
    async def destroy_instance(self, db: Session, instance: RuntimeInstance) -> None:
        """Remove the container/process; employee data dirs are KEPT."""
        ...

    @abstractmethod
    async def inspect_instance(self, instance: RuntimeInstance) -> dict: ...

    @abstractmethod
    async def get_logs(self, instance: RuntimeInstance, tail: int = 200) -> list[str]: ...

    @abstractmethod
    def get_connection_info(self, instance: RuntimeInstance) -> dict: ...

    @abstractmethod
    async def healthcheck_once(self) -> None:
        """One sweep over all managed instances (crash/unhealthy detection)."""
        ...

    @abstractmethod
    async def start_healthcheck_loop(self) -> None: ...

    @abstractmethod
    async def stop_healthcheck_loop(self) -> None: ...
