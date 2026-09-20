"""The v55 checkpoint is rebuildable; durable dispatch history is preserved."""

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from test_m3_management_dispatch_migration import invalid_observation, migrate

from app.core.database import _alembic_config
from app.models.base import Base

PREVIOUS = "54e93ab821cd"
REVISION = "55a104d932ef"
TABLE = "management_discovery_cursors"


def schema(connection):
    inspector = inspect(connection)
    return {
        "columns": [
            (r["name"], str(r["type"]), r["nullable"], r["default"])
            for r in inspector.get_columns(TABLE)
        ],
        "checks": sorted(
            (r["name"], " ".join(r["sqltext"].split()))
            for r in inspector.get_check_constraints(TABLE)
        ),
        "pk": inspector.get_pk_constraint(TABLE)["constrained_columns"],
        "event_index": [
            (r["name"], r["column_names"], r["unique"])
            for r in inspector.get_indexes("events")
            if r["name"] == "ix_events_management_discovery"
        ],
        "triggers": [
            (name, " ".join(sql.split()))
            for name, sql in connection.exec_driver_sql(
                "SELECT name, sql FROM sqlite_master WHERE type='trigger' "
                "AND name LIKE 'management_discovery_%' ORDER BY name"
            )
        ],
    }


def test_v55_up_down_up_preserves_history_and_matches_metadata(tmp_path):
    migrated = create_engine(f"sqlite:///{tmp_path / 'migrated.db'}")
    fresh = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    try:
        migrate(migrated, PREVIOUS)
        with migrated.begin() as connection:
            invalid_observation(connection)
            history = connection.exec_driver_sql("SELECT * FROM management_dispatches").all()
        migrate(migrated, "head")
        assert TABLE in inspect(migrated).get_table_names()
        Base.metadata.create_all(fresh)
        with migrated.begin() as connection, fresh.begin() as metadata:
            assert schema(connection) == schema(metadata)
            assert len(schema(connection)["triggers"]) == 2
            assert schema(connection)["event_index"]
            for db in (connection, metadata):
                db.exec_driver_sql(
                    "INSERT INTO management_discovery_cursors (scope_company_id, last_event_id) "
                    "VALUES (0, 0)"
                )
                with pytest.raises(IntegrityError):
                    db.exec_driver_sql("UPDATE management_discovery_cursors SET last_event_id=-1")
                with pytest.raises(IntegrityError):
                    db.exec_driver_sql(
                        "UPDATE management_discovery_cursors SET scope_company_id=-1"
                    )
            config = _alembic_config()
            config.attributes["connection"] = connection
            command.check(config)
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
            assert (
                connection.exec_driver_sql("SELECT * FROM management_dispatches").all() == history
            )
        migrate(migrated, PREVIOUS, downgrade=True)
        assert TABLE not in inspect(migrated).get_table_names()
        with migrated.connect() as connection:
            assert (
                connection.exec_driver_sql("SELECT * FROM management_dispatches").all() == history
            )
            assert (
                connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE name LIKE 'management_discovery_%'"
                ).all()
                == []
            )
        migrate(migrated, "head")
        with migrated.connect() as connection, fresh.connect() as metadata:
            assert schema(connection) == schema(metadata)
            assert (
                connection.exec_driver_sql("SELECT * FROM management_discovery_cursors").all() == []
            )
            assert (
                connection.exec_driver_sql("SELECT * FROM management_dispatches").all() == history
            )
        scripts = ScriptDirectory.from_config(_alembic_config())
        assert scripts.get_heads() == ["60f659ce8704"]
        assert scripts.get_revision(REVISION).down_revision == PREVIOUS
    finally:
        migrated.dispose()
        fresh.dispose()
