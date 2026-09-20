"""M2.7 **评审事实**收集器（W18 / RV1 / RV3）。

系统在这里提供的是**事实**，不是判断：

```text
✅ "这次任务产出了 2 个交付物（source_code / test_report）"
✅ "任务声明要产出 release，实际没产出"
✅ "上次会话 completed，用时 12.3s"
❌ "代码质量不错"     ❌ "通过"     ❌ "建议返工"
```

判断由 **Reviewer Agent** 给出（`ReviewVerdict`，见 `app/work/reviews.py`）。
两者的落点也不同：事实写 `review_facts`（系统写的），结论写
`review_requests.verdict`（Agent 写的）—— 分开存，谁写的可回答（H6/RV3）。

**刻意不提供**：`lint` / `build` / CI 结果。本仓库没有可观察的运行器与产物，
列出它们只会让"系统提供的事实"变成一句空话。宁可少列（设计的少而真原则）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from app.repositories import drive as drive_repo
from app.work import artifact_versions, handoff, provenance
from app.work import contracts as C

if TYPE_CHECKING:  # pragma: no cover - 仅为类型
    from app.models.project import Task

FACT_SOURCE = "app.work.review_facts"


def _session_fact(db: Session, task: Task) -> dict:
    """上次会话的结果（**runtime 的事实**，不是评审结论）。"""
    session = artifact_versions.latest_worker(db, int(task.id))
    if session is None:
        return {"present": False}
    cost = session.cost or {}
    return {
        "present": True,
        "work_session_id": int(session.id),
        "status": session.status,
        "employee_id": int(session.employee_id) if session.employee_id else None,
        "duration_sec": cost.get("duration_sec"),
        "summary": session.summary or "",
        "error": session.error or "",
        "started_at": session.started_at.isoformat() if session.started_at else None,
        "ended_at": session.ended_at.isoformat() if session.ended_at else None,
        "provenance": provenance.read(session),
    }


def _artifacts_fact(db: Session, task: Task) -> dict:
    produced = handoff.produced_artifacts(db, int(task.id))
    return {
        "count": len(produced),
        "items": [
            {
                "artifact_id": item.artifact_id,
                "title": item.title,
                "doc_type": item.doc_type,
                "version": item.version,
                "sha256": item.sha256,
                "revision_id": item.revision_id,
            }
            for item in produced
        ],
    }


def _produces_gap_fact(db: Session, task: Task) -> dict:
    """声明的预期产出 vs 实际产出（**差异是事实**，好坏由人来判断）。"""
    declared = set(task.produces_json or ())
    produced = {item.doc_type for item in handoff.produced_artifacts(db, int(task.id))}
    return {
        "declared": sorted(declared),
        "produced": sorted(produced),
        "declared_not_produced": sorted(declared - produced),
        "produced_not_declared": sorted(produced - declared),
        "declaration_present": bool(declared),
    }


def _inputs_fact(db: Session, task: Task) -> dict:
    session = artifact_versions.latest_worker(db, int(task.id))
    if session is not None:
        items = []
        for used in handoff.consumed_artifacts(db, int(task.id)):
            if used.work_session_id != session.id:
                continue
            node = drive_repo.get_node(db, used.artifact_id)
            items.append(
                {
                    "artifact_id": used.artifact_id,
                    "source_task_id": node.task_id or 0,
                    "doc_type": used.doc_type,
                    "revision_id": used.revision_id,
                    "version": used.version,
                    "sha256": used.sha256,
                }
            )
        return {"count": len(items), "items": items}
    inputs = handoff.resolve_input_artifacts(db, task)
    return {
        "count": len(inputs),
        "items": [
            {
                "artifact_id": item.artifact_id,
                "source_task_id": item.source_task_id,
                "doc_type": item.doc_type,
                "revision_id": item.revision_id,
                "version": item.version,
                "sha256": item.sha256,
            }
            for item in inputs
        ],
    }


def collect_facts(db: Session, task: Task) -> list[tuple[str, dict]]:
    """收集一个任务的全部评审事实（kind → payload）。**不做任何判断。**"""
    return [
        ("artifacts", _artifacts_fact(db, task)),
        ("produces_gap", _produces_gap_fact(db, task)),
        (
            "acceptance_criteria",
            {
                "present": bool((task.acceptance_criteria or "").strip()),
                "text": task.acceptance_criteria or "",
            },
        ),
        ("inputs", _inputs_fact(db, task)),
        ("session", _session_fact(db, task)),
        ("rework", {"rework_count": int(task.rework_count or 0)}),
    ]


def fact_kinds() -> tuple[str, ...]:
    return C.REVIEW_FACT_KINDS


__all__ = ["FACT_SOURCE", "collect_facts", "fact_kinds"]
