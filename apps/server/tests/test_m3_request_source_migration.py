"""v64 keeps unknown legacy input and guards recoverable source facts."""

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from test_m3_artifact_context_migration import legacy_attempt
from test_m3_execution_migration import contract as execution_contract
from test_m3_runtime_conversation import ScriptedAdapter, read, run
from test_m3_runtime_conversation import world as _world
from test_m3_runtime_operation_migration import _operation
from test_m3_session_purpose_migration import _migrate

from app.core.database import _alembic_config
from app.models.base import Base

PREVIOUS = "63c98cff1a27"
REVISION = "64da10b82e39"
world = _world


def contract(connection):
    return {
        "execution": execution_contract(connection),
        "operation_columns": sorted(
            (row["name"], str(row["type"]), row["nullable"], row["default"])
            for row in inspect(connection).get_columns("runtime_operations")
        ),
        "operation_guards": list(
            connection.exec_driver_sql(
                "SELECT name,sql FROM sqlite_master WHERE type='trigger' "
                "AND name LIKE 'runtime_operation_%' ORDER BY name"
            )
        ),
    }


def test_v64_up_down_up_preserves_unknown_request_sources_and_metadata_parity(tmp_path):
    migrated = create_engine(f"sqlite:///{tmp_path / 'migrated.db'}")
    fresh = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    try:
        _migrate(migrated, PREVIOUS)
        with migrated.begin() as connection:
            legacy_attempt(connection)
            _operation(connection)
            before = {
                table: dict(connection.exec_driver_sql(f"SELECT * FROM {table}").mappings().one())
                for table in ("runtime_attempts", "runtime_operations")
            }
        Base.metadata.create_all(fresh)
        for iteration in range(2):
            _migrate(migrated, "head")
            with migrated.begin() as connection, fresh.connect() as metadata:
                assert contract(connection) == contract(metadata)
                for table, column in (
                    ("runtime_attempts", "reply_operation_id"),
                    ("runtime_operations", "request_body"),
                ):
                    after = dict(
                        connection.exec_driver_sql(f"SELECT * FROM {table}").mappings().one()
                    )
                    assert after.pop(column) is None
                    assert after == before[table]
                with pytest.raises(IntegrityError, match="invalid runtime request source"):
                    connection.exec_driver_sql(
                        "UPDATE runtime_operations SET request_body='{}' WHERE id=1"
                    )
                with pytest.raises(IntegrityError, match="invalid runtime execution fact"):
                    connection.exec_driver_sql(
                        "UPDATE runtime_attempts SET reply_operation_id=1 WHERE id=1"
                    )
                config = _alembic_config()
                config.attributes["connection"] = connection
                command.check(config)
                assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
            if iteration == 0:
                _migrate(migrated, PREVIOUS, downgrade=True)
                with migrated.connect() as connection:
                    for table, original in before.items():
                        assert (
                            dict(
                                connection.exec_driver_sql(f"SELECT * FROM {table}")
                                .mappings()
                                .one()
                            )
                            == original
                        )
        scripts = ScriptDirectory.from_config(_alembic_config())
        assert scripts.get_heads() == [REVISION]
        assert scripts.get_revision(REVISION).down_revision == PREVIOUS
    finally:
        migrated.dispose()
        fresh.dispose()


async def test_v64_refuses_to_erase_accepted_input_before_any_ddl(db, world):
    await run(world, ScriptedAdapter(read(world)))
    engine = db.get_bind()
    with engine.connect() as connection:
        before = contract(connection)
        rows = {
            table: connection.exec_driver_sql(f"SELECT * FROM {table}").all()
            for table in ("runtime_operations", "runtime_attempts")
        }
    with pytest.raises(RuntimeError, match="erase accepted runtime request sources"):
        _migrate(engine, PREVIOUS, downgrade=True)
    with engine.connect() as connection:
        assert contract(connection) == before
        for table, original in rows.items():
            assert connection.exec_driver_sql(f"SELECT * FROM {table}").all() == original
        assert (
            connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()
            == REVISION
        )
