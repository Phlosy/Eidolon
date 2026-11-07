"""Engine and session factory for v2. PostgreSQL only (K8)."""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from eidolon.config import settings


class Base(DeclarativeBase):
    """Declarative base for every v2 table. Tables are created only by Alembic."""


class UnsupportedDatabase(RuntimeError):
    pass


def require_postgres(url: str) -> None:
    if make_url(url).get_backend_name() != "postgresql":
        raise UnsupportedDatabase(
            "Eidolon v2 runs on PostgreSQL only; got "
            f"{make_url(url).render_as_string(hide_password=True)}"
        )


def build_engine(url: str | None = None, **kwargs) -> Engine:
    url = url or settings.database_url
    require_postgres(url)
    kwargs.setdefault("pool_pre_ping", True)
    kwargs.setdefault("future", True)
    return create_engine(url, **kwargs)


engine = build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def transaction(factory: sessionmaker = SessionLocal) -> Iterator[Session]:
    """One unit of work: commit on success, roll back on any exception."""
    db = factory()
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()
