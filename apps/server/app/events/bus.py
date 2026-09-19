"""In-memory asyncio pub/sub EventBus + events-table persistence + WS broadcast.

See docs/architecture.md §7. Every published event is INSERTed into the events
table (activity feed source) and broadcast as ``{type, data, ts}`` JSON to all
/ws/events clients. ``publish`` is synchronous and thread-safe so both request
handlers (threadpool) and the orchestrator (event loop) can emit.
"""

import asyncio
from datetime import UTC, datetime
from threading import Lock
from typing import Any

from app.core.database import SessionLocal
from app.core.logging import get_logger
from app.core.redaction import redact_data
from app.models.event import Event
from app.repositories import persons as person_repo

logger = get_logger(__name__)


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        # Protect the subscription cutoff and enqueue scheduling together. A
        # publisher must not retain a subscriber and schedule its put after
        # unsubscribe has returned (the engine drains at that boundary).
        self._subscription_lock = Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        #: 订阅者队列满被丢弃的消息数（事件引擎 stats().queue_dropped 的数据源）。
        self.dropped = 0

    def attach_loop(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        self._loop = loop or asyncio.get_running_loop()

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        with self._subscription_lock:
            self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        with self._subscription_lock:
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
        event_id = None
        try:
            with SessionLocal() as db:
                # R1.4：actor 的 person 镜像在**唯一落库点**统一解析双写 ——
                # publish 签名不变，全仓调用方零改动；解析失败留空 + warning（§5）。
                if actor_employee_id is not None:
                    event.actor_person_id = person_repo.write_person_id(db, actor_employee_id)
                db.add(event)
                db.commit()
                event_id = event.id
        except Exception:
            logger.exception("failed to persist event %s", type)

        self._broadcast(type, data, company_id, event_id=event_id)

    def publish_persisted(self, event_id: int) -> bool:
        """Offer a committed event without creating a second activity-feed record."""
        with SessionLocal() as db:
            event = db.get(Event, event_id)
            if event is None:
                return False
            return self._broadcast(
                event.type, redact_data(event.payload), event.company_id, event_id=event.id
            )

    def _broadcast(
        self, type: str, data: dict, company_id: int | None, *, event_id: int | None
    ) -> bool:
        message = {
            "type": type,
            "data": data,
            "company_id": company_id,
            "ts": datetime.now(UTC).isoformat(),
            "event_id": event_id,
        }
        loop = self._loop
        if loop is None or not loop.is_running():
            return False
        with self._subscription_lock:
            for queue in self._subscribers:
                loop.call_soon_threadsafe(self._safe_put, queue, message)
        return True

    def _safe_put(self, queue: asyncio.Queue, message: dict) -> None:
        try:
            queue.put_nowait(message)
        except asyncio.QueueFull:
            self.dropped += 1
            logger.warning("dropping event for slow subscriber: %s", message.get("type"))


bus = EventBus()
