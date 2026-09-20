"""Graceful event shutdown keeps accepted messages, ordering, and retries."""

from __future__ import annotations

import asyncio
import threading
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.events import engine as engine_module
from app.events.bus import EventBus
from app.events.engine import EventEngine


@pytest.mark.parametrize("from_thread", [False, True])
async def test_stop_drains_publish_callbacks_without_an_intervening_yield(from_thread):
    event_bus = EventBus()
    event_bus.attach_loop()
    engine = EventEngine(event_bus)
    seen = []
    engine.register("recorder", ("shutdown.event",), lambda message: seen.append(message["data"]))
    await engine.start()
    try:
        if from_thread:
            # Join without yielding the event loop: call_soon_threadsafe deliveries
            # are accepted, but cannot reach the queue before stop begins.
            publisher = threading.Thread(
                target=event_bus.publish, args=("shutdown.event", {"n": 1})
            )
            publisher.start()
            publisher.join(timeout=5)
            assert not publisher.is_alive()
        else:
            event_bus.publish("shutdown.event", {"n": 1})
        await engine.stop()
    finally:
        await engine.stop()
    assert seen == [{"n": 1}]
    assert engine.stats()["partitions_active"] == 0


async def test_stop_drains_partition_order_and_retries_while_other_keys_progress():
    event_bus = EventBus()
    event_bus.attach_loop()
    engine = EventEngine(event_bus, retry_backoff=(0.01,))
    seen = {"a": [], "b": []}
    attempts = 0
    b_finished = asyncio.Event()

    async def handler(message):
        nonlocal attempts
        key, seq = message["data"]["key"], message["data"]["seq"]
        if key == "a" and seq == 0:
            attempts += 1
            if attempts == 1:
                raise RuntimeError("transient failure")
            await b_finished.wait()
        seen[key].append(seq)
        if key == "b" and seq == 1:
            b_finished.set()

    engine.register("recorder", ("shutdown.event",), handler, key_of=lambda m: m["data"]["key"])
    await engine.start()
    try:
        for key, seq in [("a", 0), ("a", 1), ("b", 0), ("a", 2), ("b", 1)]:
            event_bus.publish("shutdown.event", {"key": key, "seq": seq})
        await asyncio.wait_for(engine.stop(), timeout=2)
    finally:
        await engine.stop()
    assert seen == {"a": [0, 1, 2], "b": [0, 1]}
    assert attempts == 2
    assert engine.stats()["retried"] == 1
    assert engine.stats()["processed"] == 5


async def test_stop_completes_inflight_handler_before_queued_same_key_event():
    event_bus = EventBus()
    event_bus.attach_loop()
    engine = EventEngine(event_bus)
    started, release = asyncio.Event(), asyncio.Event()
    seen = []

    async def handler(message):
        seq = message["data"]["seq"]
        if seq == 0:
            started.set()
            await release.wait()
        seen.append(seq)

    engine.register("recorder", ("shutdown.event",), handler)
    await engine.start()
    try:
        event_bus.publish("shutdown.event", {"seq": 0})
        await asyncio.wait_for(started.wait(), timeout=2)
        event_bus.publish("shutdown.event", {"seq": 1})
        asyncio.get_running_loop().call_soon(release.set)
        await engine.stop()
    finally:
        release.set()
        await engine.stop()
    assert seen == [0, 1]


async def test_stop_and_restart_are_idempotent_with_concurrent_callers():
    event_bus = EventBus()
    event_bus.attach_loop()
    engine = EventEngine(event_bus)
    seen = []
    engine.register("recorder", ("shutdown.event",), lambda m: seen.append(m["data"]["seq"]))
    await engine.stop()
    try:
        for seq in range(2):
            await asyncio.gather(engine.start(), engine.start())
            event_bus.publish("shutdown.event", {"seq": seq})
            await asyncio.gather(engine.stop(), engine.stop())
            await engine.stop()
    finally:
        await engine.stop()
    assert seen == [0, 1]
    assert engine.stats()["processed"] == 2
    assert not event_bus._subscribers


