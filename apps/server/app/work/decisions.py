"""M2.4 **决策服务**：Decision Envelope 的执行者 + 决策读模型（DR1–DR10）。

一条决策 = **一个 DecisionRecord + N 个 ToolAudit**（DR2）。Agent 一次提交信封：

```text
{
  decision_type, reason, intended_outcome, scope,
  context,            # 有界事实快照（+ 稳定引用）
  actions: [ {tool, args}, … ],
  parent_decision_id  # 可选：管理决策树（DR8）
}
```

执行顺序（与用户拍板 §3 一致）：

```text
① 校验信封（结构，不评价内容 —— validate_decision_intent）
② 落 DecisionRecord（status=PROPOSED）→ 拿到 decision_id
③ 逐个执行 actions，每个 ToolAudit 自动挂 decision_id（DR3）
④ 聚合结果 → APPLIED / PARTIALLY_APPLIED / FAILED（DR6）
⑤ 写 resolved_at + outcome_note（追加式推进，不重写语义字段）
```

**决策不授予权限**（DR4）：每个动作在执行面重新走
`Actor → 任职 → AuthorityGrant → Scope → Domain Validation`。
`DecisionRecord.authority_json` 只是**当时的授权快照**（证据），不是通行证。

**原子性**（用户拍板 §9）：短决策（建任务 + 连依赖 + 派活）走同一个会话、逐个动作提交；
长生命周期决策（plan → execute → review → replan）用
`open_decision()` 先落 PROPOSED，之后分阶段 `execute_actions()` ——
**不持有长事务**。`PARTIALLY_APPLIED` 正是为这种情况准备的诚实状态。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.base import utcnow
from app.models.decision import DecisionRecord, ToolAudit
from app.models.enums import DecisionStatus
from app.models.organization import Employee
from app.repositories import position as position_repo
from app.work import authority as authority_service
from app.work import contracts as C
from app.work import tool_executor as executor
from app.work import tools as tool_machinery


class DecisionError(ValueError):
    """决策信封非法（结构问题）。系统**不**评价决策内容（W18）。"""


#: 成功执行的动作结果码（其余都算"没生效"）
_APPLIED_OUTCOMES = frozenset({"applied"})


# ---------------------------------------------------------------------------
# 上下文快照（DR9）：有界键集 + 稳定引用 + 哈希
# ---------------------------------------------------------------------------


def build_context(db: Session, ctx: tool_machinery.ToolCallContext, raw: Mapping | None) -> dict:
    """构造**有界**决策上下文（DR9）。

    键集封闭（`DECISION_CONTEXT_KEYS`）：不认识的键一律拒绝 ——
    "顺手把整张表塞进 JSON" 会让决策上下文变成数据库副本，也让哈希失去意义。
    缺失的键不补默认值（补了就等于编造"当时我看到过这个事实"）。
    """
    payload = dict(raw or {})
    unknown = sorted(set(payload) - C.DECISION_CONTEXT_KEYS)
    if unknown:
        raise DecisionError(
            f"unknown decision context keys: {unknown} (allowed: {sorted(C.DECISION_CONTEXT_KEYS)})"
        )
    # 授权快照由系统补（Agent 不需要自报，也无法伪造）
    payload["authority"] = authority_service.authority_snapshot_for(db, ctx.employee_id)
    payload.setdefault("project_id", ctx.project_id)
    if payload.get("project_id") is None:
        payload.pop("project_id", None)
    return payload


def context_hash(context: Mapping) -> str:
    """上下文摘要：跨进程稳定（排序 + 去时间），可用于"当时依据的事实没变"的核对。"""
    payload = json.dumps(context, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 决策行（写入）
# ---------------------------------------------------------------------------


def _actor_position(db: Session, employee_id: int) -> dict:
    """决策当时的职位 / 任职快照（DR9 的"稳定引用"）。

    **任职行 id** 是关键：换人之后历史仍指向"当时那一段任职"，
    而不是拿今天的组织去解释昨天的决定。
    """
    actor = authority_service.resolve_actor_authority(db, employee_id)
    active = position_repo.active_primary_assignment(db, int(employee_id))
    return {
        "acting_position_assignment_id": int(active.id) if active is not None else None,
        "acting_position_definition_id": actor.position_definition_id,
        "acting_position_code": actor.position_code,
    }


def open_decision(
    db: Session,
    ctx: tool_machinery.ToolCallContext,
    *,
    decision_type: C.DecisionKind | str,
    reason: str,
    scope: str,
    intended_outcome: str = "",
    context: Mapping | None = None,
    parent_decision_id: int | None = None,
    commit: bool = True,
) -> DecisionRecord:
    """登记一条决策（status=PROPOSED）—— 信封的第一阶段，也是长决策的第一步。

    这里做的**只有**结构校验与留档：不评价理由、不判断意图、不预演动作。
    """
    kind = (
        decision_type
        if isinstance(decision_type, C.DecisionKind)
        else C.DecisionKind(str(decision_type))
    )
    employee = db.get(Employee, int(ctx.employee_id))
    if employee is None:
        raise DecisionError("actor employee not found")
    snapshot = build_context(db, ctx, context)
    # 人级口径（R1）：决策记录要走人级口径；兼容期仍可能存在 person_id 为空的历史员工，
    # 那种情况下**如实留痕**（`actor_person_id` 与 employee 对齐），而不是编一个 person。
    person_id = ctx.person_id or employee.person_id or int(ctx.employee_id)
    intent = C.DecisionIntent(
        actor_person_id=int(person_id),
        acting_employee_id=int(ctx.employee_id),
        decision=kind,
        scope=scope,
        reason=reason,
        intended_outcome=intended_outcome,
        context_snapshot=snapshot,
        parent_decision_id=parent_decision_id,
    )
    C.validate_decision_intent(intent)

    scope_kind, _, scope_id = scope.partition(":")
    project_id = ctx.project_id
    task_id = ctx.task_id
    if scope_kind == "project" and scope_id.isdigit():
        project_id = int(scope_id)
    elif scope_kind == "task" and scope_id.isdigit():
        task_id = int(scope_id)

    row = DecisionRecord(
        company_id=int(ctx.company_id),
        actor_employee_id=int(ctx.employee_id),
        actor_person_id=ctx.person_id or snapshot.get("authority", {}).get("actor_person_id"),
        scope=scope,
        project_id=project_id,
        task_id=task_id,
        decision_type=kind.value,
        reason=reason,
        intended_outcome=intended_outcome,
        context_json=snapshot,
        context_hash=context_hash(snapshot),
        context_version=C.DECISION_CONTEXT_VERSION,
        authority_json=dict(snapshot.get("authority") or {}),
        status=DecisionStatus.proposed.value,
        parent_decision_id=parent_decision_id,
        **_actor_position(db, int(ctx.employee_id)),
    )
    db.add(row)
    db.flush()
    if commit:
        db.commit()
    return row


# ---------------------------------------------------------------------------
# 动作执行 + 状态聚合
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ActionOutcome:
    """一个动作的执行结果（决策层看到的形状；细节在 ToolAudit）。"""

    tool: str
    ok: bool
    reason: str
    audit_id: int | None
    result_ref: str = ""


@dataclass(frozen=True)
class DecisionEnvelopeResult:
    """一次信封提交的完整结果。"""

    decision_id: int
    status: DecisionStatus
    outcomes: tuple[ActionOutcome, ...] = ()
    applied: int = 0
    failed: int = 0

    @property
    def ok(self) -> bool:
        return self.status is DecisionStatus.applied


def _result_ref(tool: str, data: Mapping) -> str:
    """从工具结果里提炼一个稳定引用（"task:34" / "project:12"）。"""
    for key, prefix in (("task", "task"), ("project", "project")):
        payload = data.get(key)
        if isinstance(payload, Mapping):
            identifier = payload.get(f"{prefix}_id") or payload.get("id")
            if identifier is not None:
                return f"{prefix}:{identifier}"
    identifier = data.get("task_id")
    return f"task:{identifier}" if identifier is not None else ""


def execute_actions(
    db: Session,
    decision: DecisionRecord,
    actions: Sequence[Mapping],
    ctx: tool_machinery.ToolCallContext,
    *,
    transport=executor.ToolTransport.internal,
    commit: bool = True,
) -> tuple[ActionOutcome, ...]:
    """逐个执行信封里的动作，每个 `ToolAudit` 自动挂 `decision_id`（DR3/DR7）。

    **不做事务包裹**（用户拍板 §9）：每个动作由执行面带自己的领域事务提交；
    决策的状态是**执行完之后按事实聚合**出来的（DR6），而不是事先假定全部成功。
    """
    if decision.status in (DecisionStatus.applied.value, DecisionStatus.superseded.value):
        raise DecisionError(f"decision {decision.id} is already {decision.status}")
    decision.status = DecisionStatus.executing.value
    db.flush()

    outcomes: list[ActionOutcome] = []
    for raw in actions:
        spec_name = str(raw.get("tool") or "")
        args = dict(raw.get("args") or {})
        result = executor.execute_tool(
            db,
            name=spec_name,
            args=args,
            context=ctx,
            transport=transport,
            decision_id=int(decision.id),
            commit=False,
        )
        outcomes.append(
            ActionOutcome(
                tool=spec_name,
                ok=result.ok,
                reason=result.reason,
                audit_id=result.audit_id,
                result_ref=_result_ref(spec_name, result.data or {}),
            )
        )
    if commit:
        db.commit()
    return tuple(outcomes)


def resolve_status(outcomes: Sequence[ActionOutcome]) -> DecisionStatus:
    """按事实聚合状态（DR6）：全成 → APPLIED；全败 → FAILED；其余 → PARTIALLY_APPLIED。"""
    if not outcomes:
        return DecisionStatus.failed
    applied = sum(1 for item in outcomes if item.ok)
    if applied == len(outcomes):
        return DecisionStatus.applied
    if applied == 0:
        return DecisionStatus.failed
    return DecisionStatus.partially_applied


def resolve_decision(
    db: Session,
    decision: DecisionRecord,
    outcomes: Sequence[ActionOutcome],
    *,
    note: str = "",
    commit: bool = True,
) -> DecisionRecord:
    """把聚合结果写回决策行（追加式推进，不重写语义字段）。"""
    status = resolve_status(outcomes)
    decision.status = status.value
    decision.resolved_at = utcnow()
    decision.outcome_note = note or (
        f"{sum(1 for item in outcomes if item.ok)}/{len(outcomes)} actions applied"
    )
    db.flush()
    if commit:
        db.commit()
    return decision


def submit_envelope(
    db: Session,
    ctx: tool_machinery.ToolCallContext,
    *,
    decision_type: C.DecisionKind | str,
    reason: str,
    scope: str,
    actions: Iterable[Mapping],
    intended_outcome: str = "",
    context: Mapping | None = None,
    parent_decision_id: int | None = None,
    transport=executor.ToolTransport.internal,
) -> DecisionEnvelopeResult:
    """**一次提交**：决策 + N 个动作（用户拍板 §3 的 Decision Envelope）。

    Agent 不需要 `submit_decision()` 之后再逐个调工具 —— 那样只是无意义的仪式。
    """
    materialized = list(actions)
    if not materialized:
        raise DecisionError("a decision envelope must carry at least one action")
    # **先落意图、再执行动作**：决策行独立成一个短事务，动作各自由执行面提交。
    # 这样即便进程在动作中途崩掉，也已经留下"某人在某时提出了这个决定"的事实
    # （状态停在 EXECUTING，`PARTIALLY_APPLIED` 正是为这种局面准备的，DR6）。
    decision = open_decision(
        db,
        ctx,
        decision_type=decision_type,
        reason=reason,
        scope=scope,
        intended_outcome=intended_outcome,
        context=context,
        parent_decision_id=parent_decision_id,
        commit=True,
    )
    outcomes = execute_actions(db, decision, materialized, ctx, transport=transport, commit=False)
    resolve_decision(db, decision, outcomes, commit=False)
    db.commit()
    applied = sum(1 for item in outcomes if item.ok)
    return DecisionEnvelopeResult(
        decision_id=int(decision.id),
        status=DecisionStatus(decision.status),
        outcomes=outcomes,
        applied=applied,
        failed=len(outcomes) - applied,
    )


def supersede(db: Session, decision: DecisionRecord, *, by_decision_id: int) -> DecisionRecord:
    """标记"被后续决策取代"（例如 replan）——原记录**不改写**，只记谁取代了它（W28/DR8）。"""
    decision.status = DecisionStatus.superseded.value
    decision.superseded_by_id = int(by_decision_id)
    decision.resolved_at = decision.resolved_at or utcnow()
    db.commit()
    return decision


# ---------------------------------------------------------------------------
# 读模型（决策 + 执行事实；**反查**而不是存第二份关系，DR3）
# ---------------------------------------------------------------------------


def list_decisions(
    db: Session,
    company_id: int,
    *,
    project_id: int | None = None,
    task_id: int | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[DecisionRecord]:
    stmt = (
        select(DecisionRecord)
        .where(DecisionRecord.company_id == int(company_id))
        .order_by(DecisionRecord.id.desc())
    )
    if project_id is not None:
        stmt = stmt.where(DecisionRecord.project_id == int(project_id))
    if task_id is not None:
        stmt = stmt.where(DecisionRecord.task_id == int(task_id))
    if status is not None:
        stmt = stmt.where(DecisionRecord.status == status)
    return list(db.scalars(stmt.limit(max(1, limit)).offset(max(0, offset))))


def tool_audits_for(db: Session, decision_id: int) -> list[ToolAudit]:
    """反查某条决策的执行事实（DR3：唯一方向）。"""
    return list(
        db.scalars(
            select(ToolAudit)
            .where(ToolAudit.decision_id == int(decision_id))
            .order_by(ToolAudit.id)
        )
    )


def action_summary(db: Session, decision_id: int) -> dict:
    """派生计数（**不落列** —— 存下来就是第二份真相，DR5）。"""
    rows = tool_audits_for(db, decision_id)
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.outcome] = counts.get(row.outcome, 0) + 1
    return {
        "total": len(rows),
        "applied": counts.get("applied", 0),
        "by_outcome": counts,
    }


def child_decisions(db: Session, decision_id: int) -> list[DecisionRecord]:
    return list(
        db.scalars(
            select(DecisionRecord)
            .where(DecisionRecord.parent_decision_id == int(decision_id))
            .order_by(DecisionRecord.id)
        )
    )


def decision_view(db: Session, decision: DecisionRecord) -> dict:
    """决策读面：管理语义 + 派生动作计数 + 执行事实的**键级**摘要。

    只给 `ToolAudit` 的定位信息（tool/outcome/时间/audit_id），
    完整入参出参走 `tool_audits_for()` —— 决策读面不该变成审计转储。
    """
    rows = tool_audits_for(db, int(decision.id))
    return {
        "decision_id": int(decision.id),
        "company_id": int(decision.company_id),
        "actor_person_id": decision.actor_person_id,
        "actor_employee_id": int(decision.actor_employee_id),
        "acting_position_assignment_id": decision.acting_position_assignment_id,
        "acting_position_definition_id": decision.acting_position_definition_id,
        "acting_position_code": decision.acting_position_code,
        "decision_type": decision.decision_type,
        "scope": decision.scope,
        "project_id": decision.project_id,
        "task_id": decision.task_id,
        "reason": decision.reason,
        "intended_outcome": decision.intended_outcome,
        "status": decision.status,
        "outcome_note": decision.outcome_note,
        "parent_decision_id": decision.parent_decision_id,
        "superseded_by_id": decision.superseded_by_id,
        "context_version": int(decision.context_version),
        "context_hash": decision.context_hash,
        "context": dict(decision.context_json or {}),
        "authority_at_decision": dict(decision.authority_json or {}),
        "action_summary": action_summary(db, int(decision.id)),
        "actions": [
            {
                "audit_id": int(row.id),
                "tool_name": row.tool_name,
                "outcome": row.outcome,
                "decision_semantics": row.decision_semantics,
                "authority_allowed": row.authority_allowed,
                "created_at": row.created_at,
            }
            for row in rows
        ],
        "child_decision_ids": [int(row.id) for row in child_decisions(db, int(decision.id))],
        "created_at": decision.created_at,
        "resolved_at": decision.resolved_at,
    }


def decision_stats(db: Session, company_id: int) -> dict:
    """决策观测（只读）：按状态计数 + 动作执行事实分布。"""
    by_status = dict(
        db.execute(
            select(DecisionRecord.status, func.count(DecisionRecord.id))
            .where(DecisionRecord.company_id == int(company_id))
            .group_by(DecisionRecord.status)
        ).all()
    )
    by_outcome = dict(
        db.execute(
            select(ToolAudit.outcome, func.count(ToolAudit.id))
            .where(ToolAudit.actor_company_id == int(company_id))
            .group_by(ToolAudit.outcome)
        ).all()
    )
    semantics = dict(
        db.execute(
            select(ToolAudit.decision_semantics, func.count(ToolAudit.id))
            .where(ToolAudit.actor_company_id == int(company_id))
            .group_by(ToolAudit.decision_semantics)
        ).all()
    )
    return {
        "decisions_by_status": {str(k): int(v) for k, v in by_status.items()},
        "tool_audits_by_outcome": {str(k): int(v) for k, v in by_outcome.items()},
        "tool_audits_by_decision_semantics": {str(k): int(v) for k, v in semantics.items()},
    }


__all__ = [
    "DecisionError",
    "ActionOutcome",
    "DecisionEnvelopeResult",
    "build_context",
    "context_hash",
    "open_decision",
    "execute_actions",
    "resolve_status",
    "resolve_decision",
    "submit_envelope",
    "supersede",
    "list_decisions",
    "tool_audits_for",
    "action_summary",
    "child_decisions",
    "decision_view",
    "decision_stats",
]
