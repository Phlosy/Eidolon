"""v59 adds immutable canonical content without inventing legacy bytes."""

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect
from test_m3_drive_revision_content import create
from test_m3_session_purpose_migration import _migrate

from app.core.database import _alembic_config
from app.models.base import Base

PREVIOUS = "58d437ac6502"
REVISION = "59e548bd7603"


def contract(connection):
    return {
        "columns": sorted(
            (row["name"], str(row["type"]), row["nullable"], row["default"])
            for row in inspect(connection).get_columns("drive_revisions")
        ),
        "guards": list(
            connection.exec_driver_sql(
                "SELECT name,sql FROM sqlite_master WHERE type='trigger' "
                "AND name LIKE 'drive_revision_%' ORDER BY name"
            )
        ),
    }


def test_v59_up_down_up_preserves_unknown_content_and_matches_metadata(tmp_path):
    migrated = create_engine(f"sqlite:///{tmp_path / 'migrated.db'}")
    fresh = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    try:
        _migrate(migrated, PREVIOUS)
        with migrated.begin() as connection:
            connection.exec_driver_sql(
                "INSERT INTO drive_nodes (id,kind,name,path,zone,current_version,created_at,"
                "updated_at) VALUES (1,'document','Old','drive/old.md','projects',2,"
                "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            )
            connection.exec_driver_sql(
                "INSERT INTO drive_revisions (id,node_id,version,sha256,created_at) "
                "VALUES (1,1,1,'old-hash',CURRENT_TIMESTAMP)"
            )
            before = dict(
                connection.exec_driver_sql("SELECT * FROM drive_revisions").mappings().one()
            )
        Base.metadata.create_all(fresh)
        for iteration in range(2):
            _migrate(migrated, "head")
            with migrated.begin() as connection, fresh.connect() as metadata:
                assert contract(connection) == contract(metadata)
                after = dict(
                    connection.exec_driver_sql("SELECT * FROM drive_revisions").mappings().one()
                )
                assert after.pop("content_path") is None
                assert after == before
                config = _alembic_config()
                config.attributes["connection"] = connection
                command.check(config)
                assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
            if iteration == 0:
                _migrate(migrated, PREVIOUS, downgrade=True)
                with migrated.connect() as connection:
                    assert (
                        dict(
                            connection.exec_driver_sql("SELECT * FROM drive_revisions")
                            .mappings()
                            .one()
                        )
                        == before
                    )
    finally:
        migrated.dispose()
        fresh.dispose()


def test_v59_refuses_to_erase_saved_revision_content_before_ddl(db):
    create(db)
    engine = db.get_bind()
    with engine.connect() as connection:
        before = contract(connection)
        rows = connection.exec_driver_sql("SELECT * FROM drive_revisions").all()
    with pytest.raises(RuntimeError, match="erase preserved revision content"):
        _migrate(engine, PREVIOUS, downgrade=True)
    with engine.connect() as connection:
        assert contract(connection) == before
        assert connection.exec_driver_sql("SELECT * FROM drive_revisions").all() == rows
        assert (
            connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()
            == "62b87bef0916"
        )
