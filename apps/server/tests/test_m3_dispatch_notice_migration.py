"""v51 is additive; old events survive and only one open cycle is allowed."""

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from app.core.database import _alembic_config


def test_v51_roundtrip_preserves_events_and_does_not_invent_historic_cycles(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'dispatch-notices.db'}")
    config = _alembic_config()

    def migrate(revision, downgrade=False):
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            (command.downgrade if downgrade else command.upgrade)(config, revision)

    def verify():
        with engine.begin() as connection:
            assert connection.scalar(text("SELECT count(*) FROM events")) == 1
            assert connection.scalar(text("SELECT count(*) FROM dispatch_notices")) == 0
            config.attributes["connection"] = connection
            command.check(config)
            query_plan = connection.execute(
                text("""
                EXPLAIN QUERY PLAN SELECT id, event_id FROM dispatch_notices
                WHERE closed_at IS NULL AND broadcast_at IS NULL AND event_id IS NOT NULL
                ORDER BY id LIMIT 100
            """)
            ).all()
            assert "ix_dispatch_notice_pending" in str(query_plan)
            statement = text("""
                INSERT INTO dispatch_notices
                (company_id, scope_type, scope_id, event_type, created_at, updated_at, event_id)
                VALUES (1, 'task', 1, 'task.runtime_unavailable', '2026-09-20', '2026-09-20', 1)
            """)
            connection.execute(statement)
            with pytest.raises(IntegrityError, match="UNIQUE"):
                connection.execute(statement)
            connection.execute(text("UPDATE dispatch_notices SET closed_at = '2026-09-20'"))
            connection.execute(statement)
            assert connection.scalar(text("SELECT count(*) FROM dispatch_notices")) == 2

    try:
        migrate("50a7816c2e90")
        with engine.begin() as connection:
            connection.execute(
                text("""
                INSERT INTO events (type, payload, created_at, updated_at)
                VALUES ('task.runtime_unavailable', '{}', '2026-09-20', '2026-09-20')
            """)
            )
        migrate("head")
        verify()
        migrate("50a7816c2e90", downgrade=True)
        with engine.begin() as connection:
            assert "dispatch_notices" not in inspect(connection).get_table_names()
            assert connection.scalar(text("SELECT count(*) FROM events")) == 1
            assert (
                connection.scalar(
                    text("""
                SELECT count(*) FROM sqlite_master WHERE type = 'trigger'
                AND tbl_name = 'work_sessions'
            """)
                )
                == 3
            )
        migrate("head")
        verify()
        script = ScriptDirectory.from_config(config)
        assert len(script.get_heads()) == 1
        assert script.get_revision("51b83e04a6f2").down_revision == "50a7816c2e90"
    finally:
        engine.dispose()
