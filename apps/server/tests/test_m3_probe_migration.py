"""U4 schema preserves legacy environments and enforces paid-request ownership."""

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import _alembic_config
from app.models.organization import Company, Employee
from app.models.runtime import RuntimeInstance, RuntimeModelProbe
from app.runtimes.probe import runtime_facts


def test_v48_v49_roundtrip_preserves_environment_and_enforces_unique_reservations(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'probe-migration.db'}")
    config = _alembic_config()

    def migrate(revision, *, downgrade=False):
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            (command.downgrade if downgrade else command.upgrade)(config, revision)

    try:
        migrate("47d50a3618ef")
        with Session(engine) as db:
            company = Company(name="Legacy probes", slug="legacy-probes")
            db.add(company)
            db.flush()
            employees = [
                Employee(
                    company_id=company.id,
                    name=f"Probe {n}",
                    slug=f"probe-{n}",
                    workspace_path=str(tmp_path / f"unused-{n}"),
                    memory_namespace=f"probe-{n}",
                )
                for n in range(2)
            ]
            db.add_all(employees)
            db.flush()
            rows = [
                RuntimeInstance(
                    employee_id=e.id,
                    runtime_type="hermes",
                    deployment_mode="docker",
                    internal_host=f"legacy-{e.id}",
                    internal_port=8642,
                    metadata_json={"legacy": True},
                    status="running",
                    health_status="unknown",
                )
                for e in employees
            ]
            db.add_all(rows)
            db.commit()
            first_id, second_id = [r.id for r in rows]
            company_id, employee_id = company.id, employees[0].id

        def verify():
            with Session(engine) as db:
                rows = list(db.scalars(select(RuntimeInstance)))
                assert len(rows) == 2
                assert all(r.metadata_json == {"legacy": True} for r in rows)
                assert all(r.status == "running" and r.health_status == "unknown" for r in rows)
                assert all(f["state"] == "unknown" for r in rows for f in runtime_facts(r).values())
            with engine.connect() as connection:
                config.attributes["connection"] = connection
                command.check(config)

        migrate("head")
        verify()
        with Session(engine) as db:
            first, second = db.get(RuntimeInstance, first_id), db.get(RuntimeInstance, second_id)
            first.internal_host, first.internal_port = "127.0.0.1", 26900
            db.commit()
            second.internal_host, second.internal_port = "127.0.0.1", 26900
            with pytest.raises(IntegrityError):
                db.commit()
            db.rollback()
            for key in ("first", "second"):
                db.add(
                    RuntimeModelProbe(
                        company_id=company_id,
                        employee_id=employee_id,
                        runtime_instance_id=first_id,
                        idempotency_key=key,
                        fingerprint="fixture",
                        state="running",
                        request_json={},
                        result_json={},
                    )
                )
                if key == "first":
                    db.commit()
                else:
                    with pytest.raises(IntegrityError):
                        db.commit()
                    db.rollback()
            attempt = db.scalar(select(RuntimeModelProbe))
            attempt.state = "passed"
            db.commit()
            db.add(
                RuntimeModelProbe(
                    company_id=company_id,
                    employee_id=employee_id,
                    runtime_instance_id=first_id,
                    idempotency_key="first",
                    fingerprint="fixture",
                    state="passed",
                    request_json={},
                    result_json={},
                )
            )
            with pytest.raises(IntegrityError):
                db.commit()
            db.rollback()

        migrate("47d50a3618ef", downgrade=True)
        with engine.connect() as connection:
            assert "runtime_model_probes" not in inspect(connection).get_table_names()
            assert "uq_runtime_loopback_port" not in {
                i["name"] for i in inspect(connection).get_indexes("runtime_instances")
            }
        migrate("head")
        verify()
        script = ScriptDirectory.from_config(config)
        assert len(script.get_heads()) == 1
        assert script.get_revision("49f90481d32a").down_revision == "48e38b2049c1"
        assert script.get_revision("48e38b2049c1").down_revision == "47d50a3618ef"
        assert not (tmp_path / "unused-0").exists()
    finally:
        engine.dispose()
