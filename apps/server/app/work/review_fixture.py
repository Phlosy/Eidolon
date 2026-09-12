"""M2.7 **确定性评审替身**（Test / Tutorial / CI / 演示基础设施，不是生产判断逻辑）。

它替掉的是 **Reviewer Agent 的判断**：让

```text
Project → Task Graph → 执行 → Artifact → Review → Completed
```

这条链在不依赖 LLM Reviewer 的前提下可重复、可断言、零成本。

两条硬纪律（与 `planning_fixture.py` 同款，M2.1/D3/W33 的既有模式）：

1. **生产项目永远不会落到它头上**：没有"没人评审 → 系统自己通过"。
   只有 `projects.planning_fixture == deterministic_template` **且**
   `settings.allow_planning_fixtures` 为真时才生效；
2. **结论仍然署在一个人头上**：它调用的是与真实评审**完全相同**的服务
   （`reviews.open_review_request` + `reviews.submit_verdict`），
   结论记在项目 Manager 名下并在理由里写明这是**替身**，而不是"系统自动通过"。

换句话说：它是**被模拟的 Agent 决定**，不是系统启发式（W17/RV1 的守卫生效范围
是生产路径 —— 本模块的存在不改变那条边界，反而把"谁做的决定"写得更清楚）。
"""

from __future__ import annotations

import logging

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.enums import PlanningFixture, ReviewVerdict, TaskStatus
from app.models.project import Project
from app.models.review import ReviewRequest
from app.repositories import project as project_repo
from app.repositories import review as review_repo
from app.work import reviews

logger = logging.getLogger(__name__)

#: 替身评审的理由（写进 `review_requests.reason` / notes，任何人看到都知道这不是真评审）
FIXTURE_REVIEW_REASON = "deterministic fixture reviewer（替身：模拟 Reviewer Agent 的 PASS）"


def enabled_for(project: Project | None) -> bool:
    """这个项目是否由替身来评（门控与规划 fixture 完全一致）。"""
    if project is None:
        return False
    if not settings.allow_planning_fixtures:
        return False
    return (
        str(getattr(project, "planning_fixture", ""))
        == PlanningFixture.deterministic_template.value
    )


def review_now(*, task_id: int, project_id: int) -> ReviewRequest | None:
    """替身出一次 PASS。**由项目负责人（Manager 替身）发起、也署在它名下**。

    两段**独立短事务**（这是仓库的既有纪律，不是随便拆的）：

    ```text
    ① 发起评审（写请求 + 系统收集事实 + 提交）
    ② 给出结论（写结论 + 任务状态落地 + 提交）
    ```

    每一段都在**提交之后**才发事件 —— 事件持久化走的是另一个数据库连接，
    在写事务还没提交时发事件，SQLite 的单写者会直接 `database is locked`
    （M2.6/M2.7 实测踩过）。

    返回 None 表示"没评成"（门控没开 / 项目没有负责人 / 替身评审出错）：
    任务就停在 `in_review` 由管理层接手 —— **绝不**因为替身失败就自动通过。
    """
    with SessionLocal() as db:
        project = project_repo.get_project(db, int(project_id))
        if not enabled_for(project):
            return None
        task = project_repo.get_task(db, int(task_id))
        if task is None or str(task.status) != TaskStatus.in_review.value:
            return None
        # fixture 路径**不走 work intake**（M2.1/D3：模板替掉的就是管理层的规划），
        # 所以它通常没有 `management_employee_id`；替身改用项目负责人（立项时指派的
        # PM）作为"Manager/Reviewer 替身" —— 依然是**一个具体的人**，不是系统。
        reviewer_id = project.management_employee_id or project.owner_id
        if reviewer_id is None:
            return None
        try:
            request = reviews.open_review_request(
                db,
                task=task,
                requester_employee_id=int(reviewer_id),
                reviewer_employee_id=int(reviewer_id),
                reason=FIXTURE_REVIEW_REASON,
                commit=True,
            )
        except Exception:  # noqa: BLE001 - 替身失败必须降级为"等人"，不能连累 worker
            logger.exception("fixture review request failed task=%s", task_id)
            return None
        request_id = int(request.id)

    with SessionLocal() as db:
        request = review_repo.get_request(db, request_id)
        if request is None:  # pragma: no cover - 防御
            return None
        try:
            return reviews.submit_verdict(
                db,
                request=request,
                reviewer_employee_id=int(reviewer_id),
                verdict=ReviewVerdict.passed,
                notes=FIXTURE_REVIEW_REASON,
                commit=True,
            )
        except Exception:  # noqa: BLE001 - 同上：失败就等人，不自动通过
            logger.exception("fixture verdict failed task=%s", task_id)
            return None


__all__ = ["FIXTURE_REVIEW_REASON", "enabled_for", "review_now"]
