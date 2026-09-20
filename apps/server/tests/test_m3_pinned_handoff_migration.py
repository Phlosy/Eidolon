"""v61 preserves unknown legacy uses and refuses lossy downgrades."""

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect
from test_m3_artifact_context_migration import legacy_attempt
from test_m3_pinned_handoff import completed_world as _completed_world
from test_m3_pinned_handoff import conversation_world as _conversation_world
from test_m3_pinned_handoff import decide, declaration
from test_m3_pinned_handoff import delivered_world as _delivered_world
from test_m3_pinned_handoff import workspace_world as _workspace_world
from test_m3_session_purpose_migration import _migrate

from app.core.database import _alembic_config
from app.models.base import Base
from app.models.drive import DriveNode

completed_world = _completed_world
conversation_world = _conversation_world
workspace_world = _workspace_world
delivered_world = _delivered_world
PREVIOUS = "60f659ce8704"
REVISION = "62b87bef0916"


def contract(connection):
    inspector = inspect(connection)
    return {
        "columns": sorted(
            (r["name"], str(r["type"]), r["nullable"], r["default"])
            for r in inspector.get_columns("artifact_links")
        ),
        "foreign_keys": sorted(
            (tuple(r["constrained_columns"]), r["referred_table"], tuple(r["referred_columns"]))
            for r in inspector.get_foreign_keys("artifact_links")
        ),
        "unique": sorted(
            (r["name"], tuple(r["column_names"]))
            for r in inspector.get_unique_constraints("artifact_links")
        ),
        "indexes": sorted(
            (
                r["name"],
                r["unique"],
                tuple(r["column_names"]),
                str(r.get("dialect_options", {}).get("sqlite_where", "")),
            )
            for r in inspector.get_indexes("artifact_links")
        ),
        "guards": list(
            connection.exec_driver_sql(
                "SELECT name,sql FROM sqlite_master WHERE type='trigger' "
                "AND name LIKE 'artifact_link_%' ORDER BY name"
            )
        ),
    }


def legacy_link(connection):
    legacy_attempt(connection)
    connection.execute(
        DriveNode.__table__.insert().values(
            id=1,
            kind="document",
            name="Old output",
            path="drive/old.txt",
            zone="projects",
            project_id=1,
            company_id=1,
        )
    )
    connection.exec_driver_sql(
        "INSERT INTO artifact_links (artifact_id,task_id,role,reason,created_at,updated_at) "
        "VALUES (1,1,'consumed_by','old unknown version','2026-01-01','2026-01-01')"
    )


def test_v61_up_down_up_keeps_unknown_rows_and_matches_metadata(tmp_path):
    migrated = create_engine(f"sqlite:///{tmp_path / 'migrated.db'}")
    fresh = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    try:
        _migrate(migrated, PREVIOUS)
        with migrated.begin() as connection:
            legacy_link(connection)
            before = dict(
                connection.exec_driver_sql("SELECT * FROM artifact_links").mappings().one()
            )
        Base.metadata.create_all(fresh)
        for iteration in range(2):
            _migrate(migrated, "head")
            with migrated.begin() as connection, fresh.connect() as metadata:
                after = dict(
                    connection.exec_driver_sql("SELECT * FROM artifact_links").mappings().one()
                )
                assert after.pop("revision_id") is None
                assert after == before
                assert contract(connection) == contract(metadata)
                config = _alembic_config()
                config.attributes["connection"] = connection
                command.check(config)
                assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
            if iteration == 0:
                _migrate(migrated, PREVIOUS, downgrade=True)
                with migrated.connect() as connection:
                    assert (
                        dict(
                            connection.exec_driver_sql("SELECT * FROM artifact_links")
                            .mappings()
                            .one()
                        )
                        == before
                    )
    finally:
        migrated.dispose()
        fresh.dispose()


def test_v61_refuses_to_erase_pinned_reference_before_any_ddl(db, delivered_world):
    w = delivered_world
    decide(db, w)
    declaration(db, w)
    engine = db.get_bind()
    with engine.connect() as connection:
        before = contract(connection)
        rows = connection.exec_driver_sql("SELECT * FROM artifact_links").all()
    with pytest.raises(RuntimeError, match="erase artifact use history"):
        _migrate(engine, PREVIOUS, downgrade=True)
    with engine.connect() as connection:
        assert contract(connection) == before
        assert connection.exec_driver_sql("SELECT * FROM artifact_links").all() == rows
        assert (
            connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()
            == REVISION
        )


def test_v61_refuses_to_collapse_separate_unknown_uses(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'separate.db'}")
    try:
        _migrate(engine, PREVIOUS)
        with engine.begin() as connection:
            legacy_link(connection)
        _migrate(engine, "head")
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "INSERT INTO artifact_links "
                "(artifact_id,task_id,role,work_session_id,reason,created_at,updated_at) "
                "VALUES (1,1,'consumed_by',1,'later use','2026-02-01','2026-02-01')"
            )
            before = contract(connection)
            rows = connection.exec_driver_sql("SELECT * FROM artifact_links").all()
        with pytest.raises(RuntimeError, match="erase artifact use history"):
            _migrate(engine, PREVIOUS, downgrade=True)
        with engine.connect() as connection:
            assert contract(connection) == before
            assert connection.exec_driver_sql("SELECT * FROM artifact_links").all() == rows
    finally:
        engine.dispose()
