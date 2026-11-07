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
os.environ.setdefault(
    "EIDOLON_V2_DATABASE_URL", os.environ.get("EIDOLON_V2_TEST_DATABASE_URL", DEFAULT_URL)
)

from eidolon.config import settings  # noqa: E402
from eidolon.db import SessionLocal, engine  # noqa: E402

KERNEL_TABLES = ("facts", "fact_chains", "fact_cursors")


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
