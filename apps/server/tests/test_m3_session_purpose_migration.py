"""v53 preserves worker history, purpose guards and dependent SQLite records."""

import json

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from app.core.database import _alembic_config
from app.models.base import Base, utcnow
from app.models.organization import Company, Employee
from app.models.project import Artifact, Project, Task, WorkSession
from app.models.review import ReviewRequest

PREVIOUS = "52c94fd1a807"
REVISION = "53d82fa710bc"
ORIGINAL_SNAPSHOT = json.dumps({"version": 1, "task": {"id": 1}, "original": "unchanged"})


def _migrate(engine, revision, *, downgrade=False):
    config = _alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        (command.downgrade if downgrade else command.upgrade)(config, revision)


def _targets(connection):
    for identifier in (1, 2):
        connection.execute(
            Company.__table__.insert().values(
                id=identifier, name=f"Company {identifier}", slug=f"company-{identifier}"
            )
        )
        connection.execute(
            Employee.__table__.insert().values(
                id=identifier,
                company_id=identifier,
                name=f"Employee {identifier}",
                slug=f"employee-{identifier}",
                workspace_path=f"/unused/{identifier}",
                memory_namespace=f"unused-{identifier}",
            )
        )
        connection.execute(
            Project.__table__.insert().values(
                id=identifier,
                company_id=identifier,
                name=f"Project {identifier}",
                management_employee_id=identifier,
            )
        )
        connection.execute(
            Task.__table__.insert().values(
                id=identifier, project_id=identifier, title=f"Task {identifier}", status="in_review"
            )
        )
        connection.execute(
            ReviewRequest.__table__.insert().values(
                id=identifier,
                company_id=identifier,
                project_id=identifier,
                task_id=identifier,
                requested_by_employee_id=identifier,
                reviewer_employee_id=identifier,
                requested_at=utcnow(),
            )
        )


def _old_session(connection, *, snapshot=None):
    return connection.execute(
        text("""
        INSERT INTO work_sessions
        (task_id, employee_id, runtime_type, runtime_session_ref, status, summary,
         cost, behavior_snapshot_hash, created_at, updated_at, ended_at,
         provenance_snapshot_json)
        VALUES (1, 1, 'hermes', 'legacy-run', 'completed', 'legacy result', '{}', '',
                '2026-01-01', '2026-01-01', '2026-01-01', :snapshot)
        RETURNING id
        """),
        {"snapshot": snapshot},
    ).scalar_one()


def _dependent_records(connection):
    connection.execute(
        Artifact.__table__.insert().values(
            company_id=1, project_id=1, task_id=1, title="Original artifact", work_session_id=1
        )
    )
    connection.exec_driver_sql("""
        INSERT INTO runtime_operations
        (created_at, updated_at, work_session_id, operation_key, request_digest, kind,
         requested_action_count)
        VALUES ('2026-01-01', '2026-01-01', 1, 'original-read',
                'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'read', 1)
    """)
    connection.exec_driver_sql("""
        INSERT INTO tool_audits
        (tool_name, outcome, autonomy, transport, origin, arguments_json, arguments_digest,
         authority_reason, authority_grant_ids, authority_grants_hash, error, started_at,
         created_at, updated_at, work_session_id, result_json, runtime_operation_id, action_index)
        VALUES ('inspect_project', 'read', 'auto_allowed', 'internal', 'work_session',
                '{"project_id":1}', 'original-digest', '', '[]', '', '', '2026-01-01',
                '2026-01-01', '2026-01-01', 1, '{"project":{"name":"Original"}}', 1, 0)
    """)


def _new_session(connection, purpose="management", **overrides):
    values = dict(
        purpose=purpose,
        task_id=None,
        project_id=1,
        employee_id=1,
        company_id=1,
        runtime_type="mock",
    )
    if purpose == "review":
        values["review_request_id"] = 1
    return connection.execute(
        WorkSession.__table__.insert().values(**(values | overrides))
    ).inserted_primary_key[0]


def _sqlite_schema(connection):
    return connection.exec_driver_sql(
        "SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name"
    ).all()


def _assert_original_guards(connection, identifier):
    for change in (
        "runtime_type='mock'",
        "ended_at=NULL",
        "runtime_session_ref='replacement'",
        "provenance_snapshot_json='{}'",
    ):
        with pytest.raises(IntegrityError, match="provenance is immutable"):
            connection.execute(
                text(f"UPDATE work_sessions SET {change} WHERE id=:id"), {"id": identifier}
            )


