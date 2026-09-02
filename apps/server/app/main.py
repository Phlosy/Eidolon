"""FastAPI assembly: routers, CORS, WS, startup. See docs/architecture.md §8/§11."""

import json
import secrets
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.database import SessionLocal, init_db
from app.core.logging import configure_logging, get_logger
from app.events.bus import bus
from app.providers.secrets.store import get_secret_store
from app.runtimes.gateway import gateway
from app.runtimes.manager import get_manager
from app.runtimes.updates import get_update_service
from app.services import auth as auth_service
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


app = FastAPI(title="Eidolon Server", version="0.7.0", lifespan=lifespan)


@app.middleware("http")
async def csrf_protection(request, call_next):
    """Double-submit protection for authenticated cookie mutations.

    Login, registration and discoverable passkey login do not yet have a
    session, so they are intentionally outside this check.
    """
    exempt = {
        "/api/v1/auth/register",
        "/api/v1/auth/verify-email",
        "/api/v1/auth/login",
        "/api/v1/auth/passkeys/authentication/options",
        "/api/v1/auth/passkeys/authentication/verify",
    }
    has_session = bool(request.cookies.get(settings.session_cookie_name))
    if (
        settings.auth_required
        and has_session
        and request.method in {"POST", "PUT", "PATCH", "DELETE"}
        and request.url.path not in exempt
    ):
        cookie = request.cookies.get("eidolon_csrf", "")
        header = request.headers.get("x-csrf-token", "")
        if not cookie or not header or not secrets.compare_digest(cookie, header):
            return JSONResponse(status_code=403, content={"detail": "CSRF validation failed"})
    return await call_next(request)


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
    company_id: int | None = None
    if settings.auth_required:
        with SessionLocal() as db:
            resolved = auth_service.session_from_token(
                db, websocket.cookies.get(settings.session_cookie_name)
            )
            if resolved is None:
                await websocket.close(code=4401)
                return
            _, user = resolved
            _, company = auth_service.primary_company(db, user.id)
            company_id = company.id
    await websocket.accept()
    queue = bus.subscribe()
    try:
        while True:
            message = await queue.get()
            if company_id is not None and message.get("company_id") not in {None, company_id}:
                continue
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
