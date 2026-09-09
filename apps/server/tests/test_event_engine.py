"""E0 事件引擎：分区保序 / 跨分区并发 / 重试与死信 / 类型过滤 / stats。

测试直接构造独立实例（自己的 EventBus + EventEngine），不走 lifespan、
不碰 settings 门控 —— conftest 的两个门控只影响 lifespan 里的注册。
bus.publish 需要 loop attach：每个测试在事件循环内先 attach_loop 再 publish。
"""

from __future__ import annotations

import asyncio

from app.events.bus import EventBus
from app.events.engine import EventEngine


async def _started_engine(**kwargs) -> tuple[EventBus, EventEngine]:
    event_bus = EventBus()
    event_bus.attach_loop()
    engine = EventEngine(event_bus, **kwargs)
    await engine.start()
    return event_bus, engine


async def test_same_key_events_are_processed_in_publish_order():
    event_bus, engine = await _started_engine()
    order: list[int] = []
    done = asyncio.Event()

    def handler(message: dict) -> None:
        order.append(message["data"]["seq"])
        if len(order) == 5:
            done.set()

    engine.register("recorder", ("thing.happened",), handler, key_of=lambda m: m["data"]["key"])
    try:
        for seq in range(5):
            event_bus.publish("thing.happened", {"key": "employee-1", "seq": seq})
        await asyncio.wait_for(done.wait(), 5)
    finally:
        await engine.stop()
    assert order == [0, 1, 2, 3, 4]


async def test_different_keys_run_concurrently():
    event_bus, engine = await _started_engine()
    a_started = asyncio.Event()
    a_finished = asyncio.Event()
    b_saw_a_blocked: list[bool] = []

    async def handler(message: dict) -> None:
        if message["data"]["key"] == "a":
            a_started.set()
            await asyncio.sleep(0.3)
            a_finished.set()
        else:
            # B 完成时 A 已经开始但还没结束 ⇒ 两个 key 的处理确实重叠。
            b_saw_a_blocked.append(a_started.is_set() and not a_finished.is_set())

    engine.register("demo", ("thing.happened",), handler, key_of=lambda m: m["data"]["key"])
    try:
        event_bus.publish("thing.happened", {"key": "a"})
        event_bus.publish("thing.happened", {"key": "b"})
        await asyncio.wait_for(a_finished.wait(), 5)
    finally:
        await engine.stop()
    assert b_saw_a_blocked == [True]


async def test_retry_then_success_and_dead_letter_is_isolated_per_key():
    # 退避调小：重试语义不变，测试不等真实的 0.5s/2s。
    event_bus, engine = await _started_engine(retry_backoff=(0.01, 0.01))
    attempts = {"flaky": 0, "doomed": 0}
    flaky_done = asyncio.Event()
    doomed_done = asyncio.Event()

    async def flaky(message: dict) -> None:
        attempts["flaky"] += 1
        if attempts["flaky"] < 3:
            raise RuntimeError("database is locked")
        flaky_done.set()

    async def doomed(message: dict) -> None:
        attempts["doomed"] += 1
        if attempts["doomed"] >= 3:
            doomed_done.set()
        raise RuntimeError("永远失败")

    engine.register("flaky", ("flaky.event",), flaky, max_attempts=3)
    engine.register("doomed", ("doomed.event",), doomed, max_attempts=3)
    try:
        event_bus.publish("doomed.event", {"key": "x"})
        event_bus.publish("flaky.event", {"key": "a"})
        await asyncio.wait_for(flaky_done.wait(), 5)
        await asyncio.wait_for(doomed_done.wait(), 5)
    finally:
        await engine.stop()
    assert attempts == {"flaky": 3, "doomed": 3}
    stats = engine.stats()
    assert stats["processed"] == 1, "flaky 第三次成功；doomed 不算 processed"
    assert stats["retried"] == 4, "每个失败 attempt 计一次：flaky 2 次 + doomed 2 次"
    assert stats["dead_lettered"] == 1, "doomed 进死信，但不影响 flaky 分区"


async def test_unregistered_event_types_never_reach_the_handler():
    event_bus, engine = await _started_engine()
    seen: list[int] = []
    done = asyncio.Event()

    def handler(message: dict) -> None:
        seen.append(message["data"]["i"])
        done.set()

    engine.register("selective", ("wanted.type",), handler)
    try:
        event_bus.publish("other.type", {"i": 1})
        event_bus.publish("wanted.type", {"i": 2})
        event_bus.publish("another.type", {"i": 3})
        await asyncio.wait_for(done.wait(), 5)
    finally:
        await engine.stop()
    assert seen == [2]
    assert engine.stats()["processed"] == 1


async def test_stats_reports_all_fields():
    event_bus, engine = await _started_engine()
    try:
        engine.register("noop", ("noop.event",), lambda m: None)
        stats = engine.stats()
        assert set(stats) == {
            "registered_handlers",
            "partitions_active",
            "processed",
            "retried",
            "dead_lettered",
            "queue_dropped",
        }
        assert stats["registered_handlers"] == 1
        assert stats["queue_dropped"] == 0
    finally:
        await engine.stop()
