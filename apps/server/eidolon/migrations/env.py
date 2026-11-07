"""Alembic env for Eidolon v2. Metadata = ``eidolon.db.Base``; URL from v2 settings
unless the caller passes a connection (tests do)."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from eidolon.config import settings
from eidolon.db import Base, require_postgres

# Import every module that declares tables so autogenerate sees them.
import eidolon.agent.memory.projection  # noqa: F401
import eidolon.identity.projection  # noqa: F401
import eidolon.kernel.store  # noqa: F401
import eidolon.work.projection  # noqa: F401

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _url() -> str:
    url = config.get_main_option("sqlalchemy.url") or settings.database_url
    require_postgres(url)
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table=config.get_main_option("version_table", "alembic_version_v2"),
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    version_table = config.get_main_option("version_table", "alembic_version_v2")
    if connection is not None:
        context.configure(
            connection=connection, target_metadata=target_metadata, version_table=version_table
        )
        with context.begin_transaction():
            context.run_migrations()
        return

    engine = engine_from_config(
        {"sqlalchemy.url": _url()}, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with engine.connect() as conn:
        context.configure(
            connection=conn, target_metadata=target_metadata, version_table=version_table
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
