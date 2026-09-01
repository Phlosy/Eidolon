# Eidolon Server

Backend of Eidolon — Autonomous AI Organization Runtime. FastAPI + SQLAlchemy 2.0 + SQLite.
Binding spec: [`docs/architecture.md`](../../docs/architecture.md).

## Setup

```bash
cd apps/server
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'        # or: .venv/bin/pip install -r requirements.lock
```

## Run tests

```bash
.venv/bin/python -m pytest tests -q
.venv/bin/ruff check app tests
.venv/bin/ruff format --check app tests
```

## Run the server

```bash
# binds settings.api_host / settings.api_port (default 127.0.0.1:26881)
.venv/bin/python -m app.main
# or explicitly:
EIDOLON_MOCK_TASK_SECONDS=2 .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 26881
```

- REST API: `http://127.0.0.1:26881/api/v1` (OpenAPI docs at `/docs`)
- Health: `GET /health`
- Realtime events: `ws://127.0.0.1:26881/ws/events`
- The frontend normally reaches the API via the Web tier proxy (`/api`, `/ws`); direct access is for debugging.

On startup the app creates tables (dev convenience) and seeds the default company
"Eidolon Studio" with 5 departments and 5 mock-runtime employees (Alice/Morgan/Bob/Charlie/Dana).
Create a project with `POST /api/v1/projects {"name": "...", "description": "..."}` and the
orchestrator runs the full order → planning → research → development → testing → release loop.

## Configuration

All settings use the `EIDOLON_` prefix (see `app/core/config.py`, `.env.example` at repo root).
Key ones: `EIDOLON_DATABASE_URL`, `EIDOLON_WORKSPACE_ROOT`, `EIDOLON_RUNTIME_MODE` (`mock`),
`EIDOLON_MOCK_TASK_SECONDS`, `EIDOLON_CORS_ORIGINS`.

## Migrations

Alembic lives in `migrations/` (initial revision included):

```bash
.venv/bin/alembic upgrade head
.venv/bin/alembic revision --autogenerate -m "..."
```
