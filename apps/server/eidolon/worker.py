"""The worker process: keeps every registered consumer caught up with the fact log.

    start   replay_all — the only recovery mechanism (v2-ADR-2)
    loop    drain every consumer → wait for NOTIFY (or the poll timeout) → repeat
    stop    SIGTERM / SIGINT / ``stop()``: finish the batch in flight, commit its
            cursor, return; the process exits 0

A consumer whose batch raises is logged and left where its cursor is; the other
consumers keep going and the failed one is retried on the next wake-up. The
worker never skips a fact and never moves a cursor past a failure.
"""

import os
import select
import signal
import threading
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy.orm import sessionmaker

from eidolon.kernel.consumers import BatchResult, Consumer, Registry, process_batch
from eidolon.kernel.notify import Listener
from eidolon.kernel.store import FactStore
from eidolon.logging import get_logger

log = get_logger("eidolon.worker")


class Job(Protocol):
    """Work that is not a fact consumer but runs each cycle after the drain
    (R3-d: executing task runs and reviews). ``tick`` returns how much it did."""

    name: str

    def tick(self) -> int: ...


@dataclass
class WorkerStats:
    cycles: int = 0
    batches: int = 0
    handled: int = 0
    jobs: int = 0
    failures: dict[str, int] = field(default_factory=dict)


class Worker:
    def __init__(
        self,
        registry: Registry,
        session_factory: sessionmaker,
        listener: Listener,
        *,
        poll_interval: float = 5.0,
        batch_limit: int = 200,
        store: FactStore | None = None,
        jobs: Sequence[Job] = (),
    ):
        self.registry = registry
        self.jobs = list(jobs)
        self.session_factory = session_factory
        self.listener = listener
        self.poll_interval = poll_interval
        self.batch_limit = batch_limit
        self.store = store or FactStore()
        self.stats = WorkerStats()
        self._stop = threading.Event()
        # Self-pipe: ``stop()`` (from a signal handler or another thread) writes a
        # byte so a worker blocked in ``select`` wakes immediately.
        self._wake_r, self._wake_w = os.pipe()
        os.set_blocking(self._wake_r, False)
        os.set_blocking(self._wake_w, False)

    @property
    def consumers(self) -> list[Consumer]:
        return list(self.registry.consumers.values())

    # -- lifecycle ---------------------------------------------------------------

    def start(self) -> dict[str, int]:
        """Catch every consumer up from its cursor before serving (replay is the only
        recovery mechanism, v2-ADR-2). Returns handled counts per consumer. A consumer
        that fails here is logged and retried on the next cycle, like in the loop."""
        t0 = time.monotonic()
        handled = {c.name: sum(r.handled for r in self._drain_consumer(c)) for c in self.consumers}
        log.info(
            "worker.started",
            extra={
                "consumers": [c.name for c in self.consumers],
                "jobs": [j.name for j in self.jobs],
                "replayed": handled,
                "ms": _ms(t0),
                "poll_interval": self.poll_interval,
                "batch_limit": self.batch_limit,
            },
        )
        return handled

    def run(self) -> None:
        """``start`` then loop until ``stop``. Blocks the calling thread."""
        self.start()
        try:
            while not self._stop.is_set():
                self.drain()
                if self._stop.is_set():
                    break
                if self.run_jobs():
                    self.drain()  # jobs wrote facts; let the projections catch up now
                if self._stop.is_set():
                    break
                self.wait()
        finally:
            log.info("worker.stopped", extra={"stats": vars(self.stats)})

    def stop(self) -> None:
        """Ask the loop to finish its current batch and return. Safe from any thread
        and from a signal handler."""
        self._stop.set()
        try:
            os.write(self._wake_w, b"x")
        except BlockingIOError:
            pass  # pipe already has a pending wake-up

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    def install_signal_handlers(self, signals: Iterable[int] = (signal.SIGTERM, signal.SIGINT)):
        """Main thread only (a Python restriction)."""

        def _handler(signum, _frame):
            log.info("worker.stopping", extra={"signal": signal.Signals(signum).name})
            self.stop()

        for sig in signals:
            signal.signal(sig, _handler)

    # -- one cycle ---------------------------------------------------------------

    def drain(self) -> list[BatchResult]:
        """Run every consumer until it has nothing left (or a stop was requested)."""
        self.stats.cycles += 1
        results: list[BatchResult] = []
        for consumer in self.consumers:
            if self._stop.is_set():
                break
            results.extend(self._drain_consumer(consumer))
        return results

    def _drain_consumer(self, consumer: Consumer) -> list[BatchResult]:
        results: list[BatchResult] = []
        while True:
            t0 = time.monotonic()
            db = self.session_factory()
            try:
                result = process_batch(db, consumer, store=self.store, limit=self.batch_limit)
                db.commit()
            except Exception:
                db.rollback()
                self.stats.failures[consumer.name] = self.stats.failures.get(consumer.name, 0) + 1
                log.exception(
                    "worker.batch.failed",
                    extra={"consumer": consumer.name, "ms": _ms(t0)},
                )
                return results
            finally:
                db.close()
            self.stats.batches += 1
            self.stats.handled += result.handled
            results.append(result)
            log.info(
                "worker.batch",
                extra={
                    "consumer": result.consumer,
                    "scanned": result.scanned,
                    "handled": result.handled,
                    "cursor": result.cursor,
                    "ms": _ms(t0),
                },
            )
            if result.scanned < self.batch_limit or self._stop.is_set():
                return results

    def run_jobs(self) -> int:
        """Each job once; a failing job is logged and retried next cycle like a consumer."""
        total = 0
        for job in self.jobs:
            if self._stop.is_set():
                break
            t0 = time.monotonic()
            try:
                did = job.tick()
            except Exception:
                self.stats.failures[job.name] = self.stats.failures.get(job.name, 0) + 1
                log.exception("worker.job.failed", extra={"job": job.name, "ms": _ms(t0)})
                continue
            if did:
                self.stats.jobs += did
                log.info("worker.job", extra={"job": job.name, "did": did, "ms": _ms(t0)})
            total += did
        return total

    def wait(self) -> int:
        """Block until a NOTIFY arrives, ``stop`` is called, or ``poll_interval``
        elapses. Returns the number of notifications consumed."""
        ready, _, _ = select.select(
            [self.listener.fileno(), self._wake_r], [], [], self.poll_interval
        )
        if self._wake_r in ready:
            try:
                os.read(self._wake_r, 4096)
            except BlockingIOError:
                pass
        if self.listener.fileno() in ready:
            return self.listener.pending()
        return 0

    def close(self) -> None:
        os.close(self._wake_r)
        os.close(self._wake_w)


def _ms(t0: float) -> int:
    return round((time.monotonic() - t0) * 1000)
