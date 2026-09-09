"""In-memory asyncio pub/sub EventBus + events-table persistence + WS broadcast.

See docs/architecture.md §7. Every published event is INSERTed into the events
table (activity feed source) and broadcast as ``{type, data, ts}`` JSON to all
/ws/events clients. ``publish`` is synchronous and thread-safe so both request
handlers (threadpool) and the orchestrator (event loop) can emit.
"""

import asyncio
from datetime import UTC, datetime
from typing import Any

from app.core.database import SessionLocal
from app.core.logging import get_logger
from app.core.redaction import redact_data
from app.models.event import Event

logger = get_logger(__name__)


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        #: 订阅者队列满被丢弃的消息数（事件引擎 stats().queue_dropped 的数据源）。
        self.dropped = 0

    def attach_loop(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        self._loop = loop or asyncio.get_running_loop()

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def publish(
        self,
        type: str,
        data: dict[str, Any],
        *,
        company_id: int | None = None,
        actor_employee_id: int | None = None,
        project_id: int | None = None,
        task_id: int | None = None,
    ) -> None:
        # Secrets must never reach the events table or WS clients.
        data = redact_data(data)
        event = Event(
            type=type,
            company_id=company_id,
            actor_employee_id=actor_employee_id,
            project_id=project_id,
            task_id=task_id,
            payload=data,
        )
        try:
            with SessionLocal() as db:
                db.add(event)
                db.commit()
        except Exception:
            logger.exception("failed to persist event %s", type)

        message = {
            "type": type,
            "data": data,
            "company_id": company_id,
            "ts": datetime.now(UTC).isoformat(),
        }
        loop = self._loop
        if loop is None or not loop.is_running():
            return
        for queue in list(self._subscribers):
            loop.call_soon_threadsafe(self._safe_put, queue, message)

    def _safe_put(self, queue: asyncio.Queue, message: dict) -> None:
        try:
            queue.put_nowait(message)
        except asyncio.QueueFull:
            self.dropped += 1
            logger.warning("dropping event for slow subscriber: %s", message.get("type"))


bus = EventBus()
