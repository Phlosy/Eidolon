"""v54 preserves dedup history and installs identical SQLite/metadata guards."""

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from app.core.database import _alembic_config
from app.models.base import Base

PREVIOUS = "53d82fa710bc"
REVISION = "54e93ab821cd"


def migrate(engine, revision, *, downgrade=False):
    config = _alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        (command.downgrade if downgrade else command.upgrade)(config, revision)


def schema(connection):
    inspector = inspect(connection)
    table = "management_dispatches"
    return {
        "columns": sorted(
            (r["name"], str(r["type"]), r["nullable"], r["default"])
            for r in inspector.get_columns(table)
        ),
        "checks": sorted(
            (r["name"], " ".join(r["sqltext"].split()))
            for r in inspector.get_check_constraints(table)
        ),
        "indexes": sorted(
            (
                r["name"],
                tuple(r["column_names"]),
                r["unique"],
                str(r.get("dialect_options", {}).get("sqlite_where", "")),
            )
            for r in inspector.get_indexes(table)
        ),
        "fks": sorted(
            (tuple(r["constrained_columns"]), r["referred_table"], tuple(r["referred_columns"]))
            for r in inspector.get_foreign_keys(table)
        ),
        "triggers": connection.exec_driver_sql(
            "SELECT name, sql FROM sqlite_master WHERE type='trigger' AND "
            "tbl_name='management_dispatches' ORDER BY name"
        ).all(),
    }


def invalid_observation(connection):
    connection.exec_driver_sql(
        "INSERT INTO events (id, type, payload, created_at, updated_at) VALUES (1, "
        "'task.blocked', '{}', '2026-01-01', '2026-01-01')"
    )
    connection.exec_driver_sql(
        "INSERT INTO management_dispatches (source_event_id, source_type, purpose, "
        "resolution, resolution_reason, details_json, created_at, updated_at) VALUES (1, "
        "'task.blocked', 'management', 'invalid', 'source_scope_invalid', '{}', "
        "'2026-01-01', '2026-01-01')"
    )


def test_v54_up_down_up_check_and_metadata_parity(tmp_path):
    migrated = create_engine(f"sqlite:///{tmp_path / 'migrated.db'}")
    fresh = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    try:
        migrate(migrated, "head")
        with migrated.connect() as connection:
            expected = schema(connection)
        migrate(migrated, PREVIOUS, downgrade=True)
        assert "management_dispatches" not in inspect(migrated).get_table_names()
        migrate(migrated, "head")
        Base.metadata.create_all(fresh)
        with migrated.begin() as connection, fresh.begin() as metadata:
            assert schema(connection) == schema(metadata) == expected
            config = _alembic_config()
            config.attributes["connection"] = connection
            command.check(config)
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
            for db in (connection, metadata):
                invalid_observation(db)
                with pytest.raises(IntegrityError, match="immutable"):
                    db.exec_driver_sql("UPDATE management_dispatches SET source_type='task.failed'")
                with pytest.raises(IntegrityError, match="immutable"):
                    db.exec_driver_sql("DELETE FROM management_dispatches")
                with pytest.raises(IntegrityError):
                    db.exec_driver_sql("UPDATE management_dispatches SET work_session_id=99")
                with pytest.raises(IntegrityError):
                    db.exec_driver_sql(
                        "UPDATE management_dispatches SET details_json='{}', waiting_reason='"
                        + "x" * 65
                        + "'"
                    )
        scripts = ScriptDirectory.from_config(_alembic_config())
        assert scripts.get_heads() == ["59e548bd7603"]
        assert scripts.get_revision(REVISION).down_revision == PREVIOUS
    finally:
        migrated.dispose()
        fresh.dispose()


def test_v54_nonempty_downgrade_refuses_before_any_ddl(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'refused.db'}")
    try:
        migrate(engine, REVISION)
        with engine.begin() as connection:
            invalid_observation(connection)
            before = connection.exec_driver_sql(
                "SELECT name, sql FROM sqlite_master ORDER BY name"
            ).all()
        with pytest.raises(RuntimeError, match="dispatch dedup history"):
            migrate(engine, PREVIOUS, downgrade=True)
        with engine.connect() as connection:
            assert (
                connection.exec_driver_sql(
                    "SELECT name, sql FROM sqlite_master ORDER BY name"
                ).all()
                == before
            )
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == REVISION
            assert connection.scalar(text("SELECT COUNT(*) FROM management_dispatches")) == 1
    finally:
        engine.dispose()
