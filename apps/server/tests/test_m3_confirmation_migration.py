"""v57 preserves old history and matches metadata, including SQL receipt guards."""

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from test_m3_action_confirmations import owner, proposal
from test_m3_management_runtime import world as world
from test_m3_session_purpose_migration import (
    _assert_original_guards,
    _migrate,
    _old_session,
    _targets,
)

from app.core.database import _alembic_config
from app.models.action_confirmation import ActionConfirmation
from app.models.base import Base, utcnow
from app.models.project import WorkSession
from app.models.runtime_execution import RuntimeAttempt
from app.work import action_confirmations as confirmations

PREVIOUS = "56b215ea43f0"
REVISION = "57c326fb5401"


def contract(connection):
    inspector = inspect(connection)
    table = "action_confirmations"
    return {
        "columns": [
            (r["name"], str(r["type"]), r["nullable"], r["default"])
            for r in inspector.get_columns(table)
        ],
        "checks": sorted(
            (r["name"], " ".join(r["sqltext"].split()))
            for r in inspector.get_check_constraints(table)
        ),
        "indexes": sorted(
            (r["name"], r["column_names"], r["unique"]) for r in inspector.get_indexes(table)
        ),
        "unique": [r["column_names"] for r in inspector.get_unique_constraints(table)],
        "fks": [
            (r["constrained_columns"], r["referred_table"], r["referred_columns"])
            for r in inspector.get_foreign_keys(table)
        ],
        "guards": [
            (name, " ".join(sql.split()))
            for name, sql in connection.exec_driver_sql(
                "SELECT name, sql FROM sqlite_master WHERE type='trigger' "
                "AND name LIKE 'confirmation_%' ORDER BY name"
            )
        ],
    }


def test_v57_up_down_up_metadata_parity_and_existing_history(tmp_path):
    migrated = create_engine(f"sqlite:///{tmp_path / 'migrated.db'}")
    fresh = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    try:
        _migrate(migrated, PREVIOUS)
        with migrated.begin() as connection:
            _targets(connection)
            _old_session(connection, snapshot='{"original":"kept"}')
            before = connection.exec_driver_sql("SELECT * FROM work_sessions").all()
        Base.metadata.create_all(fresh)
        for index in range(2):
            _migrate(migrated, "head")
            with migrated.begin() as connection, fresh.connect() as metadata:
                assert contract(connection) == contract(metadata)
                assert connection.exec_driver_sql("SELECT * FROM work_sessions").all() == before
                assert (
                    connection.exec_driver_sql("SELECT COUNT(*) FROM action_confirmations").scalar()
                    == 0
                )
                _assert_original_guards(connection, 1)
                config = _alembic_config()
                config.attributes["connection"] = connection
                command.check(config)
                assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
            if index == 0:
                _migrate(migrated, PREVIOUS, downgrade=True)
                with migrated.connect() as connection:
                    assert "action_confirmations" not in inspect(connection).get_table_names()
                    assert connection.exec_driver_sql("SELECT * FROM work_sessions").all() == before
        scripts = ScriptDirectory.from_config(_alembic_config())
        assert scripts.get_heads() == ["62b87bef0916"]
        assert scripts.get_revision(REVISION).down_revision == PREVIOUS
    finally:
        migrated.dispose()
        fresh.dispose()


def test_v57_populated_downgrade_refuses_before_ddl(db, world, monkeypatch):
    _, _, _, row_id = proposal(db, world, monkeypatch)
    db.commit()
    with db.get_bind().connect() as connection:
        schema = contract(connection)
        before = connection.exec_driver_sql("SELECT * FROM action_confirmations").all()
    with pytest.raises(RuntimeError, match="erase durable action confirmations"):
        _migrate(db.get_bind(), PREVIOUS, downgrade=True)
    with db.get_bind().connect() as connection:
        assert contract(connection) == schema
        assert connection.exec_driver_sql("SELECT * FROM action_confirmations").all() == before
        assert (
            connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()
            == "62b87bef0916"
        )
    assert db.get(ActionConfirmation, row_id).status == "pending"


def test_sql_cannot_forge_review_or_misbind_receipts(client, db, world, monkeypatch):
    _, _, _, row_id = proposal(db, world, monkeypatch)
    identity = owner(client, db, world)
    invalid_updates = [
        "status = 'approved'",  # NULL checks must reject partial approval tuples.
        "review_outcome = 'approved', reviewer_user_id = 1",
        "person_id = 999",
        "proposal_audit_id = 999",
        "arguments_digest = '" + "b" * 64 + "'",
        "expires_at = '2100-01-01 00:00:00'",
        "status = 'expired', resolved_at = CURRENT_TIMESTAMP",  # Missing final receipt.
    ]
    for assignment in invalid_updates:
        with pytest.raises(IntegrityError):
            db.execute(
                text(f"UPDATE action_confirmations SET {assignment} WHERE id = :id"), {"id": row_id}
            )
        db.rollback()
    confirmations.review(db, row_id, **identity, approve=True)
    with pytest.raises(IntegrityError):
        db.execute(
            text(
                "UPDATE action_confirmations SET review_outcome = NULL, reviewed_at = NULL, "
                "reviewer_user_id = NULL, reviewer_session_id = NULL WHERE id = :id"
            ),
            {"id": row_id},
        )
    db.rollback()
    assert db.get(ActionConfirmation, row_id).status == "approved"


def test_lower_revision_refusal_rolls_back_v57_schema_too(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'earlier-history.db'}")
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
                    runtime_session_ref="local-original",
                    status="running",
                    technical_retry_count=0,
                )
            )
            connection.execute(
                RuntimeAttempt.__table__.insert().values(
                    work_session_id=1,
                    request_key="worker",
                    attempt_number=1,
                    idempotency_key="local-original",
                    request_digest="a" * 64,
                    local_session_ref="local-original",
                    kind="worker",
                    remote_session_ref="local-original",
                    status="prepared",
                    last_progress_at=utcnow(),
                )
            )
            before = contract(connection)
        with pytest.raises(RuntimeError, match="erase durable runtime attempts"):
            _migrate(engine, "55a104d932ef", downgrade=True)
        with engine.connect() as connection:
            assert contract(connection) == before
            assert (
                connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()
                == "62b87bef0916"
            )
    finally:
        engine.dispose()
