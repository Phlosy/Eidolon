"""M2.7 **评审 / 返工 / 重新规划**的唯一口径（W17 / RV1–RV8 与设计 §12）。

```text
Worker 干完              → task 停在 in_review（**系统不替谁通过**，RV1/RV2）
Manager/Requester 发起   → review_requests(status=open, reviewer=**指定的人**)
系统                     → 收集事实（review_facts：产物/交接/会话/返工次数）
Reviewer Agent 出结论    → ReviewVerdict（PASS / REWORK / REJECT / ESCALATE）
系统按**显式映射**落地    → REVIEW_VERDICT_TARGETS（ESCALATE 没有目标，停下来等人）
```

四条边界：

1. **系统永不产生结论**（RV1）：它只收集事实、只执行别人给的结论。
   "测试都过了所以 PASS" 这类启发式在 M2 里不存在。
2. **`in_review` 只能由结论推进**（RV2）：M2.7 起 `_finalize` 不再连跳 `done`。
3. **事实与结论分开存**（RV3）：`review_facts`（系统写）vs `review_requests.verdict`（人写）。
4. **结论追加式**（RV4）：写下即不可改写；改判走一条新请求，历史保留。

`replan` 属于**项目**而不是任务（RV7）：只有这个项目的 Manager 能发起，
系统自己永远不 replan（W2/W34）。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.events.bus import bus
from app.models.base import utcnow
from app.models.enums import LifecycleStatus, ProjectStatus, ReviewVerdict, TaskStatus
from app.models.organization import Employee
from app.models.project import Project, Task
from app.models.review import ReviewRequest
from app.repositories import project as project_repo
from app.repositories import review as review_repo
from app.services import tasks as task_service
from app.work import contracts as C
from app.work import review_facts


class ReviewError(C.WorkContractError):
    """评审契约被违反（结构问题：状态不对 / 结论没有评审人 / 越权 replan）。"""


# ---------------------------------------------------------------------------
# 读模型
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReviewFactView:
    kind: str
    payload: dict
    source: str

    def as_dict(self) -> dict:
        return {"kind": self.kind, "payload": self.payload, "source": self.source}


@dataclass(frozen=True)
class ReviewView:
    """一次评审的**全部可回答事实**（读面 / 读工具共用）。"""

    request_id: int
    task_id: int
    project_id: int
    status: str
    verdict: str | None
    reviewer_employee_id: int
    requested_by_employee_id: int
    reason: str
    notes: str
    verdict_decision_id: int | None
    requested_at: str
    decided_at: str | None
    facts: tuple[ReviewFactView, ...] = ()
    task_status: str = ""
    target_status: str | None = None
    rework_count: int = 0

    def as_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "task_id": self.task_id,
            "project_id": self.project_id,
            "status": self.status,
            "verdict": self.verdict,
            "reviewer_employee_id": self.reviewer_employee_id,
            "requested_by_employee_id": self.requested_by_employee_id,
            "reason": self.reason,
            "notes": self.notes,
            "verdict_decision_id": self.verdict_decision_id,
            "requested_at": self.requested_at,
            "decided_at": self.decided_at,
            "facts": [fact.as_dict() for fact in self.facts],
            "task_status": self.task_status,
            "target_status": self.target_status,
            "rework_count": self.rework_count,
        }


def review_view(db: Session, request: ReviewRequest) -> ReviewView:
    task = project_repo.get_task(db, int(request.task_id))
    return ReviewView(
        request_id=int(request.id),
        task_id=int(request.task_id),
        project_id=int(request.project_id),
        status=request.status,
        verdict=request.verdict,
        reviewer_employee_id=int(request.reviewer_employee_id),
        requested_by_employee_id=int(request.requested_by_employee_id),
        reason=request.reason or "",
        notes=request.verdict_notes or "",
        verdict_decision_id=(
            int(request.verdict_decision_id) if request.verdict_decision_id else None
        ),
        requested_at=request.requested_at.isoformat() if request.requested_at else "",
        decided_at=request.decided_at.isoformat() if request.decided_at else None,
        facts=tuple(
            ReviewFactView(kind=row.kind, payload=row.payload_json, source=row.source)
            for row in review_repo.list_facts(db, int(request.id))
        ),
        task_status=str(task.status) if task else "",
        target_status=C.REVIEW_VERDICT_TARGETS.get(request.verdict or ""),
        rework_count=int(task.rework_count or 0) if task else 0,
    )


def open_request_for_task(db: Session, task_id: int) -> ReviewRequest | None:
    """这个任务当前**未决定**的评审请求（没有则 None）。"""
    opens = [
        row
        for row in review_repo.list_requests(db, task_id=int(task_id))
        if row.status == C.REVIEW_REQUEST_OPEN
    ]
    return opens[0] if opens else None


def latest_request_for_task(db: Session, task_id: int) -> ReviewRequest | None:
    rows = review_repo.list_requests(db, task_id=int(task_id))
    return rows[0] if rows else None


# ---------------------------------------------------------------------------
# 发起评审（RV4：必须指定评审人）
# ---------------------------------------------------------------------------


def _active_employee(db: Session, employee_id: int | None, *, role_label: str) -> Employee:
    if employee_id is None:
        raise ReviewError(f"{role_label} is required")
    employee = db.get(Employee, int(employee_id))
    if employee is None:
        raise ReviewError(f"{role_label} not found")
    if str(employee.lifecycle_status) != LifecycleStatus.active.value:
        raise ReviewError(
            f"{role_label} is not active (lifecycle_status={employee.lifecycle_status})"
        )
    return employee


def open_review_request(
    db: Session,
    *,
    task: Task,
    requester_employee_id: int,
    reviewer_employee_id: int,
    reason: str = "",
    decision_id: int | None = None,
    commit: bool = True,
) -> ReviewRequest:
    """发起一次任务级评审（幂等：同一个任务已有的未决定请求会被复用）。

    硬约束：任务必须已经进入 `in_review`；**必须**指定评审人 ——
    系统不替管理层挑人（W1/R2），所以"没人评"不是自动通过的理由。
    """
    if str(task.status) != TaskStatus.in_review.value:
        raise ReviewError(
            f"task {task.id} is {task.status}: only work waiting for review can be reviewed"
        )
    requester = _active_employee(db, requester_employee_id, role_label="requester")
    reviewer = _active_employee(db, reviewer_employee_id, role_label="reviewer")
    if requester.company_id is not None and reviewer.company_id != requester.company_id:
        raise ReviewError("reviewer belongs to another company")

    existing = open_request_for_task(db, int(task.id))
    if existing is not None:
        return existing

    row = review_repo.create_request(
        db,
        company_id=int(requester.company_id or 0),
        project_id=int(task.project_id),
        task_id=int(task.id),
        requested_by_employee_id=int(requester.id),
        reviewer_employee_id=int(reviewer.id),
        status=C.REVIEW_REQUEST_OPEN,
        reason=reason,
        verdict_notes="",
        verdict_decision_id=decision_id,
        requested_at=utcnow(),
    )
    # 事实由**系统**写（RV3）：产物 / 交接 / 会话 / 返工次数都是可复核的事实
    for kind, payload in review_facts.collect_facts(db, task):
        review_repo.add_fact(
            db,
            review_request_id=int(row.id),
            task_id=int(task.id),
            kind=kind,
            payload=payload,
            source=review_facts.FACT_SOURCE,
        )
    if commit:
        db.commit()
    bus.publish(
        "task.review_named",
        {
            "task_id": int(task.id),
            "review_request_id": int(row.id),
            "reviewer_employee_id": int(reviewer.id),
        },
        company_id=int(requester.company_id or 0),
        actor_employee_id=int(requester.id),
        project_id=int(task.project_id),
        task_id=int(task.id),
    )
    return row


# ---------------------------------------------------------------------------
# 给出结论（RV2/RV4/RV5/RV6）
# ---------------------------------------------------------------------------

#: 需要说明理由的结论（"不通过"必须有据可查；PASS 可以只写一句备注）
VERDICTS_REQUIRING_NOTES: frozenset[str] = frozenset(
    {ReviewVerdict.rework.value, ReviewVerdict.rejected.value, ReviewVerdict.escalated.value}
)


def submit_verdict(
    db: Session,
    *,
    request: ReviewRequest,
    reviewer_employee_id: int,
    verdict: ReviewVerdict | str,
    notes: str = "",
    decision_id: int | None = None,
    commit: bool = True,
) -> ReviewRequest:
    """写下一次评审结论，并按**显式映射**落地到任务状态（RV2/RV6）。

    身份（T5 同款纪律）：结论只能由**这条请求指定的评审人**给出 ——
    "谁判断的"不是参数，而是请求里已经定下的人。
    """
    verdict_value = ReviewVerdict(verdict).value
    if str(request.status) != C.REVIEW_REQUEST_OPEN:
        raise ReviewError(
            f"review request {request.id} is {request.status}: verdicts are append-only (RV4)"
        )
    if int(request.reviewer_employee_id) != int(reviewer_employee_id):
        raise ReviewError(
            f"employee {reviewer_employee_id} is not the reviewer of request {request.id}"
        )
    _active_employee(db, reviewer_employee_id, role_label="reviewer")
    if verdict_value in VERDICTS_REQUIRING_NOTES and not notes.strip():
        raise ReviewError(f"{verdict_value} requires notes (为什么判成这样必须可回答)")

    task = project_repo.get_task(db, int(request.task_id))
    if task is None:  # pragma: no cover - 防御
        raise ReviewError(f"task {request.task_id} not found")

    request.verdict = verdict_value
    request.verdict_notes = notes
    request.verdict_decision_id = (
        decision_id if decision_id is not None else (request.verdict_decision_id)
    )
    request.status = C.REVIEW_REQUEST_DECIDED
    request.decided_at = utcnow()

    target = C.REVIEW_VERDICT_TARGETS[verdict_value]
    applied: str | None = None
    if target is not None:
        applied = _apply_target(db, task, target)
    # ESCALATE 没有目标（RV6）：任务**留在原地**，只是多了一条"需要人/管理层"的结论
    if commit:
        db.commit()
    _publish_verdict(db, request=request, task=task, applied=applied, target=target)
    if applied is not None:
        from app.workflow.orchestrator import orchestrator

        if verdict_value == ReviewVerdict.passed.value:
            # PASS ⇒ 任务变 done：既要让下游就绪，还要跑一次**推进**
            # （"全部任务完成 ⇒ 交付"的判断在 `_advance` 里；只发 dispatch 会漏掉它 ——
            #  M2.10 黄金路径实测：最后一个任务是被结论推进 done 的，项目因此停在 in_progress）。
            # `_finalize_external` 只做"反思 + 推进"，不碰状态机，正好是这一步要的语义。
            orchestrator.notify({"type": "task_finished", "task_id": int(task.id), "success": True})
        else:
            # REWORK / REJECT：任务回到可执行/终态，交给调度器重新安排
            orchestrator.notify({"type": "dispatch"})
    return request


def _apply_target(db: Session, task: Task, target: str) -> str:
    """把结论映射到状态迁移（**只走状态机**，不越权改别的列）。"""
    current = str(task.status)
    if target == TaskStatus.todo.value:
        # REWORK：in_review → rejected → todo（两步，状态机不允许跳步）+ 计数器 +1
        if current == TaskStatus.in_review.value:
            task_service.transition_task(db, task, TaskStatus.rejected.value)
        task_service.transition_task(db, task, TaskStatus.todo.value)
        task.rework_count = int(task.rework_count or 0) + 1
        db.flush()
        return TaskStatus.todo.value
    if target == TaskStatus.rejected.value:
        task_service.transition_task(db, task, TaskStatus.rejected.value)
        return TaskStatus.rejected.value
    if target == TaskStatus.done.value:
        task_service.transition_task(db, task, TaskStatus.done.value)
        return TaskStatus.done.value
    raise ReviewError(f"no explicit mapping for target {target}")  # pragma: no cover


def _publish_verdict(
    db: Session, *, request: ReviewRequest, task: Task, applied: str | None, target: str | None
) -> None:
    """结论落地后的事实通告（既有封闭事件集里的成员，不新增第三套语义）。"""
    project = project_repo.get_project(db, int(request.project_id))
    company_id = int(project.company_id) if project else 0
    if request.verdict == ReviewVerdict.passed.value:
        bus.publish(
            "task.review_passed",
            {
                "id": int(task.id),
                "task_id": int(task.id),
                "review_request_id": int(request.id),
                "verdict": request.verdict,
                "reviewer_employee_id": int(request.reviewer_employee_id),
                "applied": applied,
            },
            company_id=company_id,
            actor_employee_id=int(request.reviewer_employee_id),
            project_id=int(request.project_id),
            task_id=int(task.id),
        )
        return
    if request.verdict == ReviewVerdict.escalated.value:
        # 评审人判定不了 ⇒ 需要人/管理层（系统不猜，也不自动处理，RV6）
        bus.publish(
            "task.review_required",
            {
                "id": int(task.id),
                "task_id": int(task.id),
                "review_request_id": int(request.id),
                "verdict": request.verdict,
                "reviewer_employee_id": int(request.reviewer_employee_id),
                "escalated": True,
                "target_status": target,
            },
            company_id=company_id,
            actor_employee_id=int(request.reviewer_employee_id),
            project_id=int(request.project_id),
            task_id=int(task.id),
        )
        return
    bus.publish(
        "task.review_failed",
        {
            "id": int(task.id),
            "task_id": int(task.id),
            "review_request_id": int(request.id),
            "verdict": request.verdict,
            "notes": request.verdict_notes,
            "reviewer_employee_id": int(request.reviewer_employee_id),
            "applied": applied,
            "rework_count": int(task.rework_count or 0),
        },
        company_id=company_id,
        actor_employee_id=int(request.reviewer_employee_id),
        project_id=int(request.project_id),
        task_id=int(task.id),
    )


# ---------------------------------------------------------------------------
# 重新规划（RV7：只有该项目的 Manager 能发起）
# ---------------------------------------------------------------------------


def replan_project(
    db: Session,
    *,
    project: Project,
    actor_employee_id: int,
    reason: str,
    decision_id: int | None = None,
    commit: bool = True,
) -> Project:
    """把项目退回"等管理层重新规划"（**不生成任何图**，W2/W34）。

    权限：actor **必须**是这个项目当前的 Manager（`management_employee_id`）。
    其他角色即使有 `plan_project_work` 授权也不能替它 replan —— 那是这个项目的
    Manager 的判断（设计 §12.2）。
    """
    if not reason.strip():
        raise ReviewError("a replan must state a reason")
    if project.management_employee_id is None:
        raise ReviewError("project has no manager: replan requires an accountable manager")
    if int(project.management_employee_id) != int(actor_employee_id):
        raise ReviewError(
            f"employee {actor_employee_id} is not the manager of project {project.id} "
            "(replan is the project manager's call)"
        )
    _active_employee(db, actor_employee_id, role_label="manager")
    if str(project.status) in {
        ProjectStatus.completed.value,
        ProjectStatus.cancelled.value,
    }:
        raise ReviewError(
            f"project {project.id} is {project.status}: cannot replan a closed project"
        )

    project.status = ProjectStatus.planning.value
    if commit:
        db.commit()
    bus.publish(
        "project.replan_required",
        {
            "id": int(project.id),
            "name": project.name,
            "company_id": int(project.company_id),
            "project_id": int(project.id),
            "work_mode": project.work_mode,
            "management_employee_id": project.management_employee_id,
            "reasons": ["manager_requested_replan"],
            "reason": reason,
            "decision_id": decision_id,
            "needs_management": True,
        },
        company_id=int(project.company_id),
        actor_employee_id=int(actor_employee_id),
        project_id=int(project.id),
    )
    return project


__all__ = [
    "ReviewError",
    "ReviewFactView",
    "ReviewView",
    "VERDICTS_REQUIRING_NOTES",
    "latest_request_for_task",
    "open_request_for_task",
    "open_review_request",
    "replan_project",
    "review_view",
    "submit_verdict",
]
