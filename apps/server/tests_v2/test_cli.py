"""``python -m eidolon``: migrate / verify-chain / rebuild / cursors, in-process and
as a real subprocess."""

import io
import os
import subprocess
import sys
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import text
from support import KindCounts, counts, registry_with, scratch_tables, seed, store

from eidolon import logging as elog
from eidolon.cli import EXIT_FAIL, EXIT_OK, EXIT_USAGE, load_registry, main
from eidolon.db import SessionLocal
from eidolon.kernel.consumers import Registry, process

SERVER_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture()
def scratch(database):
    """Only for tests that use the KindCounts projection: test_migrate runs
    `alembic check`, which must not see extra tables. Request it *before* ``db``
    so the session is closed before the tables are dropped."""
    with scratch_tables(database):
        yield


def run(argv, registry=None):
    out = io.StringIO()
    code = main(argv, registry=registry or Registry(), session_factory=SessionLocal, out=out)
    return code, out.getvalue()


def _tamper(db, fact):
    db.execute(text("ALTER TABLE facts DISABLE TRIGGER facts_no_update_delete"))
    db.execute(
        text("UPDATE facts SET payload = '{\"forged\": true}'::jsonb WHERE id = :id"),
        {"id": fact.id},
    )
    db.execute(text("ALTER TABLE facts ENABLE TRIGGER facts_no_update_delete"))
    db.commit()


def test_verify_chain_passes_then_fails_after_tampering(db):
    a, b = uuid4(), uuid4()
    seed(db, a, 3, prefix="a")
    facts_b = seed(db, b, 2, prefix="b")

    code, out = run(["verify-chain"])
    assert code == EXIT_OK
    assert f"{a} ok checked=3" in out and f"{b} ok checked=2" in out
    assert "2 companies, 0 broken" in out

    _tamper(db, facts_b[0])
    code, out = run(["verify-chain"])
    assert code == EXIT_FAIL
    assert f"{a} ok checked=3" in out
    assert f"{b} BROKEN at chain_seq=1: hash mismatch" in out
    assert "2 companies, 1 broken" in out

    # --company narrows to one chain.
    assert run(["verify-chain", "--company", str(a)])[0] == EXIT_OK
    code, out = run(["verify-chain", "--company", str(b)])
    assert code == EXIT_FAIL and "1 companies, 1 broken" in out


def test_verify_chain_on_empty_log():
    code, out = run(["verify-chain"])
    assert code == EXIT_OK and "no facts yet" in out


def test_rebuild_via_cli(scratch, db):
    company = uuid4()
    seed(db, company, 4)
    seed(db, company, 2, kind="agent.run.completed")
    projection = KindCounts()
    process(SessionLocal, projection)
    db.execute(text("UPDATE kind_counts SET n = 999"))
    db.commit()

    code, out = run(["rebuild", "kind_counts"], registry_with(projection))
    assert code == EXIT_OK and "rebuilt kind_counts: 6 facts handled" in out
    assert counts(db) == {"agent.run.completed": 2, "work.task.created": 4}

    code, _ = run(["rebuild", "nope"], registry_with(projection))
    assert code == EXIT_USAGE


def test_cursors_lists_lag(scratch, db):
    company = uuid4()
    seed(db, company, 5)
    projection = KindCounts()
    process(SessionLocal, projection, limit=2, until_drained=False)  # cursor at 2 of 5
    head = store.last_seq(db)

    code, out = run(["cursors"], registry_with(projection))
    assert code == EXIT_OK
    assert f"head seq: {head}" in out
    line = next(ln for ln in out.splitlines() if ln.startswith("kind_counts"))
    cols = line.split()
    assert cols[1] == str(head - 3) and cols[2] == "3"


def test_cursors_marks_unregistered_consumers(scratch, db):
    seed(db, uuid4(), 1)
    projection = KindCounts()
    process(SessionLocal, projection)
    code, out = run(["cursors"])  # empty registry, but a cursor row exists
    assert code == EXIT_OK and "kind_counts" in out and "(not registered)" in out


def test_migrate_upgrades_to_head(database):
    from conftest import alembic_config

    command.downgrade(alembic_config(), "base")
    code, out = run(["migrate"])
    assert code == EXIT_OK and out.startswith("migrated to ")
    command.check(alembic_config())


def test_load_registry_accepts_callable_and_instance():
    assert isinstance(load_registry("eidolon.registry:default_registry"), Registry)
    assert isinstance(load_registry("support:registry_with"), Registry)
    with pytest.raises(SystemExit):
        load_registry("eidolon.registry")
    with pytest.raises(SystemExit):
        load_registry("eidolon.cli:EXIT_OK")


def test_real_entry_point_verify_chain(db):
    seed(db, uuid4(), 2)
    result = subprocess.run(
        [sys.executable, "-m", "eidolon", "verify-chain"],
        cwd=SERVER_ROOT,
        env=dict(os.environ),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "1 companies, 0 broken" in result.stdout


def test_json_logs_redact_secrets():
    import logging

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(elog.JsonFormatter())
    logger = logging.getLogger("eidolon.test.redact")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    elog.register_secret("hunter2-super-secret")
    try:
        logger.info(
            "provider call",
            extra={
                "api_key": "sk-abcdefghijklmnop1234",
                "url": "postgresql://u:hunter2-super-secret@h/db",
                "env": "OPENAI_API_KEY=abcd1234efgh",
            },
        )
    finally:
        logger.removeHandler(handler)
    import json

    line = json.loads(stream.getvalue())
    assert line["msg"] == "provider call" and line["level"] == "INFO"
    assert "hunter2" not in stream.getvalue() and "sk-abcdefghijklmnop" not in stream.getvalue()
    assert line["api_key"].endswith("1234") and line["env"].startswith("OPENAI_API_KEY=")
    assert "abcd1234efgh" not in line["env"]
