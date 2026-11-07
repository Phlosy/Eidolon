"""v2 test base: a real PostgreSQL, real migrations, truncated between tests.

No SQLite, no skipping: if PostgreSQL is unreachable the session fails loudly so
that nobody mistakes "did not run" for "passed" (v2-architecture §0, V1).
"""

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

SERVER_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_URL = "postgresql+psycopg://eidolon:eidolon@127.0.0.1:5433/eidolon_test"
# Precedence: explicit test URL > whatever the dev URL is > the compose/devpg default.
# `make test-v2` exports .env, so a dev URL is usually present; the *_test guard below
# is what keeps it from ever being used by mistake.
os.environ["EIDOLON_V2_DATABASE_URL"] = (
    os.environ.get("EIDOLON_V2_TEST_DATABASE_URL")
    or os.environ.get("EIDOLON_V2_DATABASE_URL")
    or DEFAULT_URL
)

from sqlalchemy import make_url  # noqa: E402

from eidolon.config import settings  # noqa: E402
from eidolon.db import SessionLocal, engine  # noqa: E402

KERNEL_TABLES = (
    "facts",
    "fact_chains",
    "fact_cursors",
    "memory_entries",
    "work_missions",
    "work_projects",
    "work_tasks",
    "work_artifacts",
    "identity_users",
    "identity_companies",
    "identity_persons",
    "identity_employments",
    "money_accounts",
    "money_journal",
    "talent_evidence",
    "talent_competencies",
    "auth_credentials",
    "auth_sessions",
)

# The session fixture drops and recreates schema "public". Refuse to do that to
# anything that is not unmistakably a test database.
_db_name = make_url(settings.database_url).database or ""
if not _db_name.endswith("_test"):
    raise SystemExit(
        f"refusing to run tests_v2 against database {_db_name!r}: the test database "
        "name must end with '_test' (set EIDOLON_V2_TEST_DATABASE_URL)"
    )


def alembic_config() -> Config:
    cfg = Config(str(SERVER_ROOT / "alembic-v2.ini"))
    cfg.set_main_option("script_location", str(SERVER_ROOT / "eidolon" / "migrations"))
    cfg.set_main_option("sqlalchemy.url", settings.database_url)
    cfg.attributes["configure_logger"] = False
    return cfg


@pytest.fixture(scope="session", autouse=True)
def database():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - environment failure
        pytest.exit(
            f"v2 tests need PostgreSQL at {settings.database_url}: {exc}\n"
            "Start one with `make pg-up` (docker) or `scripts/devpg.sh start`.",
            returncode=3,
        )
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    command.upgrade(alembic_config(), "head")
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def _clean_tables(database):
    yield
    with database.begin() as conn:
        conn.execute(text(f"TRUNCATE {', '.join(KERNEL_TABLES)}"))


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
