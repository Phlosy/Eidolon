"""The test harness must isolate committed writes, files and app lifetimes."""

import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.economy.policy import economic_policy
from app.lifecycle.provisioners.registry import get_registry
from app.models.organization import Company
from app.services import auth
from app.workflow.orchestrator import orchestrator

_LEARNING_ENABLED = settings.autonomous_learning_enabled
_MARKET_FEE = settings.economy_market_fee_bps


@pytest.mark.parametrize("case", ["first", "second"])
def test_committed_connections_and_workspace_are_test_local(db, case):
    marker = Path(settings.workspace_root) / "isolation-sentinel.txt"
    assert not marker.exists(), "a previous test's workspace survived"
    assert db.scalar(select(Company).where(Company.slug == "isolation-sentinel")) is None

    # A separate connection commits: an outer rollback on the db fixture is insufficient.
    with SessionLocal() as writer:
        writer.add(Company(name=case, slug="isolation-sentinel", description=""))
        writer.commit()
    assert db.scalar(select(Company).where(Company.slug == "isolation-sentinel")) is not None
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(case)


@pytest.mark.parametrize("case", ["first", "second"])
def test_process_state_is_test_local(case):
    assert settings.autonomous_learning_enabled == _LEARNING_ENABLED
    assert economic_policy().market_fee_bps == _MARKET_FEE
    assert not orchestrator._escalated
    assert "isolation:probe" not in get_registry()._provisioners
    auth.enforce_rate_limit("isolation-probe", limit=1)

    settings.autonomous_learning_enabled = not _LEARNING_ENABLED
    settings.economy_market_fee_bps = (_MARKET_FEE + 1) % 10_001
    economic_policy.cache_clear()
    economic_policy()
    orchestrator._escalated.add((123456, case))
    get_registry().register(SimpleNamespace(key="isolation:probe"))


def test_failed_test_finishes_background_work_before_restoring_state(tmp_path):
    """An intentional failing child test must not contaminate the next test."""
    source = Path(__file__).parent
    shutil.copy2(source / "conftest.py", tmp_path / "conftest.py")
    shutil.copy2(source / "isolation.py", tmp_path / "isolation.py")
    (tmp_path / "test_boundary.py").write_text(
        """import asyncio
from pathlib import Path
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.organization import Company
from app.workflow.orchestrator import orchestrator
from sqlalchemy import select

old = {}

def test_intentional_failure(client):
    async def pending():
        try:
            await asyncio.Event().wait()
        finally:
            with SessionLocal() as db:
                db.add(Company(name="cleanup", slug="cleanup-write", description=""))
                db.commit()
            Path(settings.workspace_root, "cleanup-write").write_text("finished")
            old["cleaned"] = True
    async def start():
        old["loop"] = asyncio.get_running_loop()
        old["task"] = asyncio.create_task(pending())
        await asyncio.sleep(0)
    client.portal.call(start)
    assert False, "intentional failure for teardown verification"

def test_next_case_is_clean(client, db):
    assert old.get("cleaned"), "background task was not finalized"
    assert old["task"].done()
    assert old["loop"].is_closed(), "prior application loop is still alive"
    assert db.scalar(select(Company).where(Company.slug == "cleanup-write")) is None
    assert not Path(settings.workspace_root, "cleanup-write").exists()
    assert not orchestrator._escalated
"""
    )
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--tb=short", str(tmp_path)],
        cwd=tmp_path,
        env={**os.environ, "PYTEST_ADDOPTS": ""},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "1 failed, 1 passed" in result.stdout, result.stdout + result.stderr
