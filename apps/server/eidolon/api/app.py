"""``eidolon-api``: FastAPI over the read models and the domain services. Thin by
design (v2-architecture §2.1): authenticate, shape DTOs, call a service. It runs
no agent loop and makes no management judgement.
"""

import secrets

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.orm import sessionmaker

from eidolon.api.auth import AuthService
from eidolon.api.routers import auth, companies, ledger, provenance, runs, work
from eidolon.config import Settings, settings

PREFIX = "/api/v2"
CSRF_COOKIE = "eidolon_csrf"
CSRF_HEADER = "x-csrf-token"
CSRF_EXEMPT = {f"{PREFIX}/auth/register", f"{PREFIX}/auth/login"}
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


def create_app(session_factory: sessionmaker | None = None, cfg: Settings = settings) -> FastAPI:
    if session_factory is None:
        from eidolon.db import SessionLocal

        session_factory = SessionLocal
    app = FastAPI(title="Eidolon API", version="2")
    app.state.session_factory = session_factory
    app.state.settings = cfg
    app.state.cookie_name = cfg.session_cookie_name
    app.state.auth = AuthService(session_ttl_hours=cfg.session_ttl_hours)

    @app.middleware("http")
    async def csrf_protection(request: Request, call_next):
        """Double-submit: a mutating request with a session cookie must carry the
        CSRF cookie's value in a header. Register and login have no session yet."""
        if (
            request.method in MUTATING
            and request.cookies.get(cfg.session_cookie_name)
            and request.url.path not in CSRF_EXEMPT
        ):
            cookie = request.cookies.get(CSRF_COOKIE, "")
            header = request.headers.get(CSRF_HEADER, "")
            if not cookie or not header or not secrets.compare_digest(cookie, header):
                return JSONResponse(status_code=403, content={"detail": "CSRF validation failed"})
        return await call_next(request)

    if cfg.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[o.strip() for o in cfg.cors_origins.split(",") if o.strip()],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    for router in (
        auth.router,
        companies.router,
        work.router,
        runs.router,
        ledger.router,
        provenance.router,
    ):
        app.include_router(router, prefix=PREFIX)

    @app.get(f"{PREFIX}/health")
    def health():
        return {"status": "ok", "api": "v2"}

    return app


app = create_app()

__all__ = ["CSRF_COOKIE", "CSRF_HEADER", "PREFIX", "app", "create_app"]
