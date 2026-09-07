"""P4d：职位事件 → Desired State 收敛 → ProvisioningJob（**在分配事务之外**）。

拍板纪律（docs/position-system.md §4、docs/workforce-domain-refactor.md §7 P7）：

* 分配成功**不依赖** Gitea / 文档 / 工作区开通成功。`position_service.assign_position()`
  只提交任职事实并发一条 `employee.position_assigned`；这里的消费者负责把权限算出来。
  反过来也一样成立：权限收敛失败绝不能回滚已经发生的任职。
* 收敛是**幂等**的 Desired State 计算，不是一次"动作"。事件重放、进程重启、
  手工改库之后再跑，结果都应当与"该有什么"一致，不产生第二个工单。
* 不写第二套 diff：期望集来自 `access.position_packages_for()`，
  增减比较走 `access.diff_entitlements()`，步骤计划走 `engine.plan_transfer()`。
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.events.bus import bus
from app.lifecycle import access
from app.lifecycle.engine import ProvisioningEngine
from app.models.enums import ProvisioningJobKind
from app.models.organization import Employee
from app.models.position import PositionAssignment
from app.repositories import lifecycle as lifecycle_repo

logger = logging.getLogger("eidolon.workforce.access")

POSITION_EVENTS = ("employee.position_assigned", "employee.position_released")

engine = ProvisioningEngine()


def converge_employee_access(
    db: Session, employee_id: int, *, reason: str = "position_change"
) -> dict | None:
    """把一个员工的**职位层**权限收敛到他当前应有的集合。

    返回 `None` = 无事发生（员工不存在 / 集合已经正确）；否则返回一份收敛结果，
    其中 `job_id` 是权限变更工单（只创建，不执行 —— 执行由调用方或消费者驱动）。

    顺序很重要，两处都不是偶然：

    1. 先记下"人级已经声称了哪些包"再动手收敛。职位层删包时要把这份名单当保护集，
       否则"卸任"会连人级本来就有的访问一起撤掉。
    2. 先算 `before` 再同步，`diff_entitlements` 才有对照物。同步之后再读现状就变成
       "自己和自己比"，diff 恒为空，工单永远不会生成。
    """
    employee = db.get(Employee, employee_id)
    if employee is None:
        return None

    rows = lifecycle_repo.list_employee_packages(db, employee_id)
    person_claimed = {
        row.package_id for row in rows if access.layer_of_source(row.source) == "person"
    }
    before = access.employee_entitlements(db, employee_id)
    desired = access.position_packages_for(db, employee_id)
    added, removed = access.sync_position_packages(
        db, employee_id, desired, person_claimed_ids=person_claimed
    )
    if not added and not removed:
        return None

    after = access.employee_entitlements(db, employee_id)
    diff = access.diff_entitlements(before, after)
    plan = engine.plan_transfer(diff)
    job = engine.create_job(
        db,
        employee_id=employee_id,
        kind=ProvisioningJobKind.permission_change.value,
        plan=plan,
        reason=f"{reason}: +{len(added)} -{len(removed)} 职位包",
        metadata={
            "trigger": reason,
            "added_package_ids": added,
            "removed_package_ids": removed,
            "layers": ["position"],
        },
    )
    db.commit()
    logger.info("职位层收敛 employee=%s +%s -%s job=%s", employee_id, added, removed, job.id)
    return {
        "employee_id": employee_id,
        "added": added,
        "removed": removed,
        "job_id": job.id,
        "diff": {
            "add": [e.key for e in diff.add],
            "remove": [e.key for e in diff.remove],
            "keep": [e.key for e in diff.keep],
        },
    }


def employees_with_assignments(db: Session) -> list[int]:
    """所有有生效任职的人（启动补收敛的作用域）。

    含没有坑的历史行：他们的职位层期望集是空，收敛会把"当年按 role 发的包"里
    属于职位层的部分收回 —— 但人级行受 `person_claimed` 保护，不会误撤。
    """
    return [
        int(row)
        for row in db.execute(
            select(PositionAssignment.employee_id)
            .where(PositionAssignment.effective_to.is_(None))
            .distinct()
        ).scalars()
    ]


def converge_all(db: Session, *, reason: str = "startup_sweep") -> list[dict]:
    """补收敛：进程在"提交任职"与"处理事件"之间死掉时，靠它把权限对上。

    幂等，且只在真的有差异时才产出结果 —— 正常情况下这是一次空转。
    """
    results: list[dict] = []
    for employee_id in employees_with_assignments(db):
        try:
            outcome = converge_employee_access(db, employee_id, reason=reason)
        except Exception:  # noqa: BLE001 - 一个人的收敛失败不能带走整轮
            logger.exception("职位层收敛失败 employee=%s", employee_id)
            db.rollback()
            continue
        if outcome is not None:
            results.append(outcome)
    return results


class PositionAccessConsumer:
    """订阅事件总线，把职位事件翻译成权限收敛 + 工单执行。

    刻意用**独立 Session**：事件携带的只是"发生了什么"，收敛要从库里重算"应该是什么"。
    顺着请求线程的 session 走，等于把收敛重新绑回那个事务。
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue | None = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._queue = bus.subscribe()
        self._task = asyncio.create_task(self._loop())
        logger.info("职位权限消费者已启动")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:  # noqa: PERF203
                pass
            self._task = None
        if self._queue is not None:
            bus.unsubscribe(self._queue)
            self._queue = None

    async def _loop(self) -> None:
        assert self._queue is not None
        while True:
            message = await self._queue.get()
            try:
                await self.handle(message)
            except Exception:  # noqa: BLE001 - 一条坏事件不能打死消费者
                logger.exception("职位事件处理失败：%s", message.get("type"))

    async def handle(self, message: dict) -> dict | None:
        """处理一条总线消息；不是职位事件就什么也不做。"""
        if not isinstance(message, dict) or message.get("type") not in POSITION_EVENTS:
            return None
        data = message.get("data") or {}
        employee_id = data.get("employee_id") or data.get("id")
        if not employee_id:
            logger.warning("%s 事件缺少 employee_id，跳过：%s", message.get("type"), data)
            return None
        with SessionLocal() as db:
            outcome = converge_employee_access(
                db, int(employee_id), reason=str(message.get("type"))
            )
            if outcome is None:
                return None
            job = lifecycle_repo.get_job(db, int(outcome["job_id"]))
            if job is None:  # pragma: no cover - 刚创建的 job 不该消失
                return outcome
            # 工单执行失败**不**回滚收敛结果：任职与授权集合已经落库，
            # 失败只体现在 job.status=partial，由 retry / reconcile 收拾。
            try:
                await engine.run(db, job)
            except Exception:  # noqa: BLE001
                logger.exception("权限工单执行失败 job=%s", job.id)
            return outcome


consumer = PositionAccessConsumer()


def sweep_on_startup() -> list[dict]:
    """启动补收敛（lifespan 里调用）。进程死在提交与处理之间时的恢复路径。"""
    with SessionLocal() as db:
        return converge_all(db)
