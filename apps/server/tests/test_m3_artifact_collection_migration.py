"""v60 receipts preserve old unknown history and survive refused rollbacks."""

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect
from test_m3_artifact_acceptance import accept
from test_m3_artifact_acceptance import completed_world as _completed_world
from test_m3_artifact_acceptance import conversation_world as _conversation_world
from test_m3_artifact_acceptance import workspace_world as _workspace_world
from test_m3_artifact_context_migration import legacy_attempt
from test_m3_session_purpose_migration import _migrate

from app.core.database import _alembic_config
from app.models.base import Base

completed_world = _completed_world
conversation_world = _conversation_world
workspace_world = _workspace_world
PREVIOUS = "59e548bd7603"
REVISION = "62b87bef0916"


def contract(connection):
    inspector = inspect(connection)
    table = "runtime_artifact_collections"
    return {
        "columns": sorted(
            (r["name"], str(r["type"]), r["nullable"], r["default"])
            for r in inspector.get_columns(table)
        ),
        "foreign_keys": sorted(
            (tuple(r["constrained_columns"]), r["referred_table"], tuple(r["referred_columns"]))
            for r in inspector.get_foreign_keys(table)
        ),
        "checks": sorted(
            (r["name"], " ".join(r["sqltext"].split()))
            for r in inspector.get_check_constraints(table)
        ),
        "guards": list(
            connection.exec_driver_sql(
                "SELECT name,sql FROM sqlite_master WHERE type='trigger' "
                "AND name LIKE 'artifact_collection_%' ORDER BY name"
            )
        ),
    }


def test_v60_up_down_up_preserves_old_attempts_without_inventing_collections(tmp_path):
    migrated = create_engine(f"sqlite:///{tmp_path / 'migrated.db'}")
    fresh = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    try:
        _migrate(migrated, PREVIOUS)
        with migrated.begin() as connection:
            legacy_attempt(connection)
            before = dict(
                connection.exec_driver_sql("SELECT * FROM runtime_attempts").mappings().one()
            )
        Base.metadata.create_all(fresh)
        for iteration in range(2):
            _migrate(migrated, "head")
            with migrated.begin() as connection, fresh.connect() as metadata:
                assert "runtime_artifact_collections" in inspect(connection).get_table_names()
                assert contract(connection) == contract(metadata)
                after = dict(
                    connection.exec_driver_sql("SELECT * FROM runtime_attempts").mappings().one()
                )
                assert after.pop("stop_requested_at") is None
                assert after.pop("stop_reason") is None
                assert after == before
                assert (
                    connection.exec_driver_sql(
                        "SELECT COUNT(*) FROM runtime_artifact_collections"
                    ).scalar()
                    == 0
                )
                config = _alembic_config()
                config.attributes["connection"] = connection
                command.check(config)
                assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
            if iteration == 0:
                _migrate(migrated, PREVIOUS, downgrade=True)
                with migrated.connect() as connection:
                    assert (
                        "runtime_artifact_collections" not in inspect(connection).get_table_names()
                    )
                    assert (
                        dict(
                            connection.exec_driver_sql("SELECT * FROM runtime_attempts")
                            .mappings()
                            .one()
                        )
                        == before
                    )
    finally:
        migrated.dispose()
        fresh.dispose()


def test_v60_refuses_to_erase_accepted_collection_before_any_ddl(db, completed_world):
    accept(completed_world)
    engine = db.get_bind()
    with engine.connect() as connection:
        before = contract(connection)
        rows = connection.exec_driver_sql("SELECT * FROM runtime_artifact_collections").all()
    with pytest.raises(RuntimeError, match="erase accepted artifact collections"):
        _migrate(engine, PREVIOUS, downgrade=True)
    with engine.connect() as connection:
        assert contract(connection) == before
        assert (
            connection.exec_driver_sql("SELECT * FROM runtime_artifact_collections").all() == rows
        )
        assert (
            connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()
            == REVISION
        )
