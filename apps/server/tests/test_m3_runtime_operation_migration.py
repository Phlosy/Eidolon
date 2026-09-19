"""v52 migrations preserve nullable legacy audits and enforce durable receipts."""

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from app.core.database import _alembic_config
from app.models.base import Base


def _legacy_audit(connection):
    connection.exec_driver_sql("""
        INSERT INTO tool_audits
        (tool_name, outcome, autonomy, transport, origin, arguments_json, arguments_digest,
         authority_reason, authority_grant_ids, authority_grants_hash, error, started_at,
         created_at, updated_at, work_session_id, result_json)
        VALUES ('inspect_project', 'read', 'auto_allowed', 'internal', 'work_session',
                '{"project_id":1}', 'legacy-digest', '', '[]', '', '', '2026-01-01',
                '2026-01-01', '2026-01-01', 1, '{"project":{"name":"Original"}}')
    """)


def _operation(connection, key="read", kind="read", count=1):
    return connection.execute(
        text("""
        INSERT INTO runtime_operations
        (created_at, updated_at, work_session_id, operation_key, request_digest, kind,
         requested_action_count)
        VALUES ('2026-01-01', '2026-01-01', 1, :key, :digest, :kind, :count)
        RETURNING id
    """),
        {"key": key, "digest": "a" * 64, "kind": kind, "count": count},
    ).scalar_one()


def _assert_guards(connection):
    op_id = _operation(connection)
    for field, value in (
        ("request_digest", "'" + "b" * 64 + "'"),
        ("operation_key", "'changed'"),
        ("work_session_id", "2"),
        ("kind", "'finish'"),
        ("requested_action_count", "0"),
        ("id", "99"),
        ("created_at", "'2027-01-01'"),
    ):
        with pytest.raises(IntegrityError, match="identity is immutable"):
            connection.execute(
                text(f"UPDATE runtime_operations SET {field} = {value} WHERE id = :id"),
                {"id": op_id},
            )
    with pytest.raises(IntegrityError, match="identity is immutable"):
        connection.execute(text("DELETE FROM runtime_operations WHERE id = :id"), {"id": op_id})
    with pytest.raises(IntegrityError, match="UNIQUE"):
        _operation(connection)
    for kind, count in (("finish", 1), ("decision", 0), ("decision", 17), ("read", 2)):
        with pytest.raises(IntegrityError, match="ck_runtime_operation_kind_count"):
            _operation(connection, key="invalid", kind=kind, count=count)
    decision_op = _operation(connection, key="decision", kind="decision", count=2)
    connection.execute(
        text("UPDATE runtime_operations SET decision_id = 12 WHERE id = :id"), {"id": decision_op}
    )
    for value in ("13", "NULL"):
        with pytest.raises(IntegrityError, match="decision is already bound"):
            connection.execute(
                text(f"UPDATE runtime_operations SET decision_id = {value} WHERE id = :id"),
                {"id": decision_op},
            )
    with pytest.raises(IntegrityError, match="ck_runtime_operation_decision"):
        connection.execute(
            text("UPDATE runtime_operations SET decision_id = 12 WHERE id = :id"), {"id": op_id}
        )

    # Both-null legacy rows stay legal. Correlation requires a valid pair, index,
    # operation and matching session/decision. No historical values are inferred.
    for pair in ((op_id, None), (None, 0), (op_id, -1), (op_id, 1), (9999, 0), (decision_op, 0)):
        with pytest.raises(IntegrityError, match="invalid runtime receipt correlation"):
            connection.execute(
                text(
                    "UPDATE tool_audits SET runtime_operation_id=:op, action_index=:index "
                    "WHERE id=1"
                ),
                {"op": pair[0], "index": pair[1]},
            )
    with pytest.raises(IntegrityError, match="invalid runtime receipt correlation"):
        connection.execute(
            text(
                "UPDATE tool_audits SET runtime_operation_id=:id, action_index=0, "
                "work_session_id=2 WHERE id=1"
            ),
            {"id": op_id},
        )
    connection.execute(
        text("UPDATE tool_audits SET runtime_operation_id=:id, action_index=0 WHERE id=1"),
        {"id": op_id},
    )
    for assignment in (
        "result_json='{}'",
        "arguments_json='{}'",
        "outcome='applied'",
        "runtime_operation_id=NULL, action_index=NULL",
        "action_index=0",
        "error='changed'",
    ):
        with pytest.raises(IntegrityError, match="runtime receipt is immutable"):
            connection.execute(text(f"UPDATE tool_audits SET {assignment} WHERE id=1"))
    with pytest.raises(IntegrityError, match="runtime receipt is immutable"):
        connection.execute(text("DELETE FROM tool_audits WHERE id=1"))
    _legacy_audit(connection)
    with pytest.raises(IntegrityError, match="UNIQUE"):
        connection.execute(
            text("UPDATE tool_audits SET runtime_operation_id=:id, action_index=0 WHERE id=2"),
            {"id": op_id},
        )


def test_v52_up_down_up_check_preserves_legacy_audits_and_guards(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'runtime-operations.db'}")
    config = _alembic_config()

    def migrate(revision, *, downgrade=False):
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            (command.downgrade if downgrade else command.upgrade)(config, revision)

    def verify():
        with engine.connect() as connection:
            transaction = connection.begin()
            row = connection.execute(text("SELECT * FROM tool_audits WHERE id=1")).mappings().one()
            assert row["runtime_operation_id"] is None and row["action_index"] is None
            assert row["arguments_digest"] == "legacy-digest"
            assert row["result_json"] == '{"project":{"name":"Original"}}'
            assert connection.scalar(text("SELECT COUNT(*) FROM runtime_operations")) == 0
            config.attributes["connection"] = connection
            command.check(config)
            _assert_guards(connection)
            transaction.rollback()

    try:
        migrate("51b83e04a6f2")
        with engine.begin() as connection:
            _legacy_audit(connection)
        migrate("head")
        verify()
        migrate("51b83e04a6f2", downgrade=True)
        with engine.connect() as connection:
            assert "runtime_operations" not in inspect(connection).get_table_names()
            columns = {row["name"] for row in inspect(connection).get_columns("tool_audits")}
            assert "runtime_operation_id" not in columns and "action_index" not in columns
            assert connection.scalar(text("SELECT COUNT(*) FROM tool_audits")) == 1
            assert not connection.scalar(
                text(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' "
                    "AND name LIKE 'runtime_%'"
                )
            )
        migrate("head")
        verify()
        script = ScriptDirectory.from_config(config)
        assert script.get_heads() == ["52c94fd1a807"]
        assert script.get_revision("52c94fd1a807").down_revision == "51b83e04a6f2"
    finally:
        engine.dispose()


def test_create_all_installs_the_same_guards_and_nullable_legacy_columns(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'metadata-runtime-operations.db'}")
    try:
        Base.metadata.create_all(engine)
        with engine.begin() as connection:
            columns = {row["name"]: row for row in inspect(connection).get_columns("tool_audits")}
            assert (
                columns["runtime_operation_id"]["nullable"] and columns["action_index"]["nullable"]
            )
            _legacy_audit(connection)
            _assert_guards(connection)
    finally:
        engine.dispose()
