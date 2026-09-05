"""SQLAlchemy engine / session factory / FastAPI dependency. See docs/architecture.md §1."""

from collections.abc import Iterator
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, inspect
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings


def _ensure_sqlite_dir(url: str) -> None:
    if url.startswith("sqlite:///"):
        db_path = Path(url.removeprefix("sqlite:///"))
        if str(db_path) != ":memory:":
            db_path.parent.mkdir(parents=True, exist_ok=True)


_ensure_sqlite_dir(settings.database_url)

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if settings.database_url.startswith("sqlite") else {},
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

_SERVER_ROOT = Path(__file__).resolve().parents[2]


class DatabaseSchemaError(RuntimeError):
    """Raised when an existing database is not at the repository's Alembic head."""


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _alembic_config() -> Config:
    return Config(str(_SERVER_ROOT / "alembic.ini"))


def _expected_heads(config: Config) -> set[str]:
    return set(ScriptDirectory.from_config(config).get_heads())


def _current_heads(connection) -> set[str]:
    return set(MigrationContext.configure(connection).get_current_heads())


def ensure_database_schema(target_engine: Engine = engine) -> None:
    """Initialize an empty database with Alembic or reject an out-of-date database."""
    config = _alembic_config()
    expected = _expected_heads(config)

    with target_engine.begin() as connection:
        tables = set(inspect(connection).get_table_names())
        if not tables:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            current = _current_heads(connection)
        elif "alembic_version" not in tables:
            raise DatabaseSchemaError(
                "Database schema is unversioned. Back it up and reconcile it with Alembic; "
                "an empty database can be initialized with `alembic upgrade head`."
            )
        else:
            current = _current_heads(connection)

    if current != expected:
        raise DatabaseSchemaError(
            "Database schema is not at the repository Alembic head "
            f"(current={sorted(current)}, expected={sorted(expected)}). "
            "Run `alembic upgrade head` before starting the server."
        )
