"""v46 owner backfill, reversible schema and model drift verification."""

from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import MetaData, Table, create_engine, inspect, select
from sqlalchemy.orm import Session

from app.core.database import _alembic_config
from app.models.base import utcnow
from app.models.organization import Company, Employee


def test_v46_classifies_only_recorded_runtime_intents_and_roundtrips(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'v46.db'}")
    config = _alembic_config()

    def migrate(revision):
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, revision)

    try:
        migrate("325887b7109a")
        with Session(engine) as db:
            company = Company(name="Legacy", slug="legacy")
            db.add(company)
            db.flush()
            employee = Employee(
                company_id=company.id,
                name="Legacy",
                slug="legacy",
                workspace_path=str(tmp_path / "unused-workspace"),
                memory_namespace="legacy-migration-test",
            )
            db.add(employee)
            db.flush()
            employee_id = employee.id
            db.commit()
        old = [
            (
                {"source": "app.work.readiness", "policy": {}},
                "pending",
                "runtime",
                "app.work.readiness",
            ),
            (
                {"source": "app.work.readiness", "requested_by": "app.services.recruitment"},
                "running",
                "runtime",
                "app.services.recruitment",
            ),
            ({"source": "position_event"}, "partial", "lifecycle", "position_event"),
            ({}, "done", "lifecycle", "legacy"),
            ([], "pending", "lifecycle", "legacy"),
        ]
        with engine.begin() as connection:
            jobs = Table("provisioning_jobs", MetaData(), autoload_with=connection)
            for metadata, status, _, _ in old:
                connection.execute(
                    jobs.insert().values(
                        employee_id=employee_id,
                        kind="onboarding",
                        status=status,
                        total_steps=3,
                        done_steps=0,
                        reason="historical reason",
                        metadata_json=metadata,
                        created_at=utcnow(),
                        updated_at=utcnow(),
                    )
                )
        migrate("head")
        script = ScriptDirectory.from_config(config)
        assert len(script.get_heads()) == 1
        assert script.get_revision("46c2d8109a7b").down_revision == "325887b7109a"
        assert "46c2d8109a7b" in {revision.revision for revision in script.walk_revisions()}

        def verify():
            with engine.connect() as connection:
                jobs = Table("provisioning_jobs", MetaData(), autoload_with=connection)
                rows = connection.execute(select(jobs).order_by(jobs.c.id)).mappings().all()
                assert len(rows) == len(old)
                for row, (metadata, status, owner, source) in zip(rows, old, strict=True):
                    assert row["metadata_json"] == metadata
                    assert (row["status"], row["owner"], row["source"]) == (status, owner, source)
                    assert row["reason"] == "historical reason"
                    assert row["claim_token"] is None and row["claimed_at"] is None
                assert "result_json" in {
                    column["name"]
                    for column in inspect(connection).get_columns("provisioning_steps")
                }
                config.attributes["connection"] = connection
                command.check(config)

        verify()
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.downgrade(config, "325887b7109a")
            assert "owner" not in {
                column["name"] for column in inspect(connection).get_columns("provisioning_jobs")
            }
        migrate("head")
        verify()
        assert not (tmp_path / "unused-workspace").exists(), "migration must not execute jobs"
    finally:
        engine.dispose()
