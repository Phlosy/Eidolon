"""Evidence Reconciliation —— 事件丢失也能重建（docs/evidence-pipeline.md §十八）。

重扫业务事实（任务/评审/技能使用/学习记录）→ Collector 解释 → Normalizer 幂等落库。
同一条事实反复 reconcile 不会产生重复证据（dedup key 见 normalize）。
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.evidence import normalize
from app.evidence.collectors import (
    REGISTRY,
    ReviewCollector,
    SkillUsageCollector,
    WorkItemCollector,
)
from app.models.knowledge import SkillUsage
from app.models.project import Task
from app.models.project_delivery import ReviewMeeting

logger = logging.getLogger(__name__)


def _upsert_candidates(db: Session, candidates) -> dict[str, int]:
    created = updated = 0
    for candidate in candidates:
        try:
            _row, is_new = normalize.upsert_evidence(db, candidate)
        except ValueError:
            continue  # 源不存在/越界：记录但不停整个 reconcile
        if is_new:
            created += 1
        else:
            updated += 1
    return {"created": created, "updated": updated}


def reconcile_task(db: Session, task_id: int) -> dict[str, int]:
    task = db.get(Task, task_id)
    if task is None:
        return {"created": 0, "updated": 0}
    collector: WorkItemCollector = REGISTRY.get("task")  # type: ignore[assignment]
    return _upsert_candidates(db, collector.collect(db, {"task_id": task.id}))


def reconcile_employee(db: Session, employee_id: int) -> dict[str, int]:
    """重扫一名员工的全部可解释事实（幂等）。"""
    totals = {"created": 0, "updated": 0}

    tasks = db.scalars(
        select(Task).where(
            Task.assignee_id == employee_id,
            Task.status.in_(["done", "failed"]),
        )
    ).all()
    task_collector: WorkItemCollector = REGISTRY.get("task")  # type: ignore[assignment]
    for task in tasks:
        _merge(totals, _upsert_candidates(db, task_collector.collect(db, {"task_id": task.id})))

    reviews = db.scalars(
        select(ReviewMeeting).where(
            ReviewMeeting.presenter_employee_id == employee_id,
            ReviewMeeting.decision.isnot(None),
        )
    ).all()
    review_collector: ReviewCollector = REGISTRY.get("review")  # type: ignore[assignment]
    for review in reviews:
        _merge(
            totals,
            _upsert_candidates(db, review_collector.collect(db, {"review_id": review.id})),
        )

    usages = db.scalars(
        select(SkillUsage).where(
            SkillUsage.employee_id == employee_id,
            SkillUsage.outcome.isnot(None),
        )
    ).all()
    skill_collector: SkillUsageCollector = REGISTRY.get("skill_usage")  # type: ignore[assignment]
    for usage in usages:
        _merge(totals, _upsert_candidates(db, skill_collector.collect(db, {"usage_id": usage.id})))

    return totals


def reconcile_project(db: Session, project_id: int) -> dict[str, int]:
    """重扫项目内所有相关员工（项目结束/补数的入口）。"""
    from app.models.project import Task as TaskModel

    employee_ids = set(
        db.scalars(
            select(TaskModel.assignee_id).where(
                TaskModel.project_id == project_id,
                TaskModel.assignee_id.isnot(None),
            )
        ).all()
    )
    employee_ids.update(
        db.scalars(
            select(ReviewMeeting.presenter_employee_id).where(
                ReviewMeeting.project_id == project_id,
                ReviewMeeting.presenter_employee_id.isnot(None),
            )
        ).all()
    )
    totals = {"created": 0, "updated": 0}
    for employee_id in employee_ids:
        _merge(totals, reconcile_employee(db, int(employee_id)))
    return totals


def _merge(totals: dict[str, int], partial: dict[str, int]) -> None:
    totals["created"] += partial["created"]
    totals["updated"] += partial["updated"]
