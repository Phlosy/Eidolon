"""Database startup ownership belongs to Alembic, never SQLAlchemy create_all."""

import logging

from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from app.core.database import DatabaseSchemaError, _alembic_config, ensure_database_schema
from app.core.logging import ContextFilter, RedactionFilter


def _upgrade(engine, revision: str) -> None:
    config = _alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, revision)


def test_empty_database_is_initialized_through_alembic(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    expected_heads = set(ScriptDirectory.from_config(_alembic_config()).get_heads())

    ensure_database_schema(engine)

    with engine.connect() as connection:
        heads = set(connection.execute(text("SELECT version_num FROM alembic_version")).scalars())
        assert heads == expected_heads
        assert "companies" in inspect(connection).get_table_names()


def test_empty_database_initialization_preserves_application_logging(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'logging.db'}")
    root = logging.getLogger()
    app_logger = logging.getLogger("app.tests.database_schema")
    previous_handlers = root.handlers[:]
    previous_level = root.level
    previous_disabled = app_logger.disabled
    handler = logging.StreamHandler()
    handler.addFilter(ContextFilter())
    handler.addFilter(RedactionFilter())
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    app_logger.disabled = False

    try:
        ensure_database_schema(engine)

        assert root.handlers == [handler]
        assert [type(filter_) for filter_ in handler.filters] == [ContextFilter, RedactionFilter]
        assert root.level == logging.INFO
        assert app_logger.disabled is False
    finally:
        root.handlers = previous_handlers
        root.setLevel(previous_level)
        app_logger.disabled = previous_disabled


def test_existing_unversioned_database_is_rejected(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE legacy_data (id INTEGER PRIMARY KEY)"))

    try:
        ensure_database_schema(engine)
    except DatabaseSchemaError as exc:
        assert "alembic upgrade head" in str(exc)
        assert "unversioned" in str(exc)
    else:
        raise AssertionError("an existing database without Alembic metadata must be rejected")


def test_existing_database_at_head_is_accepted(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'current.db'}")
    _upgrade(engine, "head")

    ensure_database_schema(engine)


def test_existing_outdated_database_is_rejected(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'outdated.db'}")
    script = ScriptDirectory.from_config(_alembic_config())
    head = script.get_current_head()
    previous = script.get_revision(head).down_revision
    assert isinstance(previous, str)
    _upgrade(engine, previous)

    try:
        ensure_database_schema(engine)
    except DatabaseSchemaError as exc:
        assert "alembic upgrade head" in str(exc)
        assert previous in str(exc)
        assert head in str(exc)
    else:
        raise AssertionError("an existing database behind Alembic head must be rejected")
