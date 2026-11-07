"""``eidolon-api``: FastAPI over the read models and the domain services. Thin by
design (v2-architecture §2.1): authenticate, shape DTOs, call a service. It runs
no agent loop and makes no management judgement.
"""

import secrets
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import sessionmaker

from eidolon import observability
from eidolon.api.auth import AuthService
from eidolon.api.routers import auth, companies, decisions, ledger, manage, provenance, runs, work
from eidolon.config import Settings, settings

PREFIX = "/api/v2"
CSRF_COOKIE = "eidolon_csrf"
CSRF_HEADER = "x-csrf-token"
CSRF_EXEMPT = {f"{PREFIX}/auth/register", f"{PREFIX}/auth/login"}
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


def route_template(app: FastAPI, request: Request) -> str | None:
    """The matched route's path template (bounded label cardinality), found the same
    way the router finds it; dependency failures still get their route."""

    def walk(routes):
        for route in routes:
            candidates = getattr(route, "effective_candidates", None)
            if callable(candidates):  # a lazily included router: look inside
                found = walk(candidates())
                if found:
                    return found
                continue
            regex = getattr(route, "path_regex", None)
            methods = getattr(route, "methods", None)
            if regex is None or not regex.match(request.url.path):
                continue
            if methods and request.method not in methods:
                continue
            return getattr(route, "path", None)
        return None

    return walk(app.routes)


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
        manage.router,
        decisions.router,
    ):
        app.include_router(router, prefix=PREFIX)

    @app.middleware("http")
    async def metrics(request: Request, call_next):
        t0 = time.perf_counter()
        response = await call_next(request)
        path = route_template(app, request) or "unmatched"
        observability.HTTP_REQUESTS.labels(request.method, path, str(response.status_code)).inc()
        observability.HTTP_LATENCY.labels(request.method, path).observe(time.perf_counter() - t0)
        return response

    @app.get(f"{PREFIX}/health")
    def health():
        """Liveness plus the two things an operator asks first: is the database there,
        and how far behind are the consumers."""
        from eidolon.registry import default_registry

        try:
            with session_factory() as db:
                report = observability.lag_report(db, list(default_registry().consumers))
        except Exception as exc:  # noqa: BLE001 - any failure here is the answer
            return JSONResponse(
                status_code=503, content={"status": "degraded", "api": "v2", "db": str(exc)}
            )
        lags = [v for v in report["cursors"].values() if v is not None]
        return {
            "status": "ok",
            "api": "v2",
            "db": "ok",
            "head_seq": report["head_seq"],
            "cursors": report["cursors"],
            "max_lag": max(lags) if lags else 0,
        }

    @app.get("/metrics")
    def metrics_endpoint():
        body, content_type = observability.render()
        return Response(content=body, media_type=content_type)

    return app


app = create_app()

__all__ = ["CSRF_COOKIE", "CSRF_HEADER", "PREFIX", "app", "create_app"]
