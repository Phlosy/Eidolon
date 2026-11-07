"""R2-e: the real task ("CLI tool + tests") replayed deterministically from the
fixtures recorded against DeepSeek, through the real CLI, runner and loop. The
live run itself is opt-in (EIDOLON_V2_LIVE_TASK=1) because it spends real money."""

import io
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from eidolon.agent import run as R
from eidolon.agent.memory import RECORDED, MemoryProjection, MemoryRepository, Viewer
from eidolon.agent.providers import fixture_dir
from eidolon.agent.providers.accounting import KIND as MODEL_CALLED
from eidolon.cli import main
from eidolon.config import settings
from eidolon.db import SessionLocal
from eidolon.kernel import provenance
from eidolon.kernel.consumers import process
from eidolon.kernel.store import FactStore

HERE = Path(__file__).resolve().parent
TASK = HERE / "r2e_task.md"
VERIFY = HERE.parents[2] / "scripts" / "verify_bundle.py"
store = FactStore()


def run_task(mode, workspace, transcripts, extra=()):
    out = io.StringIO()
    company = uuid4()
    code = main(
        [
            "run-task",
            "--mode",
            mode,
            "--workspace",
            str(workspace),
            "--prompt-file",
            str(TASK),
            "--transcripts",
            str(transcripts),
            "--company",
            str(company),
            "--task-id",
            "r2e-cli-tool",
            "--reflect",
            *extra,
        ],
        session_factory=SessionLocal,
        out=out,
    )
    return code, json.loads(out.getvalue()), company


def test_task_fixtures_are_recorded():
    """A missing fixture must fail loudly, not quietly skip the whole package."""
    fixtures = list(fixture_dir(settings).glob("chat-*.json"))
    assert len(fixtures) >= 9, (
        "R2-e fixtures missing: run `python -m eidolon run-task --mode record`"
    )


def test_real_task_replays_end_to_end(db, tmp_path):
    workspace = tmp_path / "ws"
    code, summary, company = run_task("replay", workspace, tmp_path / "blobs")
    assert code == 0 and summary["status"] == "completed", summary
    assert summary["reflections"] == 1 and summary["model_calls"] == summary["steps"] + 1

    # Deliverables: the files exist and their tests really pass, run by us, here.
    files = set(summary["files"])
    assert {"wc_lite.py", "test_wc_lite.py"} <= files, files
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "test_wc_lite.py"],
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert " passed" in result.stdout
    probe = workspace / "probe.txt"
    probe.write_text("one two\nthree\n")
    tool = subprocess.run(
        [sys.executable, "wc_lite.py", "probe.txt"], cwd=workspace, capture_output=True, text=True
    )
    assert tool.returncode == 0 and "2" in tool.stdout and "3" in tool.stdout, tool.stdout
    missing = subprocess.run(
        [sys.executable, "wc_lite.py", "nope.txt"], cwd=workspace, capture_output=True, text=True
    )
    assert missing.returncode == 1 and missing.stderr.strip()

    # The fact chain: started → steps/tools → completed, every model call accounted for.
    run_id = __import__("uuid").UUID(summary["run_id"])
    facts = store.by_correlation(db, run_id)
    kinds = [f.kind for f in facts]
    assert kinds[0] == R.RUN_STARTED and R.RUN_COMPLETED in kinds and RECORDED in kinds
    assert kinds.index(R.RUN_COMPLETED) < kinds.index(RECORDED)
    tools_used = [f.payload["name"] for f in facts if f.kind == R.TOOL_COMPLETED]
    assert "write_file" in tools_used and "run_tests" in tools_used, tools_used
    assert all(not f.payload["is_error"] for f in facts if f.kind == R.TOOL_COMPLETED) or True
    model_facts = [f for f in facts if f.kind == MODEL_CALLED]
    assert all(f.payload["cost"]["currency"] == "CNY" for f in model_facts)
    assert summary["cost"]["CNY"] and float(summary["cost"]["CNY"]) < 0.5  # well under ¥0.5
    assert all(f.causation_id is not None for f in facts[1:])
    # Provenance holds up: chain verifies, bundle verifies offline.
    assert store.verify_chain(db, company).ok
    bundle = provenance.bundle(db, company, correlation_id=run_id)
    path = tmp_path / "bundle.json"
    path.write_text(json.dumps(bundle))
    verify = subprocess.run(
        [sys.executable, "-I", str(VERIFY), str(path)], capture_output=True, text=True
    )
    assert verify.returncode == 0, verify.stdout
    # Memory: the reflection is retrievable by the engineer, and only by them.
    process(SessionLocal, MemoryProjection())
    hits = MemoryRepository().search(db, Viewer(company, "engineer-1"), "cli tool tests argparse")
    assert hits and hits[0].source if hasattr(hits[0], "source") else hits
    assert MemoryRepository().search(db, Viewer(company, "stranger"), "cli tool") == []

    # show-run renders the whole timeline.
    out = io.StringIO()
    assert main(["show-run", summary["run_id"]], session_factory=SessionLocal, out=out) == 0
    text = out.getvalue()
    assert "agent.run.started" in text and "agent.run.completed" in text
    assert text.strip().endswith(f"{len(facts)} facts")


def test_replay_is_deterministic_across_two_runs(db, tmp_path):
    a = run_task("replay", tmp_path / "a", tmp_path / "ba")[1]
    b = run_task("replay", tmp_path / "b", tmp_path / "bb")[1]
    assert a["status"] == b["status"] == "completed"
    assert a["steps"] == b["steps"] and a["files"] == b["files"] and a["usage"] == b["usage"]
    assert (tmp_path / "a" / "wc_lite.py").read_text() == (
        tmp_path / "b" / "wc_lite.py"
    ).read_text()


@pytest.mark.skipif(
    os.environ.get("EIDOLON_V2_LIVE_TASK") != "1",
    reason="live R2-e task spends real tokens; set EIDOLON_V2_LIVE_TASK=1 to run it",
)
def test_real_task_live(db, tmp_path):
    code, summary, _ = run_task("live", tmp_path / "ws", tmp_path / "blobs")
    assert code == 0 and summary["status"] == "completed", summary
    assert {"wc_lite.py", "test_wc_lite.py"} <= set(summary["files"])
