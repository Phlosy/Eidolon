"""Asynchronous environment observation: short DB phases; never remove an orphan.

Only the persisted host controller may be paired as a repair. Missing/stopped
containers are reported; normal provisioning/retry actions own their recovery.
"""

import asyncio
from datetime import UTC, datetime

from sqlalchemy import select, update

from app.events.bus import bus
from app.models.event import Event
from app.models.organization import Employee
from app.models.runtime import RuntimeInstance
from app.providers import probe as provider_probe
from app.runtimes.manager import docker_manager
from app.runtimes.manager.ensure import blocking
from app.runtimes.openclaw.pairing import pair_controller
from app.runtimes.openclaw.rpc import GatewayError
from app.runtimes.probe import (
    RUNTIME_FACT_NAMES,
    current_fact,
    fact,
    instance_fingerprint,
    probe_control,
)
from app.work.runtime_readiness import provider_state


def _snapshot(instance_id, manager):
    with docker_manager.SessionLocal() as db:
        instance = db.get(RuntimeInstance, instance_id)
        if instance is None or instance.deployment_mode != "docker" or instance.provisioning_token:
            return None
        employee = db.get(Employee, instance.employee_id)
        if employee is None or employee.lifecycle_status not in {"active", "onboarding"}:
            return None
        metadata = instance.metadata_json or {}
        token = manager._secrets.retrieve(db, metadata.get("api_key_ref"))
        device = manager._secrets.retrieve(db, metadata.get("device_key_ref"))
        provider = provider_state(db, employee, instance)
        credential = (
            manager._secrets.retrieve(db, provider.provider.credential_ref)
            if provider.provider is not None and provider.reason is None
            else None
        )
        if provider.provider is not None:
            db.expunge(provider.provider)
        db.expunge(instance)
        return instance, employee.company_id, token, device, provider, credential


def _owned(instance, attrs):
    labels = (attrs.get("Config") or {}).get("Labels") or {}
    return (
        labels.get("eidolon.managed") == "true"
        and labels.get("eidolon.runtime_type") == instance.runtime_type
        and labels.get("eidolon.employee_id") == str(instance.employee_id)
        and labels.get("eidolon.resource_key") in {None, instance.resource_key}
    )


def _save(instance, company_id, facts, *, status, health, provider_receipt=None):
    report = {"instance_fingerprint": instance_fingerprint(instance), "facts": facts}
    metadata = {**(instance.metadata_json or {}), "runtime_probe": report}
    if provider_receipt is not None:
        metadata["provider_connectivity_probe"] = provider_receipt
    with docker_manager.SessionLocal() as db:
        changed = (
            db.execute(
                update(RuntimeInstance)
                .where(
                    RuntimeInstance.id == instance.id,
                    RuntimeInstance.updated_at == instance.updated_at,
                    RuntimeInstance.provisioning_token.is_(None),
                )
                .values(
                    metadata_json=metadata,
                    status=status,
                    health_status=health,
                    last_healthcheck_at=datetime.now(UTC),
                )
            ).rowcount
            == 1
        )
        db.commit()
    if changed and status != instance.status and status in {"crashed", "unhealthy"}:
        bus.publish(
            f"runtime.{status}",
            {"instance_id": instance.id, "reason": facts["process"]["reason"]},
            company_id=company_id,
            actor_employee_id=instance.employee_id,
        )
    return changed


