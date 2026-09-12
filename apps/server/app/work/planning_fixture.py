"""M2.5 **确定性规划 fixture**：它只负责"谁创建 Task Graph"，然后退出。

用户拍板 §8：

```text
正常模式：      Manager Agent        → Task DAG
测试/教程：     Deterministic Fixture → Task DAG
```

**Graph 创建之后必须走同一个** `Task → Dependency → resolve_ready_tasks → Dispatcher →
WorkSession` 路径 —— 不得维护第二套 fixture execution engine（R7）。

因此这个模块**只建图、不推进**：建完就返回，之后由 `app/work/dispatch.py` +
编排器按同一套就绪/可派发规则跑。M2.1 之前"planning 任务完成 → 系统生成固定图"
那条隐式 fallback 已经删除（W33/R12）。

命名纪律：函数名带 `deterministic_fixture` / 常量名带 `DETERMINISTIC_PLAN` ——
任何开发者看到都该立刻明白**这不是生产环境的公司决策逻辑**。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.orm import Session

from app.models.enums import EmployeeRole, TaskKind, TaskStatus
from app.models.project import Project
from app.repositories import project as project_repo
from app.services import position_compat
from app.services import tasks as task_service


@dataclass(frozen=True)
class DeterministicPlanStage:
    """确定性图的一个阶段：里程碑 + 一个任务 + 它对前序阶段的依赖。"""

    milestone: str
    kind: str
    role: str
    depends_on_kinds: tuple[str, ...] = ()
    #: 相对项目计划起点的窗口（天）
    window: tuple[int, int] = (0, 1)


#: 确定性执行图 —— **测试/教程/CI/演示基础设施，不是生产规划逻辑**。
DETERMINISTIC_PLAN_STAGES: tuple[DeterministicPlanStage, ...] = (
    DeterministicPlanStage(
        "Intake", TaskKind.order_review.value, EmployeeRole.ceo.value, (), (0, 1)
    ),
    DeterministicPlanStage(
        "Planning",
        TaskKind.planning.value,
        EmployeeRole.product_manager.value,
        (TaskKind.order_review.value,),
        (1, 2),
    ),
    DeterministicPlanStage(
        "Discovery",
        TaskKind.research.value,
        EmployeeRole.researcher.value,
        (TaskKind.planning.value,),
        (2, 5),
    ),
    DeterministicPlanStage(
        "Build",
        TaskKind.development.value,
        EmployeeRole.engineer.value,
        (TaskKind.research.value,),
        (5, 11),
    ),
    DeterministicPlanStage(
        "Verify",
        TaskKind.testing.value,
        EmployeeRole.qa_engineer.value,
        (TaskKind.development.value,),
        (11, 15),
    ),
    DeterministicPlanStage(
        "Release",
        TaskKind.final_review.value,
        EmployeeRole.ceo.value,
        (TaskKind.testing.value,),
        (15, 18),
    ),
)

#: 计划总窗口（天）—— 与 `services.projects.DEFAULT_PLANNING_WINDOW_DAYS` 一致的口径
PLAN_WINDOW_DAYS = 18


def build_deterministic_plan(db: Session, project: Project) -> list:
    """按 `DETERMINISTIC_PLAN_STAGES` 建里程碑 + 任务 + 依赖（**一次性建全图**）。

    与旧实现的区别：旧版在 `order_review`/`planning` 完成时**分步**生成，
    因此编排器必须按 `TaskKind` 分支推进 —— 那正是 M2.5 要拆掉的东西。
    现在全图在建项目时就存在，编排器只需要按依赖就绪派发（R7/R12）。

    负责人沿用职位域的旧口径（`position_compat.employee_by_legacy_role`）：
    fixture 的意义就是确定性，它不需要也不应该询问责任路由（那是生产路径的事）。
    """
    schedule_start = project.planned_start_at or project.created_at
    project.planned_start_at = schedule_start
    project.planned_end_at = schedule_start + timedelta(days=PLAN_WINDOW_DAYS)

    by_kind: dict[str, int] = {}
    created: list = []
    for order, stage in enumerate(DETERMINISTIC_PLAN_STAGES, start=1):
        start_offset, end_offset = stage.window
        assignee = position_compat.employee_by_legacy_role(db, project.company_id, stage.role)
        milestone = project_repo.create_milestone(
            db,
            project_id=int(project.id),
            name=stage.milestone,
            description=f"{stage.milestone} 阶段（deterministic fixture）",
            order=order,
            owner_id=assignee.id if assignee else None,
            planned_start_at=schedule_start + timedelta(days=start_offset),
            planned_end_at=schedule_start + timedelta(days=end_offset),
        )
        task = task_service.create_task(
            db,
            project_id=int(project.id),
            milestone_id=int(milestone.id),
            title=f"{stage.milestone}：{project.name}",
            kind=stage.kind,
            assignee_id=assignee.id if assignee else None,
            # 全部从 backlog 开始；就绪由依赖决定（第一个阶段没有依赖 ⇒ 立刻就绪）
            status=TaskStatus.backlog.value,
            description=project.source_order_text,
            acceptance_criteria=f"产出符合要求的 {stage.kind} 交付物",
            priority=10 - order,
            sequence=order,
            depends_on=[by_kind[kind] for kind in stage.depends_on_kinds],
            planned_start_at=schedule_start + timedelta(days=start_offset),
            planned_end_at=schedule_start + timedelta(days=end_offset),
        )
        by_kind[stage.kind] = int(task.id)
        created.append(task)
    return created


__all__ = [
    "DeterministicPlanStage",
    "DETERMINISTIC_PLAN_STAGES",
    "PLAN_WINDOW_DAYS",
    "build_deterministic_plan",
]
