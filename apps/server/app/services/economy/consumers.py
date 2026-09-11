"""经营成本的事件消费者（M1.5）—— T2 培养完成时的**培养成本**（Sink）。

设计 §7/§25：培养（T1 培养资源消耗）→ Treasury。M1 不想在 T2 的培养流程里插扣款逻辑
（T2 已冻结，且扣款失败不该影响培养本身），所以走**事件**：

```
T2: cultivation.completed（profile 结业）
        ↓ 事件引擎（重试 + 死信，与证据流水线同一套）
EconomyCostConsumer: 读该培养实例的 sessions → 计价 → CompanyCostService.charge(TRAINING)
        ↓ 余额不足 ⇒ 记 unpaid（不阻塞、不产生负余额）
```

- **幂等**：`idempotency_key = training:profile:<profile_id>`；事件重放/引擎重试都不会重复扣款；
- **不阻塞**：消费者在自己的会话里跑；扣款失败（欠费）只记日志与 `unpaid` 事实；
- 开关：`settings.economy_cost_consumers_enabled`（测试默认关，dev/生产按需打开；
  与证据流水线同一纪律）。
"""

from __future__ import annotations

from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.logging import get_logger
from app.economy.contracts import EconomicActor
from app.models.cultivation import TrainingProgram
from app.models.enums import EconomicCategory
from app.services.economy.costs import CompanyCostService

logger = get_logger(__name__)

CONSUMED_EVENTS = ("cultivation.completed",)


def handle_event(message: dict) -> None:
    """处理一条事件（同步；由事件引擎在线程/任务里调用）。"""
    payload = message.get("data") or {}
    company_id = message.get("company_id")
    if company_id is None:
        return  # 无公司归属（市场发行的 free 角色）⇒ 没有付款方，不产生成本
    profile_id = payload.get("profile_id")
    person_id = payload.get("person_id")
    if profile_id is None or person_id is None:
        return

    with SessionLocal() as db:
        program = db.scalars(
            select(TrainingProgram)
            .where(TrainingProgram.person_id == int(person_id))
            .order_by(TrainingProgram.id.desc())
        ).first()
        sessions = 0
        if program is not None and isinstance(program.resource_used, dict):
            sessions = int(program.resource_used.get("sessions", 0) or 0)
        units = max(1, sessions)
        amount = units * int(settings.economy_training_credit_per_session)

        charge = CompanyCostService(db).charge(
            actor=EconomicActor.company(int(company_id)),
            amount=amount,
            category=EconomicCategory.training,
            reason="training:cultivation",
            reference_type="training_program",
            reference_id=str(int(program.id)) if program is not None else str(int(profile_id)),
            idempotency_key=f"training:profile:{int(profile_id)}",
            metadata={
                "profile_id": int(profile_id),
                "person_id": int(person_id),
                "sessions": sessions,
                "template": payload.get("template", ""),
            },
            commit=False,
        )
        db.commit()
        logger.info(
            "training cost profile=%s company=%s amount=%d paid=%s reason=%s",
            profile_id,
            company_id,
            amount,
            charge.paid,
            charge.reason,
        )


def register(event_engine) -> None:
    """把成本消费者挂到事件引擎（按 profile 分区，避免同一培养实例并发处理）。"""
    event_engine.register(
        "economy_costs",
        CONSUMED_EVENTS,
        handle_event,
        key_of=lambda message: (message.get("data") or {}).get("profile_id"),
    )
