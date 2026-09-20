"""v58 preserves unknown historical workspace identity and every earlier guard."""

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from test_m3_artifact_workspace_identity import conversation_world as _conversation_world
from test_m3_artifact_workspace_identity import prepare
from test_m3_artifact_workspace_identity import workspace_world as _workspace_world
from test_m3_session_purpose_migration import _migrate, _targets

from app.core.database import _alembic_config
from app.models.base import Base, utcnow
from app.models.project import WorkSession
from app.models.runtime_execution import RuntimeAttempt

PREVIOUS = "57c326fb5401"
REVISION = "58d437ac6502"
conversation_world = _conversation_world
workspace_world = _workspace_world


def legacy_attempt(connection):
    _targets(connection)
    connection.execute(
        WorkSession.__table__.insert().values(
            id=1,
            task_id=1,
            employee_id=1,
            runtime_type="mock",
            runtime_session_ref="original-local",
            status="running",
            technical_retry_count=0,
        )
    )
    connection.execute(
        RuntimeAttempt.__table__.insert().values(
            id=1,
            work_session_id=1,
            request_key="worker",
            attempt_number=1,
            idempotency_key="original-local",
            request_digest="a" * 64,
            local_session_ref="original-local",
            kind="worker",
            remote_session_ref="original-local",
            status="prepared",
            last_progress_at=utcnow(),
        )
    )


def contract(connection):
    inspector = inspect(connection)
    return {
        "columns": sorted(
            (r["name"], str(r["type"]), r["nullable"], r["default"])
            for r in inspector.get_columns("runtime_attempts")
        ),
        "guards": list(
            connection.exec_driver_sql(
                "SELECT name,sql FROM sqlite_master WHERE type='trigger' "
                "AND name LIKE 'runtime_attempt_%' ORDER BY name"
            )
        ),
    }


def test_v58_up_down_up_preserves_old_attempt_and_matches_metadata(tmp_path):
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
                assert after.pop("artifact_context_json") is None
                assert after == before
                with pytest.raises(IntegrityError):
                    connection.exec_driver_sql(
                        "UPDATE runtime_attempts SET artifact_context_json='{}' WHERE id=1"
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
    finally:
        migrated.dispose()
        fresh.dispose()


def test_v58_refuses_to_erase_workspace_snapshot_before_any_ddl(db, workspace_world):
    prepare(workspace_world)
    engine = db.get_bind()
    with engine.connect() as connection:
        before = contract(connection)
        rows = connection.exec_driver_sql("SELECT * FROM runtime_attempts").all()
    with pytest.raises(RuntimeError, match="erase original artifact context"):
        _migrate(engine, PREVIOUS, downgrade=True)
    with engine.connect() as connection:
        assert contract(connection) == before
        assert connection.exec_driver_sql("SELECT * FROM runtime_attempts").all() == rows
        assert (
            connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()
            == REVISION
        )