@pytest.mark.parametrize("retrying", [False, True])
async def test_stop_timeout_cancels_partition_chains_and_can_restart(retrying, caplog):
    event_bus = EventBus()
    event_bus.attach_loop()
    engine = EventEngine(event_bus, shutdown_timeout=0.03, retry_backoff=(5,))
    started, cancelled = asyncio.Event(), asyncio.Event()
    seen = []
    attempts = []

    async def handler(message):
        seq = message["data"]["seq"]
        attempts.append(seq)
        if seq == 0:
            started.set()
            if retrying:
                raise RuntimeError("retry pending at shutdown")
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        seen.append(seq)

    engine.register("recorder", ("shutdown.event",), handler)
    await engine.start()
    try:
        for seq in range(3):
            event_bus.publish("shutdown.event", {"seq": seq})
        await asyncio.wait_for(started.wait(), timeout=2)
        await asyncio.wait_for(engine.stop(), timeout=1)
        assert "停机排空超时" in caplog.text
        assert attempts == [0], "Cancelling a predecessor must not start queued successors"
        assert seen == []
        assert retrying or cancelled.is_set()
        assert engine.stats()["partitions_active"] == 0
        assert engine._task is None
        assert engine._queue is None
        assert not event_bus._subscribers

        await engine.start()
        event_bus.publish("shutdown.event", {"seq": 3})
        await engine.stop()
        assert seen == [3]
        assert engine.stats()["registered_handlers"] == 1
        assert engine.stats()["processed"] == 1
    finally:
        await engine.stop()


async def test_timeout_cancels_a_long_partition_chain_without_recursion():
    event_bus = EventBus()
    event_bus.attach_loop()
    engine = EventEngine(event_bus, shutdown_timeout=0.03)
    started, cancelled = asyncio.Event(), asyncio.Event()
    attempts = []

    async def handler(message):
        attempts.append(message["data"]["seq"])
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    engine.register("recorder", ("shutdown.event",), handler)
    await engine.start()
    try:
        # Queue capacity does not bound the accumulated in-flight chain. Feed
        # several batches through the real queue without persistence I/O.
        queue = engine._queue
        assert queue is not None
        for seq in range(queue.maxsize * 5):
            await queue.put({"type": "shutdown.event", "data": {"seq": seq}})
        await asyncio.wait_for(started.wait(), timeout=2)
        await asyncio.sleep(0)
        handlers = {
            task
            for task in asyncio.all_tasks()
            if getattr(task.get_coro(), "cr_code", None) is EventEngine._run_chained.__code__
        }
        assert len(handlers) == queue.maxsize * 5
        await asyncio.wait_for(engine.stop(), timeout=1)
        assert all(task.done() for task in handlers)
        assert attempts == [0]
        assert cancelled.is_set()
        assert engine.stats()["partitions_active"] == 0
        assert engine._task is None
        assert not event_bus._subscribers
    finally:
        await engine.stop()


async def test_start_waits_for_concurrent_stop_without_duplicating_listeners():
    event_bus = EventBus()
    event_bus.attach_loop()
    engine = EventEngine(event_bus)
    started, release = asyncio.Event(), asyncio.Event()
    seen = []

    async def handler(message):
        seq = message["data"]["seq"]
        if seq == 0:
            started.set()
            await release.wait()
        seen.append(seq)

    engine.register("recorder", ("shutdown.event",), handler)
    await engine.start()
    try:
        event_bus.publish("shutdown.event", {"seq": 0})
        await asyncio.wait_for(started.wait(), timeout=2)
        stop = asyncio.create_task(engine.stop())
        await asyncio.sleep(0)
        restart = asyncio.create_task(engine.start())
        await asyncio.sleep(0)
        assert not restart.done(), "Restart must wait for the old handler chain to finish"
        release.set()
        await asyncio.wait_for(asyncio.gather(stop, restart), timeout=2)
        assert len(event_bus._subscribers) == 1
        event_bus.publish("shutdown.event", {"seq": 1})
        await engine.stop()
    finally:
        release.set()
        await engine.stop()
    assert seen == [0, 1]
    assert engine.stats()["registered_handlers"] == 1


async def test_cancelling_stop_cleans_up_inflight_handlers():
    event_bus = EventBus()
    event_bus.attach_loop()
    engine = EventEngine(event_bus)
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def handler(message):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    engine.register("recorder", ("shutdown.event",), handler)
    await engine.start()
    try:
        event_bus.publish("shutdown.event", {})
        await asyncio.wait_for(started.wait(), timeout=2)
        stop = asyncio.create_task(engine.stop())
        await asyncio.sleep(0)
        stop.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(stop, timeout=1)
        assert cancelled.is_set()
        assert engine.stats()["partitions_active"] == 0
        assert engine._task is None
        assert not event_bus._subscribers
        await engine.start()
        await engine.stop()
    finally:
        await engine.stop()


