"""v56 is additive, preserves legacy unknowns and refuses destructive rollback."""

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect
from test_m3_session_purpose_migration import (
    _assert_original_guards,
    _migrate,
    _old_session,
    _targets,
)

from app.core.database import _alembic_config
from app.models.base import Base, utcnow
from app.models.project import WorkSession
from app.models.runtime_execution import RuntimeAttempt

PREVIOUS = "55a104d932ef"
REVISION = "56b215ea43f0"


def contract(connection):
    inspector = inspect(connection)
    return {
        "tables": {
            table: {
                "columns": sorted(
                    (row["name"], str(row["type"]), row["nullable"], row["default"])
                    for row in inspector.get_columns(table)
                ),
                "checks": sorted(
                    (row["name"], " ".join(row["sqltext"].split()))
                    for row in inspector.get_check_constraints(table)
                ),
                "indexes": sorted(
                    (row["name"], row["column_names"], row["unique"])
                    for row in inspector.get_indexes(table)
                ),
                "fks": [
                    (row["constrained_columns"], row["referred_table"], row["referred_columns"])
                    for row in inspector.get_foreign_keys(table)
                ],
            }
            for table in ("runtime_attempts", "runtime_telemetry")
        },
        "retry": [
            row
            for row in inspector.get_columns("work_sessions")
            if row["name"] == "technical_retry_count"
        ][0]["nullable"],
        "guards": [
            (name, " ".join(sql.split()))
            for name, sql in connection.exec_driver_sql(
                "SELECT name, sql FROM sqlite_master WHERE type='trigger' AND "
                "(name LIKE 'runtime_attempt_%' OR name LIKE 'runtime_telemetry_%' OR name "
                "LIKE 'work_session_%') ORDER BY name"
            )
        ],
    }


def test_v56_up_down_up_metadata_parity_and_legacy_unknown(tmp_path):
    migrated = create_engine(f"sqlite:///{tmp_path / 'migrated.db'}")
    fresh = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    try:
        _migrate(migrated, PREVIOUS)
        with migrated.begin() as connection:
            _targets(connection)
            _old_session(connection, snapshot='{"original":"kept"}')
            before = dict(
                connection.exec_driver_sql("SELECT * FROM work_sessions").mappings().one()
            )
        Base.metadata.create_all(fresh)
        for index in range(2):
            _migrate(migrated, "head")
            with migrated.begin() as connection, fresh.connect() as metadata:
                assert contract(connection) == contract(metadata)
                after = dict(
                    connection.exec_driver_sql("SELECT * FROM work_sessions").mappings().one()
                )
                assert after.pop("technical_retry_count") is None
                assert after == before
                assert (
                    connection.exec_driver_sql("SELECT COUNT(*) FROM runtime_attempts").scalar()
                    == 0
                )
                _assert_original_guards(connection, 1)
                config = _alembic_config()
                config.attributes["connection"] = connection
                command.check(config)
                assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
            if index == 0:
                _migrate(migrated, PREVIOUS, downgrade=True)
                with migrated.begin() as connection:
                    assert "runtime_attempts" not in inspect(connection).get_table_names()
                    assert (
                        dict(
                            connection.exec_driver_sql("SELECT * FROM work_sessions")
                            .mappings()
                            .one()
                        )
                        == before
                    )
                    _assert_original_guards(connection, 1)
        scripts = ScriptDirectory.from_config(_alembic_config())
        assert scripts.get_heads() == ["62b87bef0916"]
        assert scripts.get_revision(REVISION).down_revision == PREVIOUS
    finally:
        migrated.dispose()
        fresh.dispose()


def test_v56_downgrade_refuses_before_erasing_attempt_history(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'retained.db'}")
    try:
        _migrate(engine, "head")
        with engine.begin() as connection:
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
            before = connection.exec_driver_sql("SELECT * FROM runtime_attempts").all()
            schema = contract(connection)
        with pytest.raises(RuntimeError, match="erase durable runtime attempts"):
            _migrate(engine, PREVIOUS, downgrade=True)
        with engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT * FROM runtime_attempts").all() == before
            assert contract(connection) == schema
            assert (
                connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()
                == "62b87bef0916"
            )
    finally:
        engine.dispose()
