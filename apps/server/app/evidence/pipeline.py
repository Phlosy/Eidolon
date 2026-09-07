"""EvidencePipeline —— Domain Event → Collector → Normalize →（必要时）Assessment。

- `handle_event(db, message)`：同步处理一条总线事件（测试直接调用，production 由
  consumer 在 lifespan 内消费）。
- `EventsConsumer`：与 PositionAccessConsumer 同款 asyncio consumer，gated by
  settings.evidence_pipeline_enabled（conftest 关闭 → 测试不启动后台消费者）。
- 事件丢失的兜底：`reconcile.reconcile_employee/project` 随时可重扫（幂等）。
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.orm import Session

from app.evidence import normalize, reconcile
from app.evidence.collectors import REGISTRY
from app.evidence.policy import EVENT_DISPATCH

logger = logging.getLogger(__name__)

#: 消费这些事件类型；其余类型不产生证据（如 runtime.* / employee.* 等）。
CONSUMED_EVENTS = frozenset(EVENT_DISPATCH)

#: 需要带 employee 上下文的字段名兜底
_ID_KEYS = ("id", "task_id", "review_id", "usage_id", "record_id")


def _payload_id(data: dict) -> int | None:
    for key in _ID_KEYS:
        value = data.get(key)
        if value is not None:
            return int(value)
    return None


def handle_event(db: Session, message: dict) -> dict:
    """处理一条总线事件：collect → normalize；project.completed 顺带跑项目末考核。

    返回 {"collected": n, "created": n, "updated": n, "assessments": n}。
    """
    event_type = message.get("type") if isinstance(message, dict) else None
    if event_type not in CONSUMED_EVENTS:
        return {"collected": 0, "created": 0, "updated": 0, "assessments": 0}
    data = message.get("data") or {}
    collector = REGISTRY.event_collector(event_type)
    if collector is None:
        return {"collected": 0, "created": 0, "updated": 0, "assessments": 0}
    task_event = event_type in {"task.completed", "task.failed"}
    payload = {"task_id": _payload_id(data)} if task_event else {}
    source_id = _payload_id(data)
    if source_id is not None:
        if collector.source_type == "task":
            payload["task_id"] = source_id
        elif collector.source_type == "review":
            payload["review_id"] = source_id
        elif collector.source_type == "skill_usage":
            payload["usage_id"] = source_id
        elif collector.source_type == "learning":
            payload["record_id"] = source_id

    candidates = collector.collect(db, payload)
    stats: dict = {"collected": len(candidates), "created": 0, "updated": 0, "assessments": 0}
    if not candidates:
        return stats
    for candidate in candidates:
        try:
            _row, is_new = normalize.upsert_evidence(db, candidate)
        except ValueError:  # 源校验失败：记录并继续（不伪造）
            continue
        if is_new:
            stats["created"] += 1
        else:
            stats["updated"] += 1

    if event_type == "project.completed":
        project_id = data.get("id")
        if project_id is not None:
            stats["assessments"] = _project_end_assessments(db, int(project_id))
    return stats


def _project_end_assessments(db: Session, project_id: int) -> int:
    """项目完成 → 先 reconcile 项目内事实（事件丢失兜底）再跑 project_end 考核。"""
    reconcile.reconcile_project(db, project_id)
    from app.services.assessment import run_project_end_assessments

    return run_project_end_assessments(db, project_id)


class EventsConsumer:
    """lifespan 内消费 bus 事件的异步消费者（settings 门控）。"""

    def __init__(self) -> None:
        self._queue: asyncio.Queue | None = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        from app.core.database import SessionLocal
        from app.events.bus import bus

        self._queue = bus.subscribe()
        self._task = asyncio.ensure_future(self._loop(SessionLocal))

    async def stop(self) -> None:
        from app.events.bus import bus

        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._queue is not None:
            bus.unsubscribe(self._queue)
            self._queue = None

    async def _loop(self, session_factory):
        assert self._queue is not None
        while True:
            message = await self._queue.get()
            try:
                with session_factory() as db:
                    stats = handle_event(db, message)
                    if stats["collected"] or stats["assessments"]:
                        db.commit()
            except Exception:  # noqa: BLE001 - 单条事件失败不能打死消费者
                logger.exception("证据事件处理失败：%s", message.get("type"))


consumer = EventsConsumer()