@pytest.mark.parametrize(
    ("management_enabled", "stop_error"), [(False, False), (True, False), (True, True)]
)
@pytest.mark.parametrize("lifespan_error", [False, True])
async def test_lifespan_stops_event_producers_before_draining_engine(
    monkeypatch, management_enabled, lifespan_error, stop_error
):
    from app import main
    from app.work.management_runner import ManagementFinalizationError

    event_bus = EventBus()
    event_bus.attach_loop()
    engine = EventEngine(event_bus)
    seen = []
    engine.register("recorder", ("shutdown.producer",), lambda m: seen.append(m["data"]["name"]))

    def producer(name):
        async def stop():
            event_bus.publish("shutdown.producer", {"name": name})

        return stop

    # Exercise the real lifespan/engine ordering with inert infrastructure.
    for name in ("ensure_database_schema", "seed_default_company", "migrate_artifacts_to_drive"):
        monkeypatch.setattr(main, name, lambda *args: None)
    monkeypatch.setattr(main.lifecycle_service, "seed_lifecycle", lambda db: None)
    monkeypatch.setattr(main.authority_seed, "seed_default_authority", lambda db: None)
    monkeypatch.setattr(
        main, "get_secret_store", lambda: SimpleNamespace(register_existing=lambda db: None)
    )
    monkeypatch.setattr(
        main.workforce_access, "rerun_stale_provisioning_jobs", AsyncMock(return_value=[])
    )
    for name in (
        "position_access_sync",
        "role_context_events",
        "evidence_pipeline_enabled",
        "economy_cost_consumers_enabled",
    ):
        monkeypatch.setattr(settings, name, False)
    monkeypatch.setattr(main, "bus", event_bus)
    monkeypatch.setattr(engine_module, "engine", engine)
    monkeypatch.setattr(
        main,
        "runtime_provisioner",
        SimpleNamespace(start=lambda: None, stop=producer("provisioner")),
    )

    def start_orchestrator():
        assert engine._task is not None, "Recovery must not emit before event consumers start"
        if management_enabled:
            assert lifecycle == ["registered"]

    monkeypatch.setattr(
        main,
        "orchestrator",
        SimpleNamespace(start=start_orchestrator, stop=producer("orchestrator")),
    )
    monkeypatch.setattr(
        main,
        "get_manager",
        lambda: SimpleNamespace(
            start_healthcheck_loop=AsyncMock(), stop_healthcheck_loop=producer("healthcheck")
        ),
    )
    monkeypatch.setattr(
        main,
        "get_update_service",
        lambda: SimpleNamespace(
            start_update_loop=AsyncMock(), stop_update_loop=producer("updates")
        ),
    )
    monkeypatch.setattr(main, "gateway", SimpleNamespace(stop_all=producer("gateway")))
    assert "management_runner_enabled" in type(settings).model_fields
    monkeypatch.setattr(settings, "management_runner_enabled", management_enabled)
    lifecycle = []

    async def stop_management():
        await producer("management")()
        if stop_error:
            raise ManagementFinalizationError([73])

    monkeypatch.setattr(
        main,
        "management_runner",
        SimpleNamespace(
            register=lambda engine: lifecycle.append("registered"),
            start=lambda: lifecycle.append("started"),
            stop=stop_management,
        ),
    )

    try:
        with (
            pytest.raises(ManagementFinalizationError, match="finalization exhausted")
            if stop_error
            else pytest.raises(RuntimeError, match="lifespan interruption")
            if lifespan_error
            else nullcontext()
        ) as caught:
            async with main.lifespan(main.app):
                if lifespan_error:
                    raise RuntimeError("lifespan interruption")
        assert lifecycle == (["registered", "started"] if management_enabled else [])
        assert engine._task is None, "Management cleanup failure must not skip engine drain"
        assert seen == (["management"] if management_enabled else []) + [
            "provisioner",
            "orchestrator",
            "healthcheck",
            "updates",
            "gateway",
        ]
        if stop_error:
            assert caught.value.work_session_ids == (73,)
            if lifespan_error:
                assert str(caught.value.__context__) == "lifespan interruption"
    finally:
        await engine.stop()
