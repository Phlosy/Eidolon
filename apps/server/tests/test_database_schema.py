"""Database startup ownership belongs to Alembic, never SQLAlchemy create_all."""

from sqlalchemy import create_engine, inspect, text

from app.core.database import DatabaseSchemaError, ensure_database_schema


def test_empty_database_is_initialized_through_alembic(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")

    ensure_database_schema(engine)

    with engine.connect() as connection:
        heads = set(connection.execute(text("SELECT version_num FROM alembic_version")).scalars())
        assert heads
        assert "companies" in inspect(connection).get_table_names()


def test_existing_unversioned_database_is_rejected(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE legacy_data (id INTEGER PRIMARY KEY)"))

    try:
        ensure_database_schema(engine)
    except DatabaseSchemaError as exc:
        assert "alembic upgrade head" in str(exc)
        assert "unversioned" in str(exc)
    else:
        raise AssertionError("an existing database without Alembic metadata must be rejected")
