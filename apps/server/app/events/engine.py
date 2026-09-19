"""E0 · 事件引擎：分区并发调度器 + 处理器注册表（docs/talent-ecosystem-plan.md §1）。

总线（bus.py）继续负责"落库 + 广播"，引擎负责"消费"：`start()` 订阅一条队列，
dispatch task 把每条消息按注册表分发给匹配的 handler。分发按**分区键**保序：
同一个 key 的事件用 per-key 任务链严格串行，不同 key 并发执行；无键事件走全局分区。

纪律（与两个手写消费者时代相同）：

* handler 自己开 DB session，写事务保持短 —— 引擎只做分发、保序、重试、死信；
* handler 必须幂等（可重放）：重试会重放同一事件，启动补收敛/reconcile 是兜底；
* 某分区的失败（重试/死信）不阻塞其他分区。
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable, Hashable, Iterable
from dataclasses import dataclass
from typing import Any

from app.core.logging import get_logger
from app.events.bus import EventBus
from app.events.bus import bus as default_bus

logger = get_logger(__name__)

#: handler 签名：sync 或 async 皆可，返回值被忽略。
Handler = Callable[[dict], Any | Awaitable[Any]]
#: 分区键提取：返回 None 的事件走该 handler 的全局分区。
KeyOf = Callable[[dict], Hashable | None]
_STOP = object()


@dataclass(frozen=True)
class _Registration:
    name: str
    event_types: frozenset[str]
    handler: Handler
    key_of: KeyOf | None
    max_attempts: int


class EventEngine:
    """分区并发调度器。测试可直接构造独立实例（传入自己的 EventBus），不走 lifespan。"""

    def __init__(
        self,
        event_bus: EventBus = default_bus,
        *,
        retry_backoff: tuple[float, ...] = (0.5, 2.0),
        shutdown_timeout: float = 30.0,
    ) -> None:
        if shutdown_timeout <= 0:
            raise ValueError("shutdown_timeout 必须 > 0")
        self._bus = event_bus
        # 第 n 次重试前的退避：retry_backoff[min(n-1, len-1)]，默认 0.5s → 2s。
        # 分区并发后 "database is locked" 瞬时冲突会变多，重试就是吃这类冲突的。
        self._retry_backoff = retry_backoff
        self._shutdown_timeout = shutdown_timeout
        self._lifecycle_lock = asyncio.Lock()
        self._handlers: list[_Registration] = []
        self._names: set[str] = set()
        self._tails: dict[tuple[str, Hashable | None], asyncio.Task] = {}
        self._queue: asyncio.Queue | None = None
        self._task: asyncio.Task | None = None
        self._processed = 0
        self._retried = 0
        self._dead_lettered = 0

    # ------------------------------------------------------------ 注册表

    def register(
        self,
        name: str,
        event_types: Iterable[str],
        handler: Handler,
        *,
        key_of: KeyOf | None = None,
        max_attempts: int = 3,
    ) -> None:
        if name in self._names:
            raise ValueError(f"handler 重名：{name}")
        if max_attempts < 1:
            raise ValueError("max_attempts 必须 >= 1")
        self._names.add(name)
        self._handlers.append(
            _Registration(
                name=name,
                event_types=frozenset(event_types),
                handler=handler,
                key_of=key_of,
                max_attempts=max_attempts,
            )
        )

    # ------------------------------------------------------------ 生命周期

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._task is not None:
                return
            self._queue = self._bus.subscribe()
            self._task = asyncio.create_task(self._dispatch_loop())
            logger.info("事件引擎已启动（%d 个处理器）", len(self._handlers))

    async def stop(self) -> None:
        """Drain accepted in-memory events, then finish handlers within the grace period.

        Producers should stop first. Events accepted before unsubscribe are
        dispatched in order; existing queue overflow and process crashes still
        require separate recovery. Events published after the cutoff are not
        accepted by this engine, including new events emitted by a draining handler.
        Timeout cancellation is cooperative; it cannot preempt synchronous work
        or work already running in a handler's worker thread.
        """
        async with self._lifecycle_lock:
            if self._task is None:
                return
            queue, dispatcher = self._queue, self._task
            assert queue is not None
            self._bus.unsubscribe(queue)
            try:
                async with asyncio.timeout(self._shutdown_timeout):
                    # publish() schedules puts with call_soon_threadsafe. The bus
                    # lock makes unsubscribe a cutoff; one loop turn lets every
                    # put accepted before that cutoff run before our FIFO marker.
                    await asyncio.sleep(0)
                    await queue.put(_STOP)
                    await dispatcher
                    # Dispatch has finished, so no new partition tails can appear.
                    await asyncio.gather(*list(self._tails.values()), return_exceptions=True)
            except TimeoutError:
                logger.warning(
                    "事件引擎停机排空超时（timeout=%.1fs queued=%d partitions=%d）",
                    self._shutdown_timeout,
                    queue.qsize(),
                    len(self._tails),
                )
            finally:
                # On timeout or caller cancellation, cancel the whole remaining
                # chain, including predecessors awaited by each partition tail.
                pending = [dispatcher, *self._tails.values()]
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                self._tails.clear()
                self._queue = None
                self._task = None
            logger.info(
                "事件引擎已停止（processed=%d retried=%d dead_lettered=%d）",
                self._processed,
                self._retried,
                self._dead_lettered,
            )

    # ------------------------------------------------------------ 分发

    async def _dispatch_loop(self) -> None:
        assert self._queue is not None
        while True:
            message = await self._queue.get()
            if message is _STOP:
                return
            if not isinstance(message, dict):
                continue
            event_type = message.get("type")
            for registration in self._handlers:
                if event_type in registration.event_types:
                    self._dispatch(registration, message)

    def _dispatch(self, registration: _Registration, message: dict) -> None:
        key = registration.key_of(message) if registration.key_of else None
        partition = (registration.name, key)
        previous = self._tails.get(partition)
        task = asyncio.create_task(self._run_chained(previous, registration, message, partition))
        self._tails[partition] = task

    async def _run_chained(
        self,
        previous: asyncio.Task | None,
        registration: _Registration,
        message: dict,
        partition: tuple[str, Hashable | None],
    ) -> None:
        try:
            if previous is not None:
                try:
                    await previous
                except asyncio.CancelledError:
                    # A cancelled predecessor may not break this partition, but
                    # this task's own shutdown cancellation must not start work.
                    if asyncio.current_task().cancelling():
                        raise
                except Exception:
                    pass
            await self._execute(registration, message)
        finally:
            if self._tails.get(partition) is asyncio.current_task():
                del self._tails[partition]

    async def _execute(self, registration: _Registration, message: dict) -> None:
        for attempt in range(1, registration.max_attempts + 1):
            try:
                result = registration.handler(message)
                if inspect.isawaitable(result):
                    await result
                self._processed += 1
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                if attempt >= registration.max_attempts:
                    self._dead_lettered += 1
                    logger.exception(
                        "事件处理最终失败，进入死信 handler=%s type=%s attempts=%d",
                        registration.name,
                        message.get("type"),
                        attempt,
                    )
                    return
                self._retried += 1
                backoff = self._retry_backoff[min(attempt - 1, len(self._retry_backoff) - 1)]
                logger.warning(
                    "事件处理失败，%.1fs 后重试（%d/%d）handler=%s type=%s",
                    backoff,
                    attempt,
                    registration.max_attempts,
                    registration.name,
                    message.get("type"),
                )
                await asyncio.sleep(backoff)

    # ------------------------------------------------------------ 可观测

    def stats(self) -> dict[str, int]:
        return {
            "registered_handlers": len(self._handlers),
            "partitions_active": len(self._tails),
            "processed": self._processed,
            "retried": self._retried,
            "dead_lettered": self._dead_lettered,
            "queue_dropped": self._bus.dropped,
        }


engine = EventEngine()
