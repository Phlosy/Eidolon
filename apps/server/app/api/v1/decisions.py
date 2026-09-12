"""/decisions —— 管理决策的**只读**读面（M2.4）。

三层里的第一层（管理语义）+ 执行事实的**定位信息**：

| 端点 | 回答 |
| --- | --- |
| `GET /decisions` | 本公司有哪些决策（可按 project / task / status 过滤）|
| `GET /decisions/{id}` | 这条决策是谁、为什么、什么状态、由哪些动作执行 |
| `GET /decisions/{id}/tool-audits` | 为执行它，系统**实际执行了什么**（完整入参出参）|
| `GET /decisions/stats` | 决策与执行事实的分布（观测）|

**没有写端点**：决策由管理 Agent 在执行面提交（Decision Envelope，见
`app/work/decisions.py::submit_envelope`）；人类不直接写决策记录 ——
人类的管理动作走各领域正式 API（`/projects` `/tasks` …），与 Agent 共用同一批 domain service。

**不评价决策内容**：读面里没有"决策质量/打分/建议"这类字段（W18 / DR10）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.scope import resolve_company_id
from app.core.database import get_db
from app.models.decision import DecisionRecord
from app.schemas.work import (
    DecisionActionRefOut,
    DecisionActionSummaryOut,
    DecisionOut,
    DecisionStatsOut,
    ToolAuditOut,
)
from app.work import decisions as decision_service

router = APIRouter(prefix="/decisions", tags=["decisions"])


def _company_or_404(company_id: int | None) -> int:
    if company_id is None:
        raise HTTPException(status_code=404, detail="company not found")
    return int(company_id)


def _decision_or_404(db: Session, company_id: int, decision_id: int) -> DecisionRecord:
    row = db.get(DecisionRecord, int(decision_id))
    if row is None or int(row.company_id) != int(company_id):
        raise HTTPException(status_code=404, detail="decision not found")
    return row


def _decision_out(db: Session, row: DecisionRecord) -> dict:
    return decision_service.decision_view(db, row)


@router.get("", response_model=list[DecisionOut])
def list_decisions(
    project_id: int | None = Query(default=None),
    task_id: int | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> list[dict]:
    current = _company_or_404(company_id)
    rows = decision_service.list_decisions(
        db,
        current,
        project_id=project_id,
        task_id=task_id,
        status=status,
        limit=limit,
        offset=offset,
    )
    return [_decision_out(db, row) for row in rows]


@router.get("/stats", response_model=DecisionStatsOut)
def decision_stats(
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    return decision_service.decision_stats(db, _company_or_404(company_id))


@router.get("/{decision_id}", response_model=DecisionOut)
def get_decision(
    decision_id: int,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    return _decision_out(db, _decision_or_404(db, _company_or_404(company_id), decision_id))


@router.get("/{decision_id}/tool-audits", response_model=list[ToolAuditOut])
def list_decision_tool_audits(
    decision_id: int,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> list[ToolAuditOut]:
    """反查这条决策的执行事实（`ToolAudit.decision_id` 是唯一关联方向，DR3）。"""
    row = _decision_or_404(db, _company_or_404(company_id), decision_id)
    return [
        ToolAuditOut(
            audit_id=int(audit.id),
            tool_name=audit.tool_name,
            decision_id=audit.decision_id,
            outcome=audit.outcome,
            side_effect=audit.side_effect,
            decision_semantics=audit.decision_semantics,
            autonomy=audit.autonomy,
            transport=audit.transport,
            origin=audit.origin,
            actor_employee_id=audit.actor_employee_id,
            actor_person_id=audit.actor_person_id,
            actor_company_id=audit.actor_company_id,
            work_session_id=audit.work_session_id,
            task_id=audit.task_id,
            project_id=audit.project_id,
            arguments=dict(audit.arguments_json or {}),
            arguments_digest=audit.arguments_digest,
            authority_allowed=audit.authority_allowed,
            authority_reason=audit.authority_reason,
            authority_grant_ids=list(audit.authority_grant_ids or []),
            authority_grants_hash=audit.authority_grants_hash,
            result=dict(audit.result_json) if audit.result_json else None,
            error=audit.error,
            started_at=audit.started_at,
            finished_at=audit.finished_at,
        )
        for audit in decision_service.tool_audits_for(db, int(row.id))
    ]


# `DecisionActionRefOut` / `DecisionActionSummaryOut` 由 `DecisionOut` 组装使用；
# 这里显式引用一次，避免"导入了没用"的告警，也说明它们确实在校验链上。
__all__ = ["router", "DecisionActionRefOut", "DecisionActionSummaryOut"]