def _assert_purpose_guards(connection):
    assert connection.scalar(text("PRAGMA foreign_keys")) == 0
    for values in (
        {"purpose": "worker", "task_id": None, "project_id": None},
        {"purpose": "worker", "task_id": 1},
        {"purpose": "worker", "task_id": 1, "project_id": None, "review_request_id": 1},
        {"purpose": "management", "project_id": None},
        {"purpose": "management", "task_id": 1},
        {"purpose": "management", "review_request_id": 1},
        {"purpose": "review", "review_request_id": None},
        {"purpose": "review", "task_id": 1},
        {"purpose": "unknown"},
        {"purpose": None},
    ):
        with pytest.raises(IntegrityError):
            _new_session(connection, **values)
    # Explicit coherence guards are necessary: this deployment does not enable FKs.
    for purpose in ("management", "review"):
        for invalid in (
            {"project_id": 99999},
            {"project_id": 2},
            {"company_id": 2},
            {"company_id": None},
            {"employee_id": 2},
            {"employee_id": 99999},
        ):
            with pytest.raises(IntegrityError, match="invalid work session target"):
                _new_session(connection, purpose, **invalid)
        if purpose == "review":
            for invalid in ({"review_request_id": 99999}, {"review_request_id": 2}):
                with pytest.raises(IntegrityError, match="invalid work session target"):
                    _new_session(connection, purpose, **invalid)
        identifier = _new_session(connection, purpose)
        for change in (
            "purpose='worker'",
            "task_id=1",
            "project_id=2",
            "review_request_id=2",
            "company_id=2",
            "runtime_type='hermes'",
        ):
            with pytest.raises(IntegrityError, match="provenance is immutable"):
                connection.execute(
                    text(f"UPDATE work_sessions SET {change} WHERE id=:id"), {"id": identifier}
                )
        connection.execute(
            text("UPDATE work_sessions SET runtime_session_ref='first-run' WHERE id=:id"),
            {"id": identifier},
        )
        with pytest.raises(IntegrityError, match="provenance is immutable"):
            connection.execute(
                text("UPDATE work_sessions SET runtime_session_ref='replacement' WHERE id=:id"),
                {"id": identifier},
            )
        connection.execute(
            text("UPDATE work_sessions SET ended_at='2026-09-20', status='completed' WHERE id=:id"),
            {"id": identifier},
        )
        with pytest.raises(IntegrityError, match="provenance is immutable"):
            connection.execute(
                text("UPDATE work_sessions SET ended_at=NULL WHERE id=:id"), {"id": identifier}
            )


def _session_contract(connection):
    inspector = inspect(connection)
    return {
        "columns": sorted(
            (row["name"], str(row["type"]), row["nullable"], row["default"])
            for row in inspector.get_columns("work_sessions")
        ),
        "checks": sorted(
            (row["name"], " ".join(row["sqltext"].split()))
            for row in inspector.get_check_constraints("work_sessions")
        ),
        "indexes": sorted(
            (row["name"], tuple(row["column_names"]), row["unique"])
            for row in inspector.get_indexes("work_sessions")
        ),
        "foreign_keys": sorted(
            (
                tuple(row["constrained_columns"]),
                row["referred_table"],
                tuple(row["referred_columns"]),
            )
            for row in inspector.get_foreign_keys("work_sessions")
        ),
        "triggers": connection.exec_driver_sql(
            "SELECT name, sql FROM sqlite_master WHERE type='trigger' "
            "AND tbl_name='work_sessions' ORDER BY name"
        ).all(),
    }


