"""Shared helpers for container-backed runtime adapters (hermes / openclaw).

Adapters resolve their control-plane connection (base URL + per-employee
credential) from the persisted runtime_instances row; the container itself is
provisioned by the RuntimeInstanceManager (POST /employees/{id}/runtime).
"""

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.enums import RuntimeInstanceStatus
from app.providers.secrets.store import get_secret_store
from app.repositories import runtimes as runtime_repo
from app.runtimes.base import RuntimeInstance, RuntimeStatus
from app.runtimes.manager.docker_manager import RuntimeManagerError


def resolve_container_instance(employee_id: int, runtime_type: str) -> RuntimeInstance:
    """Load the employee's provisioned runtime row into a RuntimeInstance handle."""
    with SessionLocal() as db:
        row = runtime_repo.get_instance_for_employee(db, employee_id)
        if row is None or row.runtime_type != runtime_type:
            raise RuntimeManagerError(
                f"no {runtime_type} runtime instance for employee {employee_id}; "
                "provision one via POST /employees/{id}/runtime"
            )
        token = None
        token_ref = (row.metadata_json or {}).get("api_key_ref")
        if token_ref:
            token = get_secret_store().retrieve(db, token_ref)
        base_url = None
        if row.internal_host and row.internal_port:
            base_url = f"http://{row.internal_host}:{row.internal_port}"
        status = (
            RuntimeStatus.running
            if row.status in (RuntimeInstanceStatus.running.value, RuntimeInstanceStatus.idle.value)
            else RuntimeStatus.stopped
        )
        return RuntimeInstance(
            employee_id=employee_id,
            profile=row.container_name or f"{runtime_type}-{employee_id}",
            home_path=row.workspace_path,
            status=status,
            details={
                "row_id": row.id,
                "container_name": row.container_name,
                "base_url": base_url,
                "api_key": token,
                "runtime_version": row.runtime_version,
            },
        )


def refresh_status(db: Session, instance: RuntimeInstance) -> RuntimeStatus:
    row = runtime_repo.get_instance_for_employee(db, instance.employee_id)
    if row is None:
        return RuntimeStatus.error
    if row.status in (RuntimeInstanceStatus.running.value, RuntimeInstanceStatus.idle.value):
        return RuntimeStatus.running
    if row.status in (
        RuntimeInstanceStatus.crashed.value,
        RuntimeInstanceStatus.error.value,
        RuntimeInstanceStatus.unhealthy.value,
    ):
        return RuntimeStatus.error
    return RuntimeStatus.stopped
