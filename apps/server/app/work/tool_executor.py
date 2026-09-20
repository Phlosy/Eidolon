"""M2.3 起的**工具执行面**（T4 / T5 / T7 / T10 / T12 + M2.4 的 DR1–DR10）。

一次调用固定走六步，顺序不可交换：

```text
① 解析 spec            —— 未注册的工具直接拒绝
② 参数校验             —— 含"身份字段不得出现在参数里"（T5）
③ Authority 校验       —— 内部面**同样**做；default-deny，随任职生效失效（T4 / T8）
③b 决策语义门禁        —— required 必须在决策信封内；none 不许挂 decision_id（DR7）
④ Autonomy 门禁        —— 该副作用等级当前是否允许无人确认执行（用户拍板 §11）
⑤ 应用 + 执行事实留档  —— 调既有领域 service；每次调用写一条 `tool_audits`（T12 / DR1）
```

`decision_id` 只是**执行事实的一个字段**（DR3）：它把这次调用挂到某条决策上，
本身**不提供任何权限** —— 授权每一步都重新算（DR4）。

**Transport 不代表信任**（T10）：`transport` 只影响"这个到达方式是否被允许"，
不影响任何领域不变量；`debug_cli` 还额外要求显式开关（默认关）。

**执行面不产生管理判断**（T6 / W39）：它只回答"在不在授权内、领域约束过不过"，
从不回答"该不该做这件事"—— 那是 Agent 的决定（T7）。
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.transaction_effects import ensure_sqlite_transaction
from app.models.base import as_utc, utcnow
from app.models.decision import DecisionRecord, ToolAudit
from app.models.drive import DriveNode
from app.models.enums import (
    AutonomyLevel,
    DecisionSemantics,
    LifecycleStatus,
    ProjectStatus,
    ReviewVerdict,
    TaskStatus,
    ToolTransport,
)
from app.models.organization import Company, Employee
from app.models.person import Person
from app.models.project import Project, Task, WorkSession
from app.models.review import ReviewRequest
from app.models.runtime_operation import RuntimeOperation
from app.work import authority as authority_service
from app.work import contracts as C
from app.work import tool_reads, tool_writes
from app.work import tools as tool_machinery
from app.work.dispatch import EXECUTABLE_PROJECT_STATUSES

logger = logging.getLogger("eidolon.work.tools")


class ToolExecutionError(RuntimeError):
    """执行被拒绝（不是 handler 的业务失败）。`reason` 是机器可读原因码。"""

    def __init__(self, reason: str, message: str = "", *, http_status: int = 400) -> None:
        super().__init__(message or reason)
        self.reason = reason
        self.http_status = http_status


@dataclass(frozen=True)
class ToolResult:
    """一次工具调用的**完整**结果（T7）。

    `ok=True` 的语义是：Agent 决定 → 系统校验通过 → 系统已应用。
    因此成功结果里必然带 `authority`（凭什么）与 `audit_id`（可追溯，T12）。
    """

    ok: bool
    tool: str
    side_effect: str
    autonomy: str
    transport: str
    data: dict = field(default_factory=dict)
    reason: str = ""
    error: str = ""
    authority: dict | None = None
    audit_id: int | None = None
    actor: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "tool": self.tool,
            "side_effect": self.side_effect,
            "autonomy": self.autonomy,
            "transport": self.transport,
            "data": self.data,
            "reason": self.reason,
            "error": self.error,
            "authority": self.authority,
            "audit_id": self.audit_id,
            "actor": self.actor,
        }


# ---------------------------------------------------------------------------
# Actor 上下文（T5）：身份只能由系统注入
# ---------------------------------------------------------------------------


def context_for_employee(
    db: Session,
    employee: Employee,
    *,
    origin: str = "internal",
    task_id: int | None = None,
    project_id: int | None = None,
) -> tool_machinery.ToolCallContext:
    if employee.company_id is None:
        raise ToolExecutionError("employee_has_no_company", "employee is not attached to a company")
    return tool_machinery.ToolCallContext(
        employee_id=int(employee.id),
        company_id=int(employee.company_id),
        person_id=int(employee.person_id) if employee.person_id else None,
        origin=origin,
        task_id=task_id,
        project_id=project_id,
        runtime_type=str(employee.runtime_type or ""),
    )


def context_for_work_session(db: Session, work_session_id: int) -> tool_machinery.ToolCallContext:
    """从 **WorkSession** 推导 actor（Agent Runtime 的唯一注入路径）。

    身份来自创建时记录的归属，当前任职和执行范围只用于重新校验，不能改写身份。
    每次读取都刷新 ORM 缓存；调用方先取得锁并拥有事务。worker 必须仍执行原任务；
    management / review 绑定原项目 / 评审请求。缺少主职不阻断事实读取，写工具
    仍由既有 Authority / Autonomy 门禁逐次校验。
    """
    return _context_for_work_session(db, work_session_id, require_running=True)


def _context_for_work_session(db, work_session_id, *, require_running):
    """Shared identity checks; only confirmed execution may use an ended proposal."""
    session = db.get(WorkSession, int(work_session_id), populate_existing=True)
    if session is None:
        raise ToolExecutionError("work_session_not_found", "work session not found")
    if require_running and (session.status != "running" or session.ended_at is not None):
        raise ToolExecutionError(
            "work_session_not_running",
            "only a running work session without an end time may call management tools",
        )

    snapshot = session.provenance_snapshot_json
    purpose = session.purpose
    if (
        not isinstance(snapshot, dict)
        or snapshot.get("version") != 1
        or purpose not in {"worker", "management", "review"}
    ):
        raise ToolExecutionError(
            "work_session_identity_unknown", "work session has unknown historical attribution"
        )
    # v1 worker history predates explicit purposes; no other missing purpose is
    # reconstructed from today's targets.
    if snapshot.get("purpose", "worker") != purpose:
        raise ToolExecutionError(
            "work_session_identity_mismatch", "work session purpose does not match its snapshot"
        )
    keys = ["employee", "person", "company", "project"]
    if purpose != "management":
        keys.append("task")
    if purpose == "review":
        keys.append("review_request")
    attribution: dict[str, int] = {}
    for key in keys:
        fact = snapshot.get(key) if isinstance(snapshot, dict) else None
        identifier = fact.get("id") if isinstance(fact, dict) else None
        if type(identifier) is not int or identifier <= 0:
            raise ToolExecutionError(
                "work_session_identity_unknown",
                "work session has incomplete historical attribution",
            )
        attribution[key] = identifier
    if session.person_id is None or session.company_id is None:
        raise ToolExecutionError(
            "work_session_identity_unknown", "work session has no historical person or company"
        )
    if any(
        attribution[key] != getattr(session, f"{key}_id")
        for key in ("employee", "person", "company")
    ):
        raise ToolExecutionError(
            "work_session_identity_mismatch", "work session attribution does not match its snapshot"
        )

    employee = db.get(Employee, session.employee_id, populate_existing=True)
    if employee is None:
        raise ToolExecutionError("employee_not_found", "work session has no employee")
    if employee.company_id != session.company_id or employee.person_id != session.person_id:
        raise ToolExecutionError(
            "work_session_identity_mismatch",
            "employee no longer matches the original session actor",
        )
    if employee.lifecycle_status != LifecycleStatus.active.value:
        raise ToolExecutionError("employee_not_active", "work session employee is no longer active")
    if (
        db.get(Person, session.person_id, populate_existing=True) is None
        or db.get(Company, session.company_id, populate_existing=True) is None
    ):
        raise ToolExecutionError(
            "work_session_identity_unknown", "work session person or company no longer exists"
        )

    if purpose == "worker":
        if (
            session.task_id != attribution["task"]
            or session.project_id is not None
            or session.review_request_id is not None
            or snapshot.get("review_request") is not None
        ):
            raise ToolExecutionError(
                "work_session_identity_mismatch", "worker attribution does not match its snapshot"
            )
        task = db.get(Task, session.task_id, populate_existing=True)
        if (
            task is None
            or task.assignee_id != session.employee_id
            or task.status != TaskStatus.in_progress.value
        ):
            raise ToolExecutionError(
                "work_session_task_invalid",
                "work session task is no longer executing for its actor",
            )
        project_id = task.project_id
    else:
        if session.task_id is not None or session.project_id != attribution["project"]:
            raise ToolExecutionError(
                "work_session_identity_mismatch", "non-worker target does not match its snapshot"
            )
        project_id = session.project_id
    project = db.get(Project, project_id, populate_existing=True)
    if (
        project is None
        or project.id != attribution["project"]
        or project.company_id != session.company_id
    ):
        raise ToolExecutionError(
            "work_session_project_invalid",
            "work session project is no longer in its executable scope",
        )
    allowed_states = EXECUTABLE_PROJECT_STATUSES
    if purpose == "management":
        if (
            session.review_request_id is not None
            or snapshot.get("task") is not None
            or snapshot.get("review_request") is not None
        ):
            raise ToolExecutionError(
                "work_session_identity_mismatch", "management session must target only its project"
            )
        if (
            project.management_employee_id != session.employee_id
            or project.management_person_id not in (None, session.person_id)
        ):
            raise ToolExecutionError(
                "work_session_manager_invalid",
                "work session actor is no longer the designated manager",
            )
        allowed_states = allowed_states | {ProjectStatus.waiting_for_management.value}
    elif purpose == "review":
        _validate_review_scope(db, session, attribution, project)
        # The review validator handles the narrowly scoped completed-project
        # receipt case as well as open work. No write is authorized by that case.
        allowed_states = allowed_states | {
            ProjectStatus.waiting_for_management.value,
            ProjectStatus.completed.value,
        }
    if project.status not in allowed_states:
        raise ToolExecutionError(
            "work_session_project_invalid",
            "work session project is no longer in its executable scope",
        )
    return tool_machinery.ToolCallContext(
        employee_id=int(session.employee_id),
        company_id=int(session.company_id),
        person_id=int(session.person_id),
        origin="work_session",
        work_session_id=int(session.id),
        task_id=int(session.task_id) if session.task_id is not None else None,
        project_id=attribution["project"],
        runtime_type=str(session.runtime_type or ""),
        purpose=purpose,
        review_request_id=session.review_request_id,
    )


def _has_own_verdict_receipt(db: Session, session: WorkSession, request: ReviewRequest) -> bool:
    """Only an applied, correlated action proves this session decided the request.

    Neither model claims nor a decided request alone grant a receipt phase. The
    verdict and its timestamp must still match the action committed atomically
    with the domain write. Uncorrelated/internal verdicts cannot open this phase.
    """
    if request.status != C.REVIEW_REQUEST_DECIDED or request.decided_at is None:
        return False
    audits = db.scalars(
        select(ToolAudit)
        .join(RuntimeOperation, RuntimeOperation.id == ToolAudit.runtime_operation_id)
        .join(DecisionRecord, DecisionRecord.id == ToolAudit.decision_id)
        .where(
            RuntimeOperation.work_session_id == session.id,
            RuntimeOperation.kind == "decision",
            RuntimeOperation.decision_id == ToolAudit.decision_id,
            ToolAudit.work_session_id == session.id,
            ToolAudit.actor_employee_id == session.employee_id,
            ToolAudit.actor_person_id == session.person_id,
            ToolAudit.actor_company_id == session.company_id,
            ToolAudit.project_id == session.project_id,
            ToolAudit.task_id.is_(None),
            ToolAudit.origin == "work_session",
            ToolAudit.tool_name == "submit_review_verdict",
            ToolAudit.outcome == "applied",
            ToolAudit.authority_allowed.is_(True),
            DecisionRecord.actor_employee_id == session.employee_id,
            DecisionRecord.actor_person_id == session.person_id,
            DecisionRecord.company_id == session.company_id,
        )
        .execution_options(populate_existing=True)
    )
    for audit in audits:
        args, result = audit.arguments_json or {}, audit.result_json or {}
        if (
            args.get("review_request_id") == request.id
            and result.get("review_request_id") == request.id
            and result.get("task_id") == request.task_id
            and request.verdict in C.REVIEW_VERDICT_TARGETS
            and args.get("verdict") == result.get("verdict") == request.verdict
            and str(args.get("notes") or "") == (request.verdict_notes or "")
            and audit.started_at is not None
            and audit.finished_at is not None
            and as_utc(audit.started_at) <= as_utc(request.decided_at) <= as_utc(audit.finished_at)
        ):
            return True
    return False


def _validate_review_scope(db, session, attribution, project):
    if session.review_request_id != attribution["review_request"]:
        raise ToolExecutionError(
            "work_session_identity_mismatch", "review target does not match its snapshot"
        )
    request = db.get(ReviewRequest, session.review_request_id, populate_existing=True)
    task = db.get(Task, attribution["task"], populate_existing=True)
    original = session.provenance_snapshot_json["review_request"]
    if (
        request is None
        or task is None
        or request.company_id != session.company_id
        or request.project_id != project.id
        or request.task_id != task.id
        or task.project_id != project.id
        or request.reviewer_employee_id != session.employee_id
        or any(
            original.get(key) != getattr(request, key)
            for key in ("project_id", "task_id", "reviewer_employee_id", "requested_by_employee_id")
        )
        or request.requested_at is None
        or original.get("requested_at") != as_utc(request.requested_at).isoformat()
    ):
        raise ToolExecutionError(
            "work_session_review_invalid",
            "review request no longer matches the original target and actor",
        )
    latest_id = db.scalar(
        select(ReviewRequest.id)
        .where(ReviewRequest.task_id == task.id)
        .order_by(ReviewRequest.id.desc())
        .limit(1)
    )
    if latest_id != request.id:
        raise ToolExecutionError(
            "work_session_review_invalid", "review request has been superseded"
        )
    if project.status in {ProjectStatus.cancelled.value, ProjectStatus.rejected.value}:
        raise ToolExecutionError(
            "work_session_project_invalid", "review project is no longer executable"
        )
    if request.status == C.REVIEW_REQUEST_OPEN:
        if (
            task.status != TaskStatus.in_review.value
            or request.verdict is not None
            or request.decided_at is not None
            or project.status == ProjectStatus.completed.value
        ):
            raise ToolExecutionError(
                "work_session_review_invalid", "review request is no longer open for review"
            )
    elif not _has_own_verdict_receipt(db, session, request):
        raise ToolExecutionError(
            "work_session_review_invalid", "review was not decided by this session"
        )
    elif project.status == ProjectStatus.completed.value:
        # Normal PASS follow-on can complete the project before receipt delivery.
        # Admit only that session's PASS with all project work actually done.
        unfinished = db.scalar(
            select(Task.id)
            .where(Task.project_id == project.id, Task.status != TaskStatus.done.value)
            .limit(1)
        )
        if (
            request.verdict != ReviewVerdict.passed.value
            or task.status != TaskStatus.done.value
            or unfinished is not None
        ):
            raise ToolExecutionError(
                "work_session_project_invalid",
                "completed project is outside this verdict receipt scope",
            )


def _nonworker_write_allowed(db, ctx, spec, args):
    """Restrict the current tool catalog to the immutable non-worker target."""
    if ctx.purpose == "review":
        request = db.get(ReviewRequest, ctx.review_request_id, populate_existing=True)
        return (
            spec.name == "submit_review_verdict"
            and args.get("review_request_id") == ctx.review_request_id
            and request.status == C.REVIEW_REQUEST_OPEN
        )
    if spec.name in tool_writes.LEARNING_TOOL_NAMES:
        # Learning remains an action of this management conversation, not a
        # blanket permission to mutate any company/person target.
        return ctx.purpose == "management" and args.get("project_id") == ctx.project_id
    if spec.confirmation_policy is not None:
        return ctx.purpose == "management" and args.get("project_id") == ctx.project_id
    # Every present management write has a concrete project, task or review
    # target. Future company/person actions must explicitly define their scope.
    if spec.name not in {
        "create_task",
        "consume_artifact",
        "update_task",
        "create_dependency",
        "assign_task",
        "delegate_project",
        "submit_review_verdict",
        "replan_project",
        "request_review",
        "request_rework",
        "mark_task_blocked",
        "cancel_task",
    }:
        return False
    targeted = False
    if "project_id" in args:
        targeted = True
        if args["project_id"] != ctx.project_id:
            return False
    task_ids = [args[key] for key in ("task_id", "depends_on_id") if args.get(key) is not None]
    for key in ("depends_on", "consumes"):
        task_ids.extend(args.get(key) or [])
    for identifier in task_ids:
        targeted = True
        task = db.get(Task, identifier, populate_existing=True)
        if task is None or task.project_id != ctx.project_id:
            return False
    if args.get("review_request_id") is not None:
        targeted = True
        request = db.get(ReviewRequest, args["review_request_id"], populate_existing=True)
        if (
            request is None
            or request.project_id != ctx.project_id
            or request.company_id != ctx.company_id
        ):
            return False
        task = db.get(Task, request.task_id, populate_existing=True)
        if task is None or task.project_id != ctx.project_id:
            return False
    if args.get("artifact_id") is not None:
        artifact = db.get(DriveNode, args["artifact_id"], populate_existing=True)
        if (
            artifact is None
            or artifact.project_id != ctx.project_id
            or artifact.company_id != ctx.company_id
        ):
            return False
    return targeted


def _learning_decision_is_bound(db, ctx, decision_id):
    """A record by itself cannot stand in for the persistent runtime envelope."""
    if ctx.purpose != "management" or ctx.origin != "work_session" or ctx.work_session_id is None:
        return False
    decision = db.get(DecisionRecord, decision_id, populate_existing=True)
    if (
        decision is None
        or decision.decision_type != C.DecisionKind.enroll_learning.value
        or decision.actor_employee_id != ctx.employee_id
        or decision.actor_person_id != ctx.person_id
        or decision.company_id != ctx.company_id
        or decision.project_id != ctx.project_id
        or decision.status != "EXECUTING"
        or decision.resolved_at is not None
        or decision.superseded_by_id is not None
    ):
        return False
    return (
        db.scalar(
            select(RuntimeOperation.id).where(
                RuntimeOperation.decision_id == decision_id,
                RuntimeOperation.work_session_id == ctx.work_session_id,
                RuntimeOperation.kind == "decision",
            )
        )
        is not None
    )


# ---------------------------------------------------------------------------
# 注册表（默认实例；读 + 写）。`execute_tool(registry_override=...)` 只给测试/未来
# 的多租户场景留一个显式接缝 —— 生产路径永远是这一份。
# ---------------------------------------------------------------------------

registry = tool_machinery.ToolRegistry()
for _spec in (*tool_reads.build_read_tools(), *tool_writes.build_write_tools()):
    registry.register(_spec)
tool_machinery.assert_registry_is_sound(registry)


def catalog() -> list[dict]:
    """给 Runtime / 调试面看的工具清单（不含 handler）。"""
    return registry.catalog()


def tools_by_side_effect(side_effect: C.ToolSideEffect) -> tuple[tool_machinery.ToolSpec, ...]:
    return registry.by_side_effect(side_effect)


# ---------------------------------------------------------------------------
# 审计（T12）
# ---------------------------------------------------------------------------


def _args_digest(args: dict) -> str:
    payload = json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _record_tool_audit(
    db: Session,
    *,
    spec: tool_machinery.ToolSpec,
    ctx: tool_machinery.ToolCallContext,
    transport: ToolTransport,
    args: dict,
    outcome: str,
    result: dict | None,
    authority_snapshot: dict | None,
    error: str = "",
    decision_id: int | None = None,
    started_at: datetime | None = None,
) -> int:
    """每次工具调用都留一条**执行事实**（T12 / DR1）。

    M2.3 把工具审计写进 `audit_logs.after_json`（复用既有表的最小改动）。
    M2.4 改为写 `tool_audits` —— 因为需要按 `decision_id` **反查**某条决策执行了什么，
    而 JSON blob 里没有可索引的列（DR3）。

    `audit_logs` 继续承载**人/领域**动作（入职、评审、账号…）；`tool.*` 不再写它：
    同一个事实不留两个落点。
    """
    snapshot = authority_snapshot or {}
    entry = ToolAudit(
        tool_name=spec.name,
        decision_id=decision_id,
        outcome=outcome,
        side_effect=spec.side_effect.value,
        decision_semantics=spec.decision_semantics.value,
        autonomy=spec.autonomy.value,
        transport=transport.value,
        actor_employee_id=ctx.employee_id,
        actor_person_id=ctx.person_id,
        actor_company_id=ctx.company_id,
        origin=ctx.origin,
        work_session_id=ctx.work_session_id,
        task_id=ctx.task_id,
        project_id=ctx.project_id,
        # 入参原样留档（执行事实；决策行里不复制它，DR5）
        # `json_safe`：领域返回值可能带 datetime/Decimal 等 JSON 不认的值 ——
        # 让审计炸掉等于丢执行事实（M2.10 黄金路径实测踩到）
        arguments_json=tool_machinery.json_safe(dict(args)),
        arguments_digest=_args_digest(args),
        authority_json=authority_snapshot,
        authority_allowed=(bool(snapshot["allowed"]) if "allowed" in snapshot else None),
        authority_reason=str(snapshot.get("reason") or ""),
        authority_grant_ids=list(snapshot.get("grant_ids") or []),
        authority_grants_hash=str(snapshot.get("grants_hash") or ""),
        result_json=tool_machinery.json_safe(result) if result is not None else None,
        error=error,
        started_at=started_at or utcnow(),
        finished_at=utcnow(),
    )
    db.add(entry)
    db.flush()
    return int(entry.id)


def execute_tool(
    db: Session,
    *,
    name: str,
    args: dict | None = None,
    context: tool_machinery.ToolCallContext,
    transport: ToolTransport = ToolTransport.internal,
    commit: bool = True,
    registry_override: tool_machinery.ToolRegistry | None = None,
    decision_id: int | None = None,
    _runtime_action: tuple[int, int] | None = None,
    _confirmation=None,
) -> ToolResult:
    """执行一次工具调用（唯一入口）。

    内部面与调试口走**同一段代码**：唯一的差别是调试口多一道开关检查（T10）。
    一次动作的领域写入、ToolAudit 和来源 Event 共用事务；commit=False 由调用者提交。
    """
    arguments = dict(args or {})
    started_at = utcnow()
    active_registry = registry_override or registry
    try:
        spec = active_registry.get(name)
    except tool_machinery.ToolError as exc:
        return ToolResult(
            ok=False,
            tool=name,
            side_effect="unknown",
            autonomy="unknown",
            transport=transport.value,
            reason="unknown_tool",
            error=str(exc),
            actor=context.as_dict(),
        )

    base = {
        "tool": spec.name,
        "side_effect": spec.side_effect.value,
        "autonomy": spec.autonomy.value,
        "transport": transport.value,
        "actor": context.as_dict(),
    }

    def fail(reason: str, message: str, *, snapshot: dict | None = None) -> ToolResult:
        ensure_sqlite_transaction(db)
        with db.begin_nested():
            audit_id = _record_tool_audit(
                db,
                spec=spec,
                ctx=context,
                transport=transport,
                args=arguments,
                outcome=reason,
                result=None,
                authority_snapshot=snapshot,
                error=message,
                decision_id=decision_id,
                started_at=started_at,
            )
        if commit:
            db.commit()
        return ToolResult(
            ok=False,
            reason=reason,
            error=message,
            authority=snapshot,
            audit_id=audit_id,
            **base,
        )

    # ① transport 门禁（调试口默认关）
    if transport is ToolTransport.debug_cli and not settings.agent_tool_cli_enabled:
        return fail(
            "cli_disabled",
            "debug CLI is disabled (EIDOLON_AGENT_TOOL_CLI_ENABLED=false)",
        )
    if transport not in spec.transports:
        return fail("transport_not_allowed", f"{spec.name} is not available over {transport.value}")

    # ② 参数校验（含身份字段拒绝，T5）
    if _confirmation is not None and (
        spec.side_effect is not C.ToolSideEffect.high_impact or spec.confirmation_policy is None
    ):
        return fail(
            "confirmation_policy_changed", "the approved tool policy is no longer available"
        )
    try:
        tool_machinery.validate_arguments(spec.input_schema, arguments, tool=spec.name)
    except tool_machinery.ToolError as exc:
        return fail("invalid_arguments", str(exc))

    # Purpose is a restriction, never a replacement for Authority/Autonomy.
    # Refresh even if a trusted internal caller retained a context between calls.
    # Refused in-scope attempts have actual durable receipts just like all other
    # tool refusals; a decided review can never authorize another fresh write.
    if not spec.is_read and context.purpose != "worker":
        ensure_sqlite_transaction(db)
        if context.origin != "work_session" or context.work_session_id is None:
            return fail(
                "work_session_write_forbidden", "non-worker writes require a bound work session"
            )
        try:
            current = _context_for_work_session(
                db, context.work_session_id, require_running=_confirmation is None
            )
        except ToolExecutionError as exc:
            return fail(exc.reason, str(exc))
        if current != context:
            return fail(
                "work_session_identity_mismatch", "tool context no longer matches its work session"
            )
        if not _nonworker_write_allowed(db, current, spec, arguments):
            return fail(
                "work_session_write_forbidden",
                "action is outside this work session's current write scope",
            )

    # ③ Authority（内部面不例外，T4）
    snapshot: dict | None = None
    if not spec.is_read:
        assert spec.required_authority is not None and spec.authority_target is not None
        target = spec.authority_target(db, context, arguments)
        amount = None
        if spec.amount_arg is not None:
            raw = arguments.get(spec.amount_arg)
            amount = int(raw) if raw is not None else None
        decision = authority_service.authorizes(
            db,
            employee_id=context.employee_id,
            kind=spec.required_authority,
            target=target,
            amount=amount,
        )
        snapshot = C.authority_snapshot(decision, action_ref=f"tool:{spec.name}")
        if not decision.allowed:
            return fail("not_authorized", f"not authorized: {decision.reason}", snapshot=snapshot)
        checks = []
        for kind in spec.additional_authorities:
            extra = authority_service.authorizes(
                db, employee_id=context.employee_id, kind=kind, target=target, amount=amount
            )
            check = C.authority_snapshot(extra, action_ref=f"tool:{spec.name}")
            checks.append(check)
            if not extra.allowed:
                check["prior_checks"] = [snapshot, *checks[:-1]]
                return fail(
                    "not_authorized", f"not authorized: {kind.value}:{extra.reason}", snapshot=check
                )
        if checks:
            snapshot["additional_checks"] = checks

    # ③b 决策语义门禁（DR7）—— 放在 Authority **之后**：安全边界先判，
    # 否则"这个工具需要决策"会盖住"你没权限"这个更重要的回答。
    if spec.decision_semantics is DecisionSemantics.required and decision_id is None:
        return fail(
            "decision_required",
            f"{spec.name} is a management action and must belong to a decision "
            "(submit it inside a decision envelope)",
        )
    if spec.decision_semantics is DecisionSemantics.none and decision_id is not None:
        return fail(
            "decision_not_allowed",
            f"{spec.name} is not a decision action (semantics=none)",
        )
    if spec.name in tool_writes.LEARNING_TOOL_NAMES and not _learning_decision_is_bound(
        db, context, decision_id
    ):
        return fail(
            "invalid_learning_decision",
            "learning requires this management session's executing enroll_learning envelope",
            snapshot=snapshot,
        )

    # ④ Autonomy 门禁（Authority ≠ Autonomy，用户拍板 §11）
    if spec.autonomy is not AutonomyLevel.auto_allowed:
        if spec.confirmation_policy is None:
            return fail(
                "autonomy_requires_confirmation",
                "this action has no registered confirmation path",
                snapshot=snapshot,
            )
        from app.work import action_confirmations as confirmations

        ensure_sqlite_transaction(db)
        try:
            if _confirmation is None:
                if commit:
                    raise confirmations.ConfirmationError("runtime_transaction_required")
                with db.begin_nested():
                    row = confirmations.propose(
                        db,
                        spec=spec,
                        ctx=context,
                        args=arguments,
                        decision_id=decision_id,
                        runtime_action=_runtime_action,
                    )
                    data = {"confirmation": confirmations.proposal_ref(row)}
                    audit_id = _record_tool_audit(
                        db,
                        spec=spec,
                        ctx=context,
                        transport=transport,
                        args=arguments,
                        outcome="confirmation_pending",
                        result=data,
                        authority_snapshot=snapshot,
                        decision_id=decision_id,
                        started_at=started_at,
                    )
                # Only the trusted runtime coordinator correlates and commits this
                # original receipt. A proposal cannot originate from the debug CLI.
                return ToolResult(
                    ok=False,
                    reason="confirmation_pending",
                    data=data,
                    authority=snapshot,
                    audit_id=audit_id,
                    **base,
                )
            confirmations.validate_execution(
                db,
                _confirmation,
                spec=spec,
                ctx=context,
                args=arguments,
                decision_id=decision_id,
            )
        except confirmations.ConfirmationError as exc:
            return fail(exc.reason, str(exc), snapshot=snapshot)
        except tool_machinery.ToolError as exc:
            return fail("confirmation_terms_unavailable", str(exc), snapshot=snapshot)

    # ⑤ 应用 + 审计
    #
    # **SAVEPOINT 而不是 `db.rollback()`**：执行面跑在调用方的事务里（决策信封就是这种情况），
    # 裸回滚会把**调用方已经落下的东西**一起抹掉 —— 实测踩到过：一个动作在 handler 里
    # 被领域拒绝，整个 DecisionRecord（以及此前已成功的动作）全被回滚掉。
    # SAVEPOINT 丢弃本动作的领域写入、来源事件和成功审计；拒绝审计另行记录。
    # SQLite 的虚拟 Session 事务不足以包住 SAVEPOINT，必须先有物理 BEGIN。
    ensure_sqlite_transaction(db)
    handler_failure: tuple[str, str] | None = None
    with db.begin_nested() as savepoint:
        try:
            data = spec.handler(db, context, arguments)
        except tool_machinery.ToolError as exc:
            savepoint.rollback()
            handler_failure = ("domain_rejected", str(exc))
        except Exception as exc:
            savepoint.rollback()
            logger.exception("tool handler failed", extra={"tool": spec.name})
            handler_failure = ("handler_failed", f"{type(exc).__name__}: {exc}")

        if handler_failure is None:
            result = data if isinstance(data, dict) else {"result": data}
            if spec.is_read:
                result.setdefault("result_kind", "facts_only")
            if not spec.is_read and snapshot is not None:
                result["authority"] = snapshot

            # Audit failure must escape the savepoint and roll back the applied
            # effect too. It is not a business rejection with a missing receipt.
            audit_id = _record_tool_audit(
                db,
                spec=spec,
                ctx=context,
                transport=transport,
                args=arguments,
                outcome="applied" if not spec.is_read else "read",
                result=result,
                authority_snapshot=snapshot,
                decision_id=decision_id,
                started_at=started_at,
            )
    if handler_failure is not None:
        return fail(*handler_failure, snapshot=snapshot)
    if commit:
        db.commit()
    return ToolResult(
        ok=True,
        data=result,
        reason="applied" if not spec.is_read else "ok",
        authority=snapshot,
        audit_id=audit_id,
        **base,
    )


def describe_tools() -> dict:
    """诊断用：工具清单 + 当前可用性（不含 handler）。"""
    return {
        "tools": catalog(),
        "read_count": len(registry.by_side_effect(C.ToolSideEffect.read)),
        "write_count": len(registry.by_side_effect(C.ToolSideEffect.write)),
        "high_impact_count": len(registry.by_side_effect(C.ToolSideEffect.high_impact)),
        "reserved_high_impact_authorities": sorted(
            kind.value for kind in C.HIGH_IMPACT_AUTHORITIES_RESERVED
        ),
        "cli_enabled": bool(settings.agent_tool_cli_enabled),
        "autonomy_by_side_effect": {
            key.value: level.value for key, level in C.AUTONOMY_BY_SIDE_EFFECT.items()
        },
    }
