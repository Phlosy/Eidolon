"""/runtime-types + /runtimes + /runtime-images (v0.2).

The frontend discovers runtime types/capabilities from /runtime-types and must
not hardcode them. Instance log tails are redacted server-side.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.runtime import (
    RuntimeImageOut,
    RuntimeInstanceOut,
    RuntimeLogsOut,
    RuntimeTypeInfoOut,
)
from app.services import runtimes as runtime_service

router = APIRouter(tags=["runtimes"])


@router.get("/runtime-types", response_model=list[RuntimeTypeInfoOut])
async def list_runtime_types() -> list[RuntimeTypeInfoOut]:
    return await runtime_service.runtime_types()


@router.get("/runtimes", response_model=list[RuntimeInstanceOut])
def list_runtimes(db: Session = Depends(get_db)) -> list[RuntimeInstanceOut]:
    return runtime_service.list_instances(db)


@router.get("/runtimes/{instance_id}", response_model=RuntimeInstanceOut)
def get_runtime(instance_id: int, db: Session = Depends(get_db)) -> RuntimeInstanceOut:
    return runtime_service.get_instance(db, instance_id)


@router.post("/runtimes/{instance_id}/start", response_model=RuntimeInstanceOut)
async def start_runtime(instance_id: int, db: Session = Depends(get_db)) -> RuntimeInstanceOut:
    return await runtime_service.instance_action(db, instance_id, "start")


@router.post("/runtimes/{instance_id}/stop", response_model=RuntimeInstanceOut)
async def stop_runtime(instance_id: int, db: Session = Depends(get_db)) -> RuntimeInstanceOut:
    return await runtime_service.instance_action(db, instance_id, "stop")


@router.post("/runtimes/{instance_id}/restart", response_model=RuntimeInstanceOut)
async def restart_runtime(instance_id: int, db: Session = Depends(get_db)) -> RuntimeInstanceOut:
    return await runtime_service.instance_action(db, instance_id, "restart")


@router.get("/runtimes/{instance_id}/logs", response_model=RuntimeLogsOut)
async def runtime_logs(
    instance_id: int,
    tail: int = Query(default=200, ge=1, le=5000),
    db: Session = Depends(get_db),
) -> RuntimeLogsOut:
    return RuntimeLogsOut(lines=await runtime_service.instance_logs(db, instance_id, tail))


@router.get("/runtime-images", response_model=list[RuntimeImageOut])
def list_runtime_images(db: Session = Depends(get_db)) -> list[RuntimeImageOut]:
    return runtime_service.list_images(db)


@router.post("/runtime-images/check-updates", response_model=list[RuntimeImageOut])
async def check_runtime_image_updates(db: Session = Depends(get_db)) -> list[RuntimeImageOut]:
    return await runtime_service.check_updates(db)


@router.post("/runtime-images/{runtime_type}/update", response_model=RuntimeImageOut)
async def update_runtime_image(runtime_type: str, db: Session = Depends(get_db)) -> RuntimeImageOut:
    return await runtime_service.trigger_update(db, runtime_type)
