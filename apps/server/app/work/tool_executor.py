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

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.base import utcnow
from app.models.decision import ToolAudit
from app.models.enums import AutonomyLevel, DecisionSemantics, ToolTransport
from app.models.organization import Employee
from app.models.project import Task, WorkSession
from app.work import authority as authority_service
from app.work import contracts as C
from app.work import tool_reads, tool_writes
from app.work import tools as tool_machinery

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

    身份来自库里的会话行 + 员工行，**不来自**模型参数（T5）。会话必须处于
    `running`：已经结束的会话不该再发起组织动作（那会让"谁在什么时候做的"失真）。
    """
    session = db.get(WorkSession, int(work_session_id))
    if session is None:
        raise ToolExecutionError("work_session_not_found", "work session not found")
    if session.status != "running":
        raise ToolExecutionError(
            "work_session_not_running",
            f"work session is {session.status}; only a running session may call management tools",
        )
    employee = db.get(Employee, int(session.employee_id))
    if employee is None:  # pragma: no cover - 防御
        raise ToolExecutionError("employee_not_found", "work session has no employee")
    task = db.get(Task, int(session.task_id)) if session.task_id else None
    return tool_machinery.ToolCallContext(
        employee_id=int(employee.id),
        company_id=int(employee.company_id or 0),
        person_id=int(employee.person_id) if employee.person_id else None,
        origin="work_session",
        work_session_id=int(session.id),
        task_id=int(session.task_id) if session.task_id else None,
        project_id=int(task.project_id) if task is not None else None,
        runtime_type=str(session.runtime_type or ""),
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
        arguments_json=dict(args),
        arguments_digest=_args_digest(args),
        authority_json=authority_snapshot,
        authority_allowed=(bool(snapshot["allowed"]) if "allowed" in snapshot else None),
        authority_reason=str(snapshot.get("reason") or ""),
        authority_grant_ids=list(snapshot.get("grant_ids") or []),
        authority_grants_hash=str(snapshot.get("grants_hash") or ""),
        result_json=result,
        error=error,
        started_at=started_at or utcnow(),
        finished_at=utcnow(),
    )
    db.add(entry)
    db.flush()
    db.commit()
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
) -> ToolResult:
    """执行一次工具调用（唯一入口）。

    内部面与调试口走**同一段代码**：唯一的差别是调试口多一道开关检查（T10）。
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
    try:
        tool_machinery.validate_arguments(spec.input_schema, arguments, tool=spec.name)
    except tool_machinery.ToolError as exc:
        return fail("invalid_arguments", str(exc))

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

    # ④ Autonomy 门禁（Authority ≠ Autonomy，用户拍板 §11）
    if spec.autonomy is not AutonomyLevel.auto_allowed:
        return fail(
            "autonomy_requires_confirmation",
            f"{spec.side_effect.value} actions need human confirmation "
            f"(autonomy={spec.autonomy.value}); no confirmation channel in M2.3",
            snapshot=snapshot,
        )

    # ⑤ 应用 + 审计
    #
    # **SAVEPOINT 而不是 `db.rollback()`**：执行面跑在调用方的事务里（决策信封就是这种情况），
    # 裸回滚会把**调用方已经落下的东西**一起抹掉 —— 实测踩到过：一个动作在 handler 里
    # 被领域拒绝，整个 DecisionRecord（以及此前已成功的动作）全被回滚掉。
    # SAVEPOINT 只丢弃**这个 handler**写了一半的东西，调用方的上下文原封不动。
    savepoint = db.begin_nested()
    try:
        data = spec.handler(db, context, arguments)
    except tool_machinery.ToolError as exc:
        if savepoint.is_active:
            savepoint.rollback()
        return fail("domain_rejected", str(exc), snapshot=snapshot)
    except Exception as exc:  # pragma: no cover - 防御：领域异常不能悄悄吞掉
        if savepoint.is_active:
            savepoint.rollback()
        logger.exception("tool handler failed", extra={"tool": spec.name})
        return fail("handler_failed", f"{type(exc).__name__}: {exc}", snapshot=snapshot)
    if savepoint.is_active:
        savepoint.commit()

    result = data if isinstance(data, dict) else {"result": data}
    if spec.is_read:
        result.setdefault("result_kind", "facts_only")
    if not spec.is_read and snapshot is not None:
        result["authority"] = snapshot

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
    if commit and not spec.is_read:  # 读工具不写业务状态；handler 自己负责提交写入
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
