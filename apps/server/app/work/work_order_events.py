"""M2.9 WorkOrder 桥的事件消费者（E0 事件引擎）。

```text
M1: WorkOrderService.accept()  →  work_order.accepted（经济事实，契约不动）
        ↓ 事件引擎（重试 + 死信，与其它消费者同一套）
M2.9 桥: route_accepted_order() → 解析公司 Work Intake 责任人 → 记一条 `routed` 事实
```

- **幂等**：`route_accepted_order` 自己判重（已有 routed/bound/declined 就返回）；
  事件重放/引擎重试都不会写第二条；
- **不决策**：消费者只把订单"送到该看的人面前"，既不建 Project 也不选执行载体（WO5）；
- 开关：`settings.work_order_bridge_consumers_enabled`（测试默认关，dev/生产打开；
  与证据流水线 / 成本消费者同一纪律）。
"""

from __future__ import annotations

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.logging import get_logger
from app.repositories import economy as economy_repo
from app.work import work_order_bridge

logger = get_logger(__name__)

CONSUMED_EVENTS = ("work_order.accepted",)


def handle_event(message: dict) -> None:
    """处理一条事件（同步；由事件引擎在后台跑）。"""
    payload = message.get("data") or {}
    order_id = payload.get("work_order_id") or payload.get("id")
    if order_id is None:
        return
    with SessionLocal() as db:
        order = economy_repo.get_work_order(db, int(order_id))
        if order is None:  # pragma: no cover - 防御
            return
        routed = work_order_bridge.route_accepted_order(db, order, commit=True)
    if routed is not None:
        logger.info("work order routed to intake owner", extra={"work_order_id": int(order_id)})


def register(event_engine) -> None:
    """注册到事件引擎（`app/main.py` 的 lifespan 调用）。"""
    if not settings.work_order_bridge_consumers_enabled:
        return
    event_engine.register(
        "work_order_bridge",
        CONSUMED_EVENTS,
        handle_event,
        key_of=lambda message: str((message.get("data") or {}).get("work_order_id") or ""),
    )


__all__ = ["CONSUMED_EVENTS", "handle_event", "register"]
