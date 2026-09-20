"""v62 preserves unknown stop history and prevents destructive rollback."""

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from test_m3_artifact_collection_migration import contract as collection_contract
from test_m3_artifact_context_migration import legacy_attempt
from test_m3_execution_migration import contract as execution_contract
from test_m3_session_purpose_migration import _migrate
from test_m3_telemetry import bound as _bound
from test_m3_telemetry import prepare
from test_m3_telemetry import worker_world as _worker_world

from app.core.database import _alembic_config
from app.models.base import Base

PREVIOUS = "61a76adf9805"
REVISION = "62b87bef0916"
bound = _bound
worker_world = _worker_world


def contract(connection):
    return execution_contract(connection), collection_contract(connection)


def test_v62_up_down_up_metadata_parity_without_invented_stop_history(tmp_path):
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
                assert contract(connection) == contract(metadata)
                after = dict(
                    connection.exec_driver_sql("SELECT * FROM runtime_attempts").mappings().one()
                )
                assert after.pop("stop_requested_at") is None
                assert after.pop("stop_reason") is None
                assert after == before
                for assignment in (
                    "stop_reason='cancelled'",
                    "stop_requested_at='2026-09-20 00:00:00'",
                    "stop_requested_at='bad-time', stop_reason='cancelled'",
                    "stop_requested_at='2026-09-20 00:00:00', stop_reason='invented'",
                ):
                    with pytest.raises(IntegrityError):
                        connection.exec_driver_sql(
                            f"UPDATE runtime_attempts SET {assignment} WHERE id=1"
                        )
                config = _alembic_config()
                config.attributes["connection"] = connection
                command.check(config)
                assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
            if iteration == 0:
                _migrate(migrated, PREVIOUS, downgrade=True)
                with migrated.connect() as connection:
                    assert (
                        dict(
                            connection.exec_driver_sql("SELECT * FROM runtime_attempts")
                            .mappings()
                            .one()
                        )
                        == before
                    )
        scripts = ScriptDirectory.from_config(_alembic_config())
        assert scripts.get_heads() == [REVISION]
        assert scripts.get_revision(REVISION).down_revision == PREVIOUS
    finally:
        migrated.dispose()
        fresh.dispose()


def test_v62_downgrade_refuses_to_erase_stop_before_any_ddl(db, bound):
    recorder = prepare(bound)
    recorder.request_stop("cancelled")
    engine = db.get_bind()
    with engine.connect() as connection:
        before = contract(connection)
        rows = connection.exec_driver_sql("SELECT * FROM runtime_attempts").all()
    with pytest.raises(RuntimeError, match="erase durable stop intent"):
        _migrate(engine, PREVIOUS, downgrade=True)
    with engine.connect() as connection:
        assert contract(connection) == before
        assert connection.exec_driver_sql("SELECT * FROM runtime_attempts").all() == rows
        assert (
            connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()
            == REVISION
        )
