"""Explicit selected-model validation with a durable one-request budget receipt."""

import asyncio
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.models.base import as_utc, utcnow
from app.models.organization import Employee
from app.models.runtime import RuntimeInstance, RuntimeModelProbe
from app.providers import probe
from app.repositories import runtimes as runtime_repo
from app.runtimes.manager import docker_manager
from app.runtimes.manager.ensure import blocking
from app.runtimes.probe import MODEL_TTL_SECONDS, fact
from app.work.runtime_readiness import provider_state


def _output(attempt):
    return {"id": attempt.id, "state": attempt.state, "result": dict(attempt.result_json or {})}


def _expire_running(db, instance_id):
    active = db.scalar(
        select(RuntimeModelProbe).where(
            RuntimeModelProbe.runtime_instance_id == instance_id,
            RuntimeModelProbe.state == "running",
        )
    )
    if (
        active
        and as_utc(active.created_at)
        + timedelta(seconds=active.request_json["timeout_seconds"] + 30)
        < utcnow()
    ):
        result = {
            **active.result_json,
            "fact": fact("unknown", "model_probe_interrupted"),
            "usage": None,
            "cost_usd": None,
        }
        db.execute(
            update(RuntimeModelProbe)
            .where(RuntimeModelProbe.id == active.id, RuntimeModelProbe.state == "running")
            .values(state="unknown", result_json=result)
        )


def _claim(instance_id, limits, manager):
    request = limits.model_dump(mode="json")
    with docker_manager.SessionLocal() as db:
        instance = runtime_repo.get_instance(db, instance_id)
        if instance is None:
            raise HTTPException(404, "runtime instance not found")
        # Receipts outlive runtime deletion; SQLite may reuse the deleted id.
        # Bind the key to this environment generation, not just its row number.
        request["runtime_identity"] = {
            "resource_key": instance.resource_key,
            "created_at": as_utc(instance.created_at).isoformat(),
        }
        employee = db.get(Employee, instance.employee_id)
        if employee is None or employee.lifecycle_status not in {"active", "onboarding"}:
            raise HTTPException(409, "employee_inactive")
        state = provider_state(db, employee, instance)
        if state.reason:
            raise HTTPException(409, state.reason)
        _expire_running(db, instance_id)
        existing = db.scalar(
            select(RuntimeModelProbe).where(
                RuntimeModelProbe.runtime_instance_id == instance_id,
                RuntimeModelProbe.idempotency_key == limits.idempotency_key,
            )
        )
        if existing:
            if existing.fingerprint != state.fingerprint or existing.request_json != request:
                raise HTTPException(409, "model_probe_idempotency_conflict")
            db.commit()
            return _output(existing), None
        try:
            quote = probe.model_probe_quote(state.provider, state.model, limits)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        credential = manager._secrets.retrieve(db, state.provider.credential_ref)
        attempt = RuntimeModelProbe(
            company_id=employee.company_id,
            employee_id=employee.id,
            runtime_instance_id=instance_id,
            idempotency_key=limits.idempotency_key,
            fingerprint=state.fingerprint,
            request_json=request,
            state="running",
            result_json={
                "fact": fact("unknown", "model_probe_running"),
                "pricing": quote,
                "usage": None,
                "cost_usd": None,
            },
        )
        db.add(attempt)
        try:
            db.flush()
            instance.metadata_json = {
                **instance.metadata_json,
                "selected_model_probe": {
                    "attempt_id": attempt.id,
                    "fingerprint": state.fingerprint,
                    "fact": attempt.result_json["fact"],
                },
            }
            db.commit()
        except IntegrityError:
            # Another request already reserved this key or this runtime's only
            # active validation slot. No second remote call is allowed.
            db.rollback()
            raise HTTPException(409, "model_probe_already_running") from None
        db.expunge(state.provider)
        return _output(attempt), (state.provider, credential, state.model, quote)


def _finish(attempt_id, result):
    with docker_manager.SessionLocal() as db:
        attempt = db.get(RuntimeModelProbe, attempt_id)
        if attempt is None or attempt.state != "running":
            return _output(attempt) if attempt else None
        changed = db.execute(
            update(RuntimeModelProbe)
            .where(RuntimeModelProbe.id == attempt_id, RuntimeModelProbe.state == "running")
            .values(state=result["fact"]["state"], result_json={**attempt.result_json, **result})
        ).rowcount
        if changed != 1:
            db.refresh(attempt)
            return _output(attempt)
        instance = db.get(RuntimeInstance, attempt.runtime_instance_id)
        if instance is not None:
            latest = (instance.metadata_json or {}).get("selected_model_probe") or {}
            if latest.get("attempt_id") == attempt.id:
                metadata = {
                    **instance.metadata_json,
                    "selected_model_probe": {
                        "attempt_id": attempt.id,
                        "fingerprint": attempt.fingerprint,
                        "fact": result["fact"],
                    },
                }
                # A simultaneous configuration/control update wins over this
                # snapshot. Its metadata must not be replaced by an old copy.
                db.execute(
                    update(RuntimeInstance)
                    .where(
                        RuntimeInstance.id == instance.id,
                        RuntimeInstance.updated_at == instance.updated_at,
                    )
                    .values(metadata_json=metadata)
                    .execution_options(synchronize_session=False)
                )
        db.commit()
        return _output(attempt)


async def validate_model(manager, instance_id, limits):
    receipt, prepared = await blocking(_claim, instance_id, limits, manager)
    if prepared is None:
        return receipt
    provider, credential, model, quote = prepared
    try:
        result = await probe.validate_selected_model(provider, credential, model, limits, quote)
    except asyncio.CancelledError:
        await blocking(
            _finish,
            receipt["id"],
            {
                "fact": fact("unknown", "model_probe_cancelled", ttl=MODEL_TTL_SECONDS),
                "usage": None,
                "cost_usd": None,
            },
        )
        raise
    return await blocking(_finish, receipt["id"], result)