def test_v53_up_down_up_preserves_history_and_inbound_receipts(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'purpose-migration.db'}")
    try:
        _migrate(engine, PREVIOUS)
        with engine.begin() as connection:
            _targets(connection)
            assert _old_session(connection) == 1
            assert _old_session(connection, snapshot=ORIGINAL_SNAPSHOT) == 2
            _dependent_records(connection)
            before_rows = (
                connection.exec_driver_sql("SELECT * FROM work_sessions ORDER BY id")
                .mappings()
                .all()
            )
        for iteration in range(2):
            _migrate(engine, "head")
            with engine.connect() as connection:
                transaction = connection.begin()
                rows = (
                    connection.exec_driver_sql("SELECT * FROM work_sessions ORDER BY id")
                    .mappings()
                    .all()
                )
                assert len(rows) == len(before_rows)
                for before, after in zip(before_rows, rows, strict=True):
                    assert {key: after[key] for key in before} == dict(before)
                    assert after["purpose"] == "worker"
                    assert after["project_id"] is None and after["review_request_id"] is None
                    _assert_original_guards(connection, after["id"])
                columns = {
                    row["name"]: row for row in inspect(connection).get_columns("work_sessions")
                }
                assert columns["task_id"]["nullable"]
                assert not columns["employee_id"]["nullable"] and not columns["purpose"]["nullable"]
                assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
                assert connection.scalar(text("SELECT work_session_id FROM artifacts")) == 1
                assert (
                    connection.scalar(text("SELECT work_session_id FROM runtime_operations")) == 1
                )
                assert (
                    connection.scalar(text("SELECT result_json FROM tool_audits"))
                    == '{"project":{"name":"Original"}}'
                )
                with pytest.raises(IntegrityError, match="runtime receipt is immutable"):
                    connection.exec_driver_sql("UPDATE tool_audits SET result_json='{}'")
                with pytest.raises(IntegrityError, match="identity is immutable"):
                    connection.exec_driver_sql("DELETE FROM runtime_operations")
                _assert_purpose_guards(connection)
                default_id = _old_session(connection)
                assert (
                    connection.scalar(
                        text("SELECT purpose FROM work_sessions WHERE id=:id"), {"id": default_id}
                    )
                    == "worker"
                )
                config = _alembic_config()
                config.attributes["connection"] = connection
                command.check(config)
                transaction.rollback()
            if iteration == 0:
                _migrate(engine, PREVIOUS, downgrade=True)
                with engine.connect() as connection:
                    columns = {
                        row["name"]: row for row in inspect(connection).get_columns("work_sessions")
                    }
                    assert "purpose" not in columns and not columns["task_id"]["nullable"]
                    assert (
                        connection.exec_driver_sql("SELECT * FROM work_sessions ORDER BY id")
                        .mappings()
                        .all()
                        == before_rows
                    )
                    _assert_original_guards(connection, 1)
                    assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        scripts = ScriptDirectory.from_config(_alembic_config())
        assert scripts.get_heads() == ["61a76adf9805"]
        assert scripts.get_revision(REVISION).down_revision == PREVIOUS
    finally:
        engine.dispose()


def test_metadata_and_migration_have_identical_session_constraints(tmp_path):
    migrated = create_engine(f"sqlite:///{tmp_path / 'migrated.db'}")
    fresh = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    try:
        _migrate(migrated, "head")
        Base.metadata.create_all(fresh)
        with migrated.begin() as connection, fresh.begin() as metadata_connection:
            assert _session_contract(connection) == _session_contract(metadata_connection)
            for target in (connection, metadata_connection):
                _targets(target)
                _old_session(target)
                _assert_original_guards(target, 1)
                _assert_purpose_guards(target)
    finally:
        migrated.dispose()
        fresh.dispose()


@pytest.mark.parametrize("purpose", ["management", "review"])
def test_incompatible_downgrade_refuses_before_any_destructive_ddl(tmp_path, purpose):
    engine = create_engine(f"sqlite:///{tmp_path / 'refused-downgrade.db'}")
    try:
        _migrate(engine, REVISION)
        with engine.begin() as connection:
            _targets(connection)
            _old_session(connection)
            _dependent_records(connection)
            _new_session(connection, purpose)
            before = _sqlite_schema(connection)
            before_rows = connection.exec_driver_sql(
                "SELECT * FROM work_sessions ORDER BY id"
            ).all()
        with pytest.raises(RuntimeError, match="non-worker work sessions"):
            _migrate(engine, PREVIOUS, downgrade=True)
        with engine.connect() as connection:
            assert _sqlite_schema(connection) == before
            assert (
                connection.exec_driver_sql("SELECT * FROM work_sessions ORDER BY id").all()
                == before_rows
            )
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == REVISION
            assert connection.scalar(text("SELECT COUNT(*) FROM runtime_operations")) == 1
            _assert_original_guards(connection, 1)
    finally:
        engine.dispose()


@pytest.mark.parametrize("downgrade", [False, True])
def test_fk_enabled_rebuild_is_rejected_before_schema_changes(tmp_path, downgrade):
    engine = create_engine(f"sqlite:///{tmp_path / 'fk-enabled.db'}")
    try:
        _migrate(engine, REVISION if downgrade else PREVIOUS)
        with engine.begin() as connection:
            _targets(connection)
            _old_session(connection)
            _dependent_records(connection)
            before = _sqlite_schema(connection)
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            assert connection.scalar(text("PRAGMA foreign_keys")) == 1
            config = _alembic_config()
            config.attributes["connection"] = connection
            with pytest.raises(RuntimeError, match="foreign_keys=OFF"):
                (command.downgrade if downgrade else command.upgrade)(
                    config, PREVIOUS if downgrade else REVISION
                )
            assert _sqlite_schema(connection) == before
            assert connection.scalar(text("SELECT COUNT(*) FROM work_sessions")) == 1
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    finally:
        engine.dispose()
