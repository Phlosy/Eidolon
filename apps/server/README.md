# Eidolon Server

Backend of Eidolon — Autonomous AI Organization Runtime. FastAPI + SQLAlchemy 2.0 + SQLite.
Binding spec: [`docs/architecture.md`](../../docs/architecture.md).

## Setup

后端环境统一由**本地 conda 环境**提供（仓库内不建 `.venv`）。在仓库根目录：

```bash
make install-server        # conda create -n eidolon python=3.12 + pip install -r requirements.lock
```

等价的手工步骤（等价于 `make install-server` 展开）：

```bash
conda create -y -n eidolon --override-channels -c conda-forge python=3.12
conda activate eidolon
cd apps/server
pip install -r requirements.lock   # 锁版本，与 CI / make install 完全一致
pip install -e . --no-deps         # 再以开发模式装进本项目
```

激活后 `python` / `pytest` / `ruff` / `alembic` / `uvicorn` 都在 PATH 里，
下面各节的命令都按“已 `conda activate eidolon`”写。

改过 `pyproject.toml` 的依赖后，先 `make install-server` 装新版，再跑 `make lock-server`
重写 `requirements.lock`，两份一起提交 —— 锁文件一旦没跟上，CI 会直接 `ModuleNotFoundError`。

## Run tests

```bash
# 先 conda activate eidolon（或在仓库根用 make test-server）
python -m pytest tests -q
ruff check app tests
ruff format --check app tests
```

## Run the server

```bash
# binds settings.api_host / settings.api_port (default 127.0.0.1:26881)
python -m app.main
# or explicitly:
EIDOLON_MOCK_TASK_SECONDS=2 uvicorn app.main:app --host 127.0.0.1 --port 26881
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
alembic upgrade head                      # 或仓库根目录 make migrate
alembic revision --autogenerate -m "..."  # 或 make migrate-new M="..."
```

改过 `app/models/**` 就要开一个 revision，并且**先 `make stop`、`alembic upgrade head`、
再 `make run`**：启动时 schema 的全权所有者是 Alembic（`app/core/database.py:
ensure_database_schema`：空库直接 `upgrade head`，已有库不在仓库 head 或没有
`alembic_version` 就抛 `DatabaseSchemaError` 拒绝启动），所以忘了 migration 不可能“本地不报错”。
CI 再加一道 `alembic check` 挡模型与 revision 漂移。
例外是历史库：更早的 dev 库可能被当年的 `create_all` 先把表建了出来，所以 v0.8 / v0.9
`upgrade()` 里的 `create_table` / `add_column` 先过 `sa.inspect(bind)` 判存。
