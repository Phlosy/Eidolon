"""RuntimeGateway: adapter registry + per-employee instance management.

See docs/architecture.md §4.2. Enforces one RuntimeInstance per Employee;
instance profile/home derive from EIDOLON_WORKSPACE_ROOT/{employee_slug}/.
With EIDOLON_RUNTIME_MODE=mock all real adapters are bypassed via MockAdapter.
"""

from app.core.config import settings
from app.core.logging import get_logger
from app.events.bus import bus
from app.models.enums import RuntimeType
from app.models.organization import Employee
from app.runtimes.base import EmployeeRef, RuntimeAdapter, RuntimeInstance
from app.runtimes.hermes.adapter import HermesAdapter
from app.runtimes.mock.adapter import MockAdapter
from app.runtimes.openclaw.adapter import OpenClawAdapter

logger = get_logger(__name__)


class RuntimeGateway:
    def __init__(self) -> None:
        mock = MockAdapter()
        self._adapters: dict[RuntimeType, RuntimeAdapter] = {
            RuntimeType.mock: mock,
            RuntimeType.hermes: HermesAdapter(),
            RuntimeType.openclaw: OpenClawAdapter(),
        }
        self._mock = mock
        self._instances: dict[int, RuntimeInstance] = {}

    def adapter_for(self, runtime_type: str) -> RuntimeAdapter:
        if settings.runtime_mode == "mock":
            return self._mock
        try:
            rt = RuntimeType(runtime_type)
        except ValueError:
            raise NotImplementedError(f"unknown runtime type: {runtime_type}") from None
        adapter = self._adapters.get(rt)
        if adapter is None or not adapter.implemented:
            raise NotImplementedError("adapter not implemented yet")
        return adapter

    async def get_or_create_instance(self, employee: Employee) -> RuntimeInstance:
        instance = self._instances.get(employee.id)
        if instance is not None:
            return instance
        adapter = self.adapter_for(employee.runtime_type)
        ref = EmployeeRef(
            id=employee.id, slug=employee.slug, name=employee.name, role=employee.role
        )
        instance = await adapter.create_instance(ref, employee.runtime_config or {})
        await adapter.start(instance)
        self._instances[employee.id] = instance
        bus.publish(
            "runtime.started",
            {
                "employee_id": employee.id,
                "runtime_type": adapter.type.value,
                "profile": instance.profile,
            },
            company_id=employee.company_id,
            actor_employee_id=employee.id,
        )
        logger.info("runtime instance started", extra={"employee_id": employee.id})
        return instance

    def drop_instance(self, employee_id: int) -> None:
        """Forget an employee's instance (e.g. after runtime switch); it is recreated on demand."""
        self._instances.pop(employee_id, None)

    async def stop_all(self) -> None:
        for employee_id, instance in list(self._instances.items()):
            try:
                await self.adapter_for(RuntimeType.mock).stop(instance)
            except Exception:
                logger.exception("failed to stop instance", extra={"employee_id": employee_id})
        self._instances.clear()

    def adapters(self) -> dict[RuntimeType, RuntimeAdapter]:
        return dict(self._adapters)

    def list_adapters(self) -> list[dict]:
        mock_only = settings.runtime_mode == "mock"
        result = []
        for rt in RuntimeType:
            adapter = self._adapters.get(rt)
            implemented = bool(adapter and adapter.implemented)
            available = implemented and adapter.detect()
            if mock_only and rt != RuntimeType.mock:
                # runtime_mode=mock: real adapters are reported unavailable
                available = False
            result.append({"type": rt.value, "available": available, "implemented": implemented})
        return result

    def list_instances(self) -> list[dict]:
        return [
            {
                "employee_id": inst.employee_id,
                "profile": inst.profile,
                "home_path": inst.home_path,
                "status": inst.status.value,
            }
            for inst in self._instances.values()
        ]


gateway = RuntimeGateway()