async def probe_instance(
    manager, instance_id: int, *, repair_control: bool = False, reuse_provider: bool = False
) -> bool:
    snapshot = await blocking(_snapshot, instance_id, manager)
    if snapshot is None:
        return False
    instance, company_id, token, device, provider, credential = snapshot
    facts = {name: fact("unknown", "not_probed") for name in RUNTIME_FACT_NAMES}
    if not instance.container_id:
        facts["container"] = fact("failed", "container_not_recorded")
        facts["process"] = fact("failed", "container_not_recorded")
        return await blocking(_save, instance, company_id, facts, status="error", health="unknown")
    # Inspect is meaningful only when the daemon is reachable. An unavailable
    # daemon is unknown, not proof that an employee's container disappeared.
    if not await blocking(manager._docker.ping):
        facts["container"] = fact("unknown", "docker_unavailable")
        return await blocking(
            _save, instance, company_id, facts, status=instance.status, health="unknown"
        )
    attrs = await blocking(manager._docker.inspect_container, instance.container_id)
    if attrs is None:
        inventory = await blocking(manager._docker.list_managed_containers)
        missing = inventory is not None and instance.container_id not in {
            a["Id"] for a in inventory
        }
        facts["container"] = fact(
            "failed" if missing else "unknown",
            "container_missing" if missing else "docker_unavailable",
        )
        facts["process"] = facts["container"].copy()
        return await blocking(
            _save,
            instance,
            company_id,
            facts,
            status="crashed" if missing else instance.status,
            health="unknown",
        )
    if not _owned(instance, attrs):
        facts["container"] = fact("failed", "container_ownership_mismatch")
        return await blocking(_save, instance, company_id, facts, status="error", health="unknown")
    facts["container"] = fact("passed", "container_present")
    state = attrs.get("State") or {}
    docker_state = state.get("Status", "unknown")
    docker_health = (state.get("Health") or {}).get("Status", "none")
    if docker_state != "running":
        facts["process"] = fact(
            "failed", "process_not_running", docker_state=docker_state, docker_health=docker_health
        )
        status = (
            "crashed"
            if instance.status in {"starting", "running", "idle", "unhealthy"}
            else instance.status
        )
        return await blocking(_save, instance, company_id, facts, status=status, health="unhealthy")
    base_url = (
        f"http://{instance.internal_host}:{instance.internal_port}"
        if instance.internal_host and instance.internal_port
        else None
    )
    control = await probe_control(instance.runtime_type, base_url, token, device_key=device)
    if (
        repair_control
        and instance.runtime_type == "openclaw"
        and device
        and control["control"]["reason"] == "pairing_required"
    ):
        try:
            await pair_controller(manager._docker, instance, token, device)
            control = await probe_control(instance.runtime_type, base_url, token, device_key=device)
        except GatewayError as exc:
            control["control"] = fact("failed", exc.code)
    facts.update(control)
    provider_receipt = None
    previous = (instance.metadata_json or {}).get("provider_connectivity_probe") or {}
    provider_current = (
        previous.get("fingerprint") == provider.fingerprint
        and current_fact(previous.get("fact"))["state"] != "unknown"
    )
    if provider.reason is None and not (reuse_provider and provider_current):
        result = await provider_probe.test_provider(provider.provider, credential)
        provider_receipt = {
            "fingerprint": provider.fingerprint,
            "fact": fact(
                "passed" if result["ok"] else "failed",
                "provider_reachable" if result["ok"] else "provider_connection_failed",
            ),
        }
    # Images without a Docker HEALTHCHECK can provide observed application
    # health. Unknown health and mere State=running never imply readiness.
    healthy = docker_health == "healthy" or (
        docker_health == "none" and facts["application_health"]["state"] == "passed"
    )
    health = "unhealthy" if docker_health == "unhealthy" else "healthy" if healthy else "unknown"
    facts["process"] = fact(
        "passed" if healthy else "failed" if health == "unhealthy" else "unknown",
        "process_healthy"
        if healthy
        else "process_unhealthy"
        if health == "unhealthy"
        else "process_health_unknown",
        docker_state=docker_state,
        docker_health=docker_health,
    )
    return await blocking(
        _save,
        instance,
        company_id,
        facts,
        status="unhealthy" if health == "unhealthy" else "running",
        health=health,
        provider_receipt=provider_receipt,
    )


def _rows():
    with docker_manager.SessionLocal() as db:
        rows = list(
            db.scalars(select(RuntimeInstance).where(RuntimeInstance.deployment_mode == "docker"))
        )
        for row in rows:
            db.expunge(row)
        return rows


def _report_orphans(containers):
    # Read fresh rows after Docker inventory: a creator may have committed its
    # resource key before recording container_id. Such a resource is not orphaned.
    with docker_manager.SessionLocal() as db:
        rows = list(db.scalars(select(RuntimeInstance)))
        ids = {row.container_id for row in rows if row.container_id}
        keys = {row.resource_key for row in rows if row.resource_key}
        names = {row.container_name for row in rows if row.container_name}
        inventory_ids = [attrs["Id"] for attrs in containers if attrs.get("Id")]
        seen = {
            event.payload.get("container_id")
            for event in db.scalars(
                select(Event).where(
                    Event.type == "runtime.orphan_detected",
                    Event.payload["container_id"].as_string().in_(inventory_ids),
                )
            )
        }
        employee_ids = {
            int(value)
            for attrs in containers
            if (
                value := ((attrs.get("Config") or {}).get("Labels") or {}).get(
                    "eidolon.employee_id", ""
                )
            ).isdigit()
        }
        companies = dict(
            db.execute(
                select(Employee.id, Employee.company_id).where(Employee.id.in_(employee_ids))
            ).all()
        )
        pending = []
        for attrs in containers:
            labels = (attrs.get("Config") or {}).get("Labels") or {}
            if labels.get("eidolon.managed") != "true" or labels.get(
                "eidolon.runtime_type"
            ) not in {"hermes", "openclaw"}:
                continue
            cid = attrs.get("Id")
            if (
                not cid
                or cid in ids
                or cid in seen
                or labels.get("eidolon.resource_key") in keys
                or str(attrs.get("Name", "")).lstrip("/") in names
            ):
                continue
            employee_id = labels.get("eidolon.employee_id", "")
            company_id = companies.get(int(employee_id)) if employee_id.isdigit() else None
            pending.append((cid, company_id))
    for container_id, company_id in pending:
        bus.publish(
            "runtime.orphan_detected",
            {"container_id": container_id, "action": "report_only"},
            company_id=company_id,
        )


async def reconcile_once(manager) -> None:
    rows = await blocking(_rows)
    limiter = asyncio.Semaphore(4)

    async def observe(instance_id):
        async with limiter:
            await probe_instance(manager, instance_id, repair_control=True, reuse_provider=True)

    results = await asyncio.gather(*(observe(row.id) for row in rows), return_exceptions=True)
    for result in results:
        if isinstance(result, BaseException):
            # Exception messages can contain runtime output. Report only the type.
            docker_manager.logger.warning(
                "runtime reconciliation failed: %s", type(result).__name__
            )
    inventory = await blocking(manager._docker.list_managed_containers)
    if inventory is not None:
        await blocking(_report_orphans, inventory)
