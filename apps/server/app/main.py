"""FastAPI assembly: routers, CORS, WS, startup. See docs/architecture.md §8/§11."""

import json
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.database import SessionLocal, init_db
from app.core.logging import configure_logging, get_logger
from app.events.bus import bus
from app.providers.secrets.store import get_secret_store
from app.runtimes.gateway import gateway
from app.runtimes.manager import get_manager
from app.runtimes.updates import get_update_service
from app.services import lifecycle as lifecycle_service
from app.services.drive_migration import migrate_artifacts_to_drive
from app.services.seed import seed_default_company
from app.workflow.orchestrator import orchestrator

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(settings.log_level)
    init_db()  # dev convenience; Alembic has the authoritative initial migration
    with SessionLocal() as db:
        seed_default_company(db)
        migrate_artifacts_to_drive(db)  # v0.3: legacy artifacts -> drive_nodes
        lifecycle_service.seed_lifecycle(db)  # v0.4: lifecycle seed + legacy backfill (§12)
        get_secret_store().register_existing(db)  # arm log/event redaction
    bus.attach_loop()
    orchestrator.start()
    manager = get_manager()
    await manager.start_healthcheck_loop()
    update_service = get_update_service()
    await update_service.start_update_loop()
    logger.info("eidolon server started (runtime_mode=%s)", settings.runtime_mode)
    yield
    await orchestrator.stop()
    await manager.stop_healthcheck_loop()
    await update_service.stop_update_loop()
    await gateway.stop_all()


app = FastAPI(title="Eidolon Server", version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api/v1")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.websocket("/ws/events")
async def ws_events(websocket: WebSocket) -> None:
    await websocket.accept()
    queue = bus.subscribe()
    try:
        while True:
            message = await queue.get()
            await websocket.send_text(json.dumps(message, default=str))
    except WebSocketDisconnect:
        pass
    finally:
        bus.unsubscribe(queue)


def main() -> None:
    uvicorn.run(
        "app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
