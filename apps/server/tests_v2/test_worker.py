"""The worker: replay on start, NOTIFY wake-up, graceful stop, failure isolation,
and the real process exiting 0 on SIGTERM."""

import json
import os
import signal
import subprocess
import sys
import threading
import time
from uuid import uuid4

import pytest
from support import KindCounts, counts, registry_with, scratch_tables, seed, store

from eidolon.db import SessionLocal
from eidolon.kernel import consumers
from eidolon.kernel.consumers import function_consumer
from eidolon.kernel.notify import listening
from eidolon.worker import Worker

SERVER_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(autouse=True)
def _scratch(database):
    with scratch_tables(database):
        yield


@pytest.fixture()
def listener():
    with listening() as lst:
        yield lst


def wait_until(predicate, timeout=3.0, step=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return predicate()


def run_in_thread(worker):
    thread = threading.Thread(target=worker.run, daemon=True)
    thread.start()
    return thread


def test_start_replays_backlog(db, listener):
    company = uuid4()
    seed(db, company, 4)
    projection = KindCounts()
    worker = Worker(registry_with(projection), SessionLocal, listener)
    try:
        assert worker.start() == {"kind_counts": 4}
        assert counts(db) == {"work.task.created": 4}
        assert consumers.cursor(db, "kind_counts") == store.last_seq(db)
    finally:
        worker.close()


def test_notify_wakes_worker_and_drains_within_a_second(db, listener):
    projection = KindCounts()
    # Long poll interval: if the worker reacts, it is the NOTIFY, not the timeout.
    worker = Worker(registry_with(projection), SessionLocal, listener, poll_interval=30)
    thread = run_in_thread(worker)
    try:
        assert wait_until(lambda: worker.stats.cycles >= 1)
        company = uuid4()
        t0 = time.monotonic()
        seed(db, company, 3)  # another thread appends and commits
        assert wait_until(lambda: counts(db) == {"work.task.created": 3}, timeout=1.0)
        assert time.monotonic() - t0 < 1.0
        assert consumers.cursor(db, "kind_counts") == store.last_seq(db)
    finally:
        worker.stop()
        thread.join(timeout=3)
        worker.close()
    assert not thread.is_alive()


def test_stop_finishes_current_batch_and_commits_cursor(db, listener):
    company = uuid4()
    seed(db, company, 5)
    worker_ref = {}

    def stop_mid_batch(fact):
        worker_ref["w"].stop()  # requested while a batch is in flight

    projection = KindCounts(on_handle=stop_mid_batch)
    worker = Worker(registry_with(projection), SessionLocal, listener, poll_interval=30)
    worker_ref["w"] = worker
    thread = run_in_thread(worker)
    thread.join(timeout=3)
    worker.close()
    assert not thread.is_alive()
    # The batch that was running finished whole and its cursor was committed.
    assert counts(db) == {"work.task.created": 5}
    assert consumers.cursor(db, "kind_counts") == store.last_seq(db)
    assert projection.calls == 5


def test_stop_interrupts_wait_immediately(listener):
    worker = Worker(registry_with(), SessionLocal, listener, poll_interval=30)
    thread = run_in_thread(worker)
    assert wait_until(lambda: worker.stats.cycles >= 1)
    t0 = time.monotonic()
    worker.stop()
    thread.join(timeout=3)
    worker.close()
    assert not thread.is_alive() and time.monotonic() - t0 < 1.0


def test_failing_consumer_does_not_block_the_others(db, listener):
    company = uuid4()
    facts = seed(db, company, 3)
    broken = KindCounts(fail_on_seq=facts[1].seq)
    seen = []
    healthy = function_consumer("healthy", None, lambda _db, f: seen.append(f.seq))
    worker = Worker(registry_with(broken, healthy), SessionLocal, listener, poll_interval=0.1)
    thread = run_in_thread(worker)
    try:
        assert wait_until(lambda: worker.stats.cycles >= 2)
        assert len(seen) == 3  # the healthy consumer caught up on the first pass
        assert consumers.cursor(db, "healthy") == store.last_seq(db)
        assert consumers.cursor(db, "kind_counts") == 0  # never moved past the failure
        assert counts(db) == {}
        assert worker.stats.failures["kind_counts"] >= 2  # retried each cycle
        # Fix the handler; the next cycle recovers without any manual step.
        broken.fail_on_seq = None
        assert wait_until(lambda: counts(db) == {"work.task.created": 3})
        assert consumers.cursor(db, "kind_counts") == store.last_seq(db)
    finally:
        worker.stop()
        thread.join(timeout=3)
        worker.close()


def test_batch_limit_splits_drain_into_logged_batches(db, listener, caplog):
    company = uuid4()
    seed(db, company, 7)
    projection = KindCounts()
    worker = Worker(registry_with(projection), SessionLocal, listener, batch_limit=3)
    try:
        with caplog.at_level("INFO", logger="eidolon.worker"):
            results = worker.drain()
    finally:
        worker.close()
    assert [r.scanned for r in results] == [3, 3, 1]
    batch_logs = [r for r in caplog.records if r.getMessage() == "worker.batch"]
    assert [(r.consumer, r.scanned, r.handled) for r in batch_logs] == [
        ("kind_counts", 3, 3),
        ("kind_counts", 3, 3),
        ("kind_counts", 1, 1),
    ]
    assert all(hasattr(r, "cursor") and hasattr(r, "ms") for r in batch_logs)


def test_real_process_exits_zero_on_sigterm_with_json_logs():
    proc = subprocess.Popen(
        [sys.executable, "-m", "eidolon", "worker", "--poll-interval", "0.2"],
        cwd=SERVER_ROOT,
        env=dict(os.environ),  # carries the *_test database URL set by conftest
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        # The default registry has real consumers now; their startup batches are logged
        # before worker.started. Read until it appears.
        for _ in range(20):
            first = json.loads(proc.stderr.readline())
            if first["msg"] == "worker.started":
                break
            assert first["msg"] == "worker.batch", first
        assert first["msg"] == "worker.started" and first["logger"] == "eidolon.worker"
        assert first["consumers"] == [
            "identity",
            "work_items",
            "run_launcher",
            "memory_entries",
            "memory_reflector",
            "cost_meter",
            "money_ledger",
        ]
        assert first["level"] == "INFO" and "ts" in first
        proc.send_signal(signal.SIGTERM)
        code = proc.wait(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert code == 0
    rest = [json.loads(line) for line in proc.stderr.read().splitlines() if line.strip()]
    msgs = [r["msg"] for r in rest if r["msg"] != "worker.batch"]  # in-flight cycle may log
    assert msgs == ["worker.stopping", "worker.stopped"]
    assert next(r for r in rest if r["msg"] == "worker.stopping")["signal"] == "SIGTERM"
