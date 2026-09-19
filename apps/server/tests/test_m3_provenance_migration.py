"""v50 preserves legacy records and enforces history on migrated/fresh SQLite."""

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from app.core.database import _alembic_config
from app.models.base import Base


def _legacy_session(connection):
    # Existing non-null task/employee ownership remains unchanged by v50. SQLite
    # does not enforce this project's FKs; this fixture isolates the session table.
    connection.execute(
        text("""
        INSERT INTO work_sessions
        (task_id, employee_id, runtime_type, runtime_session_ref, status, summary,
         cost, behavior_snapshot_hash, created_at, updated_at, ended_at)
        VALUES (1, 2, 'hermes', 'legacy-run', 'completed', 'legacy result', '{}', '',
                '2026-01-01', '2026-01-01', '2026-01-01')
    """)
    )


def _assert_guard(connection):
    with pytest.raises(IntegrityError, match="provenance is immutable"):
        connection.execute(text("UPDATE work_sessions SET runtime_type = 'mock'"))
    with pytest.raises(IntegrityError, match="provenance is immutable"):
        connection.execute(text("UPDATE work_sessions SET ended_at = NULL"))


def test_v50_up_down_up_preserves_legacy_unknowns_and_constraints(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'provenance-migration.db'}")
    config = _alembic_config()

    def migrate(revision, downgrade=False):
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            (command.downgrade if downgrade else command.upgrade)(config, revision)

    def verify():
        with engine.begin() as connection:
            row = connection.execute(text("SELECT * FROM work_sessions")).mappings().one()
            assert row["summary"] == "legacy result" and row["runtime_session_ref"] == "legacy-run"
            assert row["task_id"] == 1 and row["employee_id"] == 2
            for field in ("person_id", "company_id", "deployment_mode", "provenance_snapshot_json"):
                assert row[field] is None
            columns = {c["name"]: c for c in inspect(connection).get_columns("work_sessions")}
            assert not columns["task_id"]["nullable"] and not columns["employee_id"]["nullable"]
            _assert_guard(connection)
            config.attributes["connection"] = connection
            command.check(config)

    try:
        migrate("49f90481d32a")
        with engine.begin() as connection:
            _legacy_session(connection)
        migrate("head")
        verify()
        migrate("49f90481d32a", downgrade=True)
        with engine.begin() as connection:
            columns = {c["name"] for c in inspect(connection).get_columns("work_sessions")}
            assert "provenance_snapshot_json" not in columns
            assert connection.scalar(text("SELECT COUNT(*) FROM work_sessions")) == 1
            assert not connection.scalar(
                text(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type = 'trigger' "
                    "AND tbl_name = 'work_sessions'"
                )
            )
        migrate("head")
        verify()
        script = ScriptDirectory.from_config(config)
        assert len(script.get_heads()) == 1
        assert script.get_revision("50a7816c2e90").down_revision == "49f90481d32a"
    finally:
        engine.dispose()


def test_fresh_metadata_schema_has_same_provenance_guards(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    try:
        Base.metadata.create_all(engine)
        with engine.begin() as connection:
            _legacy_session(connection)
            _assert_guard(connection)
    finally:
        engine.dispose()
