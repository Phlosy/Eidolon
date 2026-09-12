"""M2.4 Leadership Planning & Delegation —— Decision Envelope 测试（DR1–DR10）。

三层不混是这套测试的主轴：

```text
DecisionRecord  = 为什么（管理语义）
ToolAudit       = 实际执行了什么（执行事实）
Domain State    = 事实最终变成什么样
```

因此每个用例都尽量同时看三层，而不是只看返回值。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.models.decision import DecisionRecord, ToolAudit
from app.models.enums import (
    AuthorityScopeKind,
    DecisionSemantics,
    DecisionStatus,
    LifecycleStatus,
    TaskKind,
    TaskStatus,
    ToolSideEffect,
)
from app.models.organization import Employee
from app.models.position import PositionDefinition
from app.models.project import Task
from app.repositories import organization as org_repo
from app.repositories import project as project_repo
from app.schemas.position import AssignmentIn, PositionDefinitionIn, SlotIn
from app.services import position_service
from app.services import tasks as task_service
from app.work import authority as authority_service
from app.work import contracts as C
from app.work import decisions as decision_service
from app.work import tool_executor as executor

SERVER_ROOT = Path(__file__).resolve().parents[1]
APP = SERVER_ROOT / "app"
DECISIONS_MODULE = APP / "work" / "decisions.py"
MODEL_MODULE = APP / "models" / "decision.py"


# ---------------------------------------------------------------------------
# 工具（与 M2.3 测试同款纪律：独立职位定义隔离 grant 状态）
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _restore_org_state(org_snapshot):
    yield org_snapshot


def _employees(db, company_id: int) -> dict[str, Employee]:
    return {row.slug: row for row in org_repo.list_employees(db, company_id)}


def _lab(db, company_id: int, code: str, slug: str = "charlie"):
    """隔离实验台（**幂等**）：同一 `code` 被多个用例复用不会 409。

    为什么必须幂等：`org_snapshot` 只还原任职/生命周期，**不**删除测试期间新建的
    职位定义（删定义会牵连 grant 行、也会破坏 append-only 的历史）。
    """
    from app.repositories import position as position_repo

    employee = _employees(db, company_id)[slug]
    position_service.release_position(db, employee, reason="m2.4 lab")
    db.flush()
    definition = position_repo.get_definition_by_code(db, code, company_id=company_id)
    if definition is None:
        definition = position_service.create_definition(
            db,
            PositionDefinitionIn(
                code=code, name=f"Decision Lab {code}", job_family="engineering", level=3
            ),
            company_id=company_id,
        )
        db.flush()
    slots = position_repo.list_slots(db, company_id=company_id, definition_id=int(definition.id))
    if slots:
        slot = slots[0]
    else:
        slot = position_service.open_slots(
            db,
            int(definition.id),
            SlotIn(department_id=int(employee.department_id), count=1, note="m2.4 lab"),
            company_id,
        )[0]
    position_service.assign_position(
        db, employee, AssignmentIn(slot_id=int(slot.id), reason="m2.4 lab", kind="assign")
    )
    db.commit()
    return employee, definition


def _grant(db, definition: PositionDefinition, *kinds: C.AuthorityKind):
    for kind in kinds:
        authority_service.grant_authority(
            db,
            position_definition_id=int(definition.id),
            kind=kind,
            scope_kind=AuthorityScopeKind.company,
            commit=False,
        )
    db.commit()


#: 一个管理 Agent 组织一条完整工作链需要的授权（工作图 + 派活）
MANAGER_AUTHORITY = (
    C.AuthorityKind.plan_project_work,
    C.AuthorityKind.assign_task,
    C.AuthorityKind.delegate_management,
)


def _manager(db, company_id: int, code: str = "lab-decisions"):
    employee, definition = _lab(db, company_id, code)
    _grant(db, definition, *MANAGER_AUTHORITY)
    return employee, definition


def _context(db, employee: Employee):
    return executor.context_for_employee(db, employee, origin="test")


def _project(db, company_id: int, name: str = "决策项目"):
    project = project_repo.create_project(
        db,
        company_id=company_id,
        name=name,
        description="M2.4 测试项目",
        status="in_progress",
        source_order_text="M2.4 测试项目",
    )
    db.commit()
    return project


def _decision_row(db, decision_id: int) -> DecisionRecord:
    row = db.get(DecisionRecord, int(decision_id))
    assert row is not None
    return row


# ---------------------------------------------------------------------------
# DR1 / DR2：三层分离 + 一条决策多个动作
# ---------------------------------------------------------------------------


def test_three_layers_are_separate():
    """DR1：意图 / 执行事实 / 领域状态是**两张表 + 领域表**，不是一个东西。"""
    tables = set(__import__("app.models", fromlist=["Base"]).Base.metadata.tables)
    assert {"decision_records", "tool_audits"} <= tables
    decision_columns = set(
        __import__("app.models", fromlist=["Base"])
        .Base.metadata.tables["decision_records"]
        .columns.keys()
    )
    # 执行细节不得出现在决策行上（DR5）
    assert not (
        decision_columns
        & {"tool_name", "arguments_json", "result_json", "error", "audit_ids", "tool_ids"}
    ), "决策行复制了执行细节 —— DR5"
    audit_columns = set(
        __import__("app.models", fromlist=["Base"])
        .Base.metadata.tables["tool_audits"]
        .columns.keys()
    )
    # 管理语义不得出现在执行事实上（否则又混层了）
    assert not (
        audit_columns & {"reason", "intended_outcome", "parent_decision_id", "decision_type"}
    ), "执行事实里混进了管理语义 —— DR1"

    # 模块层面：决策服务只经执行面执行动作，不自己写库
    called = {
        node.func.attr
        for node in ast.walk(ast.parse(DECISIONS_MODULE.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "execute_tool" in called, "决策服务必须经执行面执行动作"
    # 决策服务**拥有** decision_records / tool_audits 两张表（db.add 是它的职责），
    # 但它绝不能直接改**领域**状态：那必须经执行面 → 领域 service（DR1/DR4）。
    assert not (
        called
        & {
            "create_task",
            "transition_task",
            "add_dependency",
            "create_project",
            "assign_position",
            "release_position",
            "offboard",
        }
    ), "决策服务越过了执行面直接改领域状态"
    assert "delete" not in called


def test_one_decision_produces_many_actions(db, default_company_id):
    """DR2：一条决策 = 一个 DecisionRecord + **N 个 ToolAudit**（tool call ≠ decision）。"""
    manager, _definition = _manager(db, default_company_id)
    project = _project(db, default_company_id, "DR2 多动作")
    ctx = _context(db, manager)
    scope = f"project:{int(project.id)}"

    result = decision_service.submit_envelope(
        db,
        ctx,
        decision_type=C.DecisionKind.decompose_project,
        reason="把交付拆成实现与验证两条工作线，各派一位负责人。",
        intended_outcome="两条工作线就位，依赖已声明。",
        scope=scope,
        context={"project_id": int(project.id), "note": "two workstreams"},
        actions=[
            {"tool": "create_task", "args": {"project_id": int(project.id), "title": "实现"}},
            {"tool": "create_task", "args": {"project_id": int(project.id), "title": "验证"}},
        ],
    )

    assert result.status is DecisionStatus.applied, result.outcomes
    assert result.applied == 2 and result.failed == 0

    # 一层：决策行只有一条
    rows = list(db.scalars(select(DecisionRecord).where(DecisionRecord.id == result.decision_id)))
    assert len(rows) == 1
    decision = rows[0]
    assert decision.decision_type == C.DecisionKind.decompose_project.value
    assert decision.reason.startswith("把交付拆成")
    assert decision.intended_outcome
    assert decision.context_hash and decision.context_version == C.DECISION_CONTEXT_VERSION
    assert decision.resolved_at is not None

    # 二层：一条决策挂 N 条执行事实
    audits = decision_service.tool_audits_for(db, result.decision_id)
    assert len(audits) == 2
    assert {row.tool_name for row in audits} == {"create_task"}
    assert all(int(row.decision_id) == int(decision.id) for row in audits)
    assert all(row.actor_employee_id == int(manager.id) for row in audits)

    # 三层：领域状态真的变了
    created = list(db.scalars(select(Task).where(Task.project_id == int(project.id))))
    assert {row.title for row in created} == {"实现", "验证"}


def test_direct_tool_call_without_decision_is_still_legal_for_optional_tools(
    db, default_company_id
):
    """DR2 的另一半：**不必**把每个工具调用都变成决策（OPTIONAL 类可独立执行）。"""
    manager, _definition = _manager(db, default_company_id)
    project = _project(db, default_company_id, "DR2 独立动作")
    task = task_service.create_task(
        db,
        project_id=int(project.id),
        title="独立任务",
        kind=TaskKind.development.value,
        assignee_id=None,
        status=TaskStatus.backlog.value,
    )
    db.commit()
    result = executor.execute_tool(
        db,
        name="update_task",
        args={"task_id": int(task.id), "priority": 7},
        context=_context(db, manager),
    )
    assert result.ok, result.error
    audits = list(db.scalars(select(ToolAudit).where(ToolAudit.tool_name == "update_task")))
    assert audits and audits[-1].decision_id is None, "独立动作的审计不该挂决策"


# ---------------------------------------------------------------------------
# DR3：关联只有一个方向
# ---------------------------------------------------------------------------


def test_link_direction_is_single_way(db, default_company_id):
    """DR3：`ToolAudit.decision_id → DecisionRecord.id`，**没有**反向数组。"""
    columns = set(DecisionRecord.__table__.columns.keys())
    assert "audit_ids" not in columns
    assert not {name for name in columns if name.endswith("_ids") and "audit" in name}
    assert "decision_id" in ToolAudit.__table__.columns
    # 反查是唯一查询路径（读模型就是这么做的）
    manager, _definition = _manager(db, default_company_id)
    project = _project(db, default_company_id, "DR3 反查")
    result = decision_service.submit_envelope(
        db,
        _context(db, manager),
        decision_type=C.DecisionKind.decompose_project,
        reason="一条动作的决策",
        scope=f"project:{int(project.id)}",
        actions=[{"tool": "create_task", "args": {"project_id": int(project.id), "title": "T"}}],
    )
    view = decision_service.decision_view(db, _decision_row(db, result.decision_id))
    assert view["action_summary"]["total"] == 1
    assert len(view["actions"]) == 1
    assert view["actions"][0]["audit_id"]


# ---------------------------------------------------------------------------
# DR4：决策不授予权限
# ---------------------------------------------------------------------------


def test_decision_never_grants_authority(db, default_company_id):
    """DR4：决策里写"我要派活"**不产生任何授权**；每个动作重新走 Authority。"""
    # 一个只有工作图授权、**没有** assign_task 授权的 manager
    employee, definition = _lab(db, default_company_id, "lab-noassign")
    _grant(db, definition, C.AuthorityKind.plan_project_work)
    project = _project(db, default_company_id, "DR4 无派活权")
    task = task_service.create_task(
        db,
        project_id=int(project.id),
        title="要派的任务",
        kind=TaskKind.development.value,
        assignee_id=None,
        status=TaskStatus.backlog.value,
    )
    db.commit()
    bob = _employees(db, default_company_id)["bob"]

    result = decision_service.submit_envelope(
        db,
        _context(db, employee),
        decision_type=C.DecisionKind.assign_task,
        reason="我认为 Bob 该做这个任务。",
        scope=f"project:{int(project.id)}",
        actions=[
            {"tool": "assign_task", "args": {"task_id": int(task.id), "employee_id": int(bob.id)}}
        ],
    )
    assert result.status is DecisionStatus.failed
    audits = decision_service.tool_audits_for(db, result.decision_id)
    assert audits[0].outcome == "not_authorized"
    assert audits[0].authority_allowed is False
    db.refresh(task)
    assert task.assignee_id is None, "被拒的动作绝不能落进领域状态"


def test_decision_authority_snapshot_is_evidence_not_a_pass(db, default_company_id):
    """决策行上的授权快照只作证据：它不参与任何后续动作的授权判定。"""
    manager, _definition = _manager(db, default_company_id, code="lab-dr4-evidence")
    project = _project(db, default_company_id, "DR4 证据")
    ctx = _context(db, manager)
    result = decision_service.submit_envelope(
        db,
        ctx,
        decision_type=C.DecisionKind.decompose_project,
        reason="建一个任务",
        scope=f"project:{int(project.id)}",
        actions=[{"tool": "create_task", "args": {"project_id": int(project.id), "title": "A"}}],
    )
    decision = _decision_row(db, result.decision_id)
    assert decision.authority_json["position_grants_hash"], "决策要留当时的授权摘要"
    # 把授权全部撤回之后：决策行还在，但**同一条决策不能重放**（它已经终态）
    for row in authority_service.effective_grants(db, int(manager.id)):
        authority_service.revoke_authority(db, grant_id=int(row.grant_id), reason="dr4")
    assert (
        authority_service.authorizes(
            db, employee_id=int(manager.id), kind=C.AuthorityKind.plan_project_work
        ).allowed
        is False
    )
    replay = executor.execute_tool(
        db,
        name="create_task",
        args={"project_id": int(project.id), "title": "B"},
        context=ctx,
        decision_id=int(decision.id),
    )
    assert replay.ok is False and replay.reason == "not_authorized"


# ---------------------------------------------------------------------------
# DR5：决策不复制执行细节
# ---------------------------------------------------------------------------


def test_decision_record_does_not_copy_tool_payloads(db, default_company_id):
    """DR5：入参/出参/错误只住在 `ToolAudit`。"""
    manager, _definition = _manager(db, default_company_id)
    project = _project(db, default_company_id, "DR5 不复制")
    long_title = "标题" * 30
    result = decision_service.submit_envelope(
        db,
        _context(db, manager),
        decision_type=C.DecisionKind.decompose_project,
        reason="建一个长标题任务",
        scope=f"project:{int(project.id)}",
        actions=[
            {
                "tool": "create_task",
                "args": {"project_id": int(project.id), "title": long_title},
            }
        ],
    )
    decision = _decision_row(db, result.decision_id)
    blob = (
        str(decision.context_json)
        + str(decision.authority_json)
        + str(decision.outcome_note)
        + str(decision.reason)
        + str(decision.intended_outcome)
    )
    assert long_title not in blob, "决策行复制了工具入参 —— DR5"
    # 工具名同样属于执行事实（决策行说"我决定了什么"，不说"调了哪个工具"）
    from app.work import tool_executor as _executor

    # 排除授权快照：它按设计含 `AuthorityKind`（"assign_task" 既是授权名也是工具名），
    # 那是**授权证据**而非执行细节；其余字段一律不得出现工具名。
    without_authority = (
        str(decision.context_json.get("note", ""))
        + str(decision.outcome_note)
        + str(decision.reason)
        + str(decision.intended_outcome)
    )
    for tool_name in _executor.registry.names():
        assert tool_name not in without_authority, f"决策行复制了工具名 {tool_name} —— DR5"
    assert not (set(decision.context_json) & {"actions", "results", "tool", "tools"})
    # `authority_json` 是**授权**快照（grant 的 kind/scope），不是执行细节 ——
    # 但它里面会出现 "assign_task" 这类**授权名**；这不是工具名泄漏，见下一行断言。
    audit = decision_service.tool_audits_for(db, result.decision_id)[0]
    assert audit.arguments_json["title"] == long_title, "执行事实必须留有入参"


# ---------------------------------------------------------------------------
# DR6：partial apply 必须能表达
# ---------------------------------------------------------------------------


def test_partial_apply_is_expressed_not_rounded(db, default_company_id):
    """DR6：多动作部分失败 ⇒ `PARTIALLY_APPLIED`（不许四舍五入成成功或失败）。"""
    manager, _definition = _manager(db, default_company_id)
    project = _project(db, default_company_id, "DR6 部分生效")
    dana = _employees(db, default_company_id)["dana"]
    dana.lifecycle_status = LifecycleStatus.suspended.value  # 生命周期冲突 ⇒ 第二个动作会失败
    db.commit()

    result = decision_service.submit_envelope(
        db,
        _context(db, manager),
        decision_type=C.DecisionKind.decompose_project,
        reason="建两个任务，其中一个派给一个目前不可用的人。",
        scope=f"project:{int(project.id)}",
        actions=[
            {"tool": "create_task", "args": {"project_id": int(project.id), "title": "能做的"}},
            {
                "tool": "create_task",
                "args": {"project_id": int(project.id), "title": "派不出去的"},
            },
            # 让第二个动作失败：把它交给一个停用的人的**指派**动作会 domain_rejected
            {
                "tool": "assign_task",
                "args": {"task_id": 0, "employee_id": int(dana.id)},
            },
        ],
    )
    assert result.status is DecisionStatus.partially_applied, result.outcomes
    decision = _decision_row(db, result.decision_id)
    assert decision.resolved_at is not None
    assert "actions applied" in decision.outcome_note
    outcomes = [row.outcome for row in decision_service.tool_audits_for(db, decision.id)]
    assert "applied" in outcomes and any(code != "applied" for code in outcomes)

    # **领域状态**：成功的那个动作必须真的留下（失败的不能把它一起回滚掉）
    titles = {
        row.title for row in db.scalars(select(Task).where(Task.project_id == int(project.id)))
    }
    assert "能做的" in titles, "成功的动作被后来的失败动作回滚了"
    assert "派不出去的" in titles, "create_task 本身应该成功"
    # 决策行也必须还在（意图不因动作失败而消失）
    assert _decision_row(db, result.decision_id).reason.startswith("建两个任务")


def test_all_failed_actions_yield_failed_status(db, default_company_id):
    """全部失败 ⇒ `FAILED`（与"部分生效"区分开）。"""
    manager, _definition = _manager(db, default_company_id)
    result = decision_service.submit_envelope(
        db,
        _context(db, manager),
        decision_type=C.DecisionKind.decompose_project,
        reason="引用一个不存在的项目。",
        scope=f"company:{int(manager.company_id)}",
        actions=[{"tool": "create_task", "args": {"project_id": 999_999, "title": "X"}}],
    )
    assert result.status is DecisionStatus.failed


def test_status_machine_has_no_unknown_values():
    """状态机封闭：读面出现的状态必须都在 `DecisionStatus` 里。"""
    assert {s.value for s in DecisionStatus} == {
        "PROPOSED",
        "EXECUTING",
        "APPLIED",
        "PARTIALLY_APPLIED",
        "FAILED",
        "SUPERSEDED",
    }
    resolved = []
    for status in DecisionStatus:
        resolved.append(status)
    assert DecisionStatus.partially_applied in resolved


# ---------------------------------------------------------------------------
# DR7：decision_semantics 声明与执行
# ---------------------------------------------------------------------------


def test_decision_semantics_are_declared_and_enforced(db, default_company_id):
    """DR7：写工具必须声明语义；REQUIRED 无决策 ⇒ 拒绝；NONE 挂决策 ⇒ 拒绝。"""
    for spec in executor.registry.specs():
        if spec.is_read:
            assert spec.decision_semantics is DecisionSemantics.none, spec.name
        else:
            assert spec.decision_semantics is not DecisionSemantics.none, spec.name

    manager, _definition = _manager(db, default_company_id)
    project = _project(db, default_company_id, "DR7 语义")
    ctx = _context(db, manager)

    missing = executor.execute_tool(
        db,
        name="create_task",
        args={"project_id": int(project.id), "title": "无决策"},
        context=ctx,
    )
    assert missing.ok is False and missing.reason == "decision_required"

    from app.work import decisions as ds

    decision_id = int(
        ds.open_decision(
            db,
            ctx,
            decision_type=C.DecisionKind.decompose_project,
            reason="读工具不该挂决策",
            scope=f"project:{int(project.id)}",
        ).id
    )
    read_with_decision = executor.execute_tool(
        db,
        name="inspect_project",
        args={"project_id": int(project.id)},
        context=ctx,
        decision_id=decision_id,
    )
    assert read_with_decision.ok is False and read_with_decision.reason == "decision_not_allowed"

    # 注册时也要强制：写工具不声明语义 → 直接炸
    from app.work import tools as machinery

    with pytest.raises(machinery.ToolError):
        machinery.ToolSpec(
            name="silent_write",
            description="不声明决策语义的写工具",
            side_effect=ToolSideEffect.write,
            required_authority=C.AuthorityKind.assign_task,
            authority_target=lambda db, ctx, args: C.AuthorityTarget(),
            input_schema=machinery.object_schema({}),
            output_schema=machinery.object_schema({}),
            handler=lambda db, ctx, args: {},
        )
    with pytest.raises(machinery.ToolError):
        machinery.ToolSpec(
            name="read_with_semantics",
            description="读工具不许声明决策语义",
            side_effect=ToolSideEffect.read,
            decision_semantics=DecisionSemantics.optional,
            input_schema=machinery.object_schema({}),
            output_schema=machinery.object_schema({}),
            handler=lambda db, ctx, args: {},
        )


def test_declared_semantics_match_the_agreed_split():
    """第一批工具的语义划分固定（用户拍板 §6 的示例清单）。"""
    required = {
        spec.name
        for spec in executor.registry.by_side_effect(ToolSideEffect.write)
        if spec.decision_semantics is DecisionSemantics.required
    }
    optional = {
        spec.name
        for spec in executor.registry.by_side_effect(ToolSideEffect.write)
        if spec.decision_semantics is DecisionSemantics.optional
    }
    assert required == {
        "create_task",
        "create_dependency",
        "assign_task",
        "delegate_project",
        "request_rework",
        "cancel_task",
        # M2.6（H4）：改变执行**输入**是计划动作（与 create_dependency 同族）
        "consume_artifact",
    }
    assert optional == {"update_task", "request_review", "mark_task_blocked"}


# ---------------------------------------------------------------------------
# DR8：决策树
# ---------------------------------------------------------------------------


def test_parent_decision_forms_a_tree_without_a_workflow_model(db, default_company_id):
    """DR8：`parent_decision_id` 表达 CEO → CTO → Lead；**不**新建 workflow 模型。"""
    ceo, ceo_definition = _manager(db, default_company_id, "lab-ceo-d")
    cto, cto_definition = _lab(db, default_company_id, "lab-cto-d", slug="bob")
    _grant(db, cto_definition, *MANAGER_AUTHORITY)
    project = _project(db, default_company_id, "DR8 决策树")

    parent = decision_service.submit_envelope(
        db,
        _context(db, ceo),
        decision_type=C.DecisionKind.delegate_management,
        reason="技术型项目交给 CTO 组织。",
        scope=f"project:{int(project.id)}",
        actions=[
            {
                "tool": "delegate_project",
                "args": {"project_id": int(project.id), "to_employee_id": int(cto.id)},
            }
        ],
    )
    assert parent.status is DecisionStatus.applied, parent.outcomes

    child = decision_service.submit_envelope(
        db,
        _context(db, cto),
        decision_type=C.DecisionKind.decompose_project,
        reason="承接后拆成一条实现任务。",
        scope=f"project:{int(project.id)}",
        parent_decision_id=parent.decision_id,
        actions=[{"tool": "create_task", "args": {"project_id": int(project.id), "title": "实现"}}],
    )
    assert child.status is DecisionStatus.applied, child.outcomes

    child_row = _decision_row(db, child.decision_id)
    assert child_row.parent_decision_id == parent.decision_id
    view = decision_service.decision_view(db, _decision_row(db, parent.decision_id))
    assert view["child_decision_ids"] == [child.decision_id]
    # 没有第二套 workflow 模型
    tables = set(__import__("app.models", fromlist=["Base"]).Base.metadata.tables)
    assert not {name for name in tables if "workflow" in name}


def test_supersede_marks_the_old_decision_without_rewriting_it(db, default_company_id):
    """SUPERSEDED：原记录不改写，只记"被谁取代"（replan 的诚实表达）。"""
    manager, _definition = _manager(db, default_company_id)
    project = _project(db, default_company_id, "取代")
    ctx = _context(db, manager)
    first = decision_service.submit_envelope(
        db,
        ctx,
        decision_type=C.DecisionKind.decompose_project,
        reason="先建一条任务。",
        scope=f"project:{int(project.id)}",
        actions=[{"tool": "create_task", "args": {"project_id": int(project.id), "title": "旧"}}],
    )
    second = decision_service.submit_envelope(
        db,
        ctx,
        decision_type=C.DecisionKind.replan,
        reason="计划变了，重新拆。",
        scope=f"project:{int(project.id)}",
        actions=[{"tool": "create_task", "args": {"project_id": int(project.id), "title": "新"}}],
    )
    old = _decision_row(db, first.decision_id)
    reason_before = old.reason
    decision_service.supersede(db, old, by_decision_id=second.decision_id)
    assert old.status == DecisionStatus.superseded.value
    assert old.superseded_by_id == second.decision_id
    assert old.reason == reason_before, "取代不改写原决策的理由（W28）"


# ---------------------------------------------------------------------------
# DR9：上下文有界 + 哈希
# ---------------------------------------------------------------------------


def test_context_is_bounded_and_hashed(db, default_company_id):
    """DR9：键集封闭、哈希可重算；**不是**数据库副本。"""
    manager, _definition = _manager(db, default_company_id)
    project = _project(db, default_company_id, "DR9 上下文")
    ctx = _context(db, manager)
    result = decision_service.submit_envelope(
        db,
        ctx,
        decision_type=C.DecisionKind.decompose_project,
        reason="有界上下文",
        scope=f"project:{int(project.id)}",
        context={"project_id": int(project.id), "note": "two workstreams"},
        actions=[{"tool": "create_task", "args": {"project_id": int(project.id), "title": "T"}}],
    )
    decision = _decision_row(db, result.decision_id)
    assert set(decision.context_json) <= C.DECISION_CONTEXT_KEYS
    assert "authority" in decision.context_json, "授权快照由系统补（Agent 无法伪造）"
    assert decision.context_hash == decision_service.context_hash(decision.context_json)
    assert decision.acting_position_assignment_id is not None, "要留当时的任职行（换人后可解释）"
    assert decision.acting_position_code == "lab-decisions"


def test_unknown_context_keys_are_rejected(db, default_company_id):
    """DR9：未知键一律拒绝 —— 不许"顺手把整张表塞进 JSON"。"""
    manager, _definition = _manager(db, default_company_id)
    project = _project(db, default_company_id, "DR9 拒绝")
    with pytest.raises(decision_service.DecisionError):
        decision_service.submit_envelope(
            db,
            _context(db, manager),
            decision_type=C.DecisionKind.decompose_project,
            reason="带了一个不认识的键",
            scope=f"project:{int(project.id)}",
            context={"company_dump": {"everything": True}},
            actions=[
                {"tool": "create_task", "args": {"project_id": int(project.id), "title": "T"}}
            ],
        )


def test_context_has_no_wall_clock_and_is_stable(db, default_company_id):
    """哈希只吃事实：同一份上下文两次算出的摘要一致（不含时间）。"""
    payload = {"project_id": 3, "note": "x", "authority": {"grant_ids": [1, 2]}}
    assert decision_service.context_hash(payload) == decision_service.context_hash(dict(payload))
    assert decision_service.context_hash(payload) != decision_service.context_hash(
        {"project_id": 3, "note": "y", "authority": {"grant_ids": [1, 2]}}
    )


# ---------------------------------------------------------------------------
# 长生命周期决策（用户拍板 §9：不持有长事务）
# ---------------------------------------------------------------------------


def test_long_lived_decision_can_span_phases(db, default_company_id):
    """`open_decision` → 分阶段 `execute_actions` → `resolve_decision`（不持有长事务）。"""
    manager, _definition = _manager(db, default_company_id)
    project = _project(db, default_company_id, "长决策")
    ctx = _context(db, manager)
    decision = decision_service.open_decision(
        db,
        ctx,
        decision_type=C.DecisionKind.decompose_project,
        reason="先登记，再分两个阶段执行。",
        scope=f"project:{int(project.id)}",
    )
    assert decision.status == DecisionStatus.proposed.value
    assert decision.resolved_at is None

    first = decision_service.execute_actions(
        db,
        decision,
        [{"tool": "create_task", "args": {"project_id": int(project.id), "title": "阶段一"}}],
        ctx,
    )
    assert decision.status == DecisionStatus.executing.value, "执行中：还没到终态"
    assert all(item.ok for item in first)

    second = decision_service.execute_actions(
        db,
        decision,
        [{"tool": "create_task", "args": {"project_id": int(project.id), "title": "阶段二"}}],
        ctx,
    )
    all_outcomes = first + second
    decision_service.resolve_decision(db, decision, all_outcomes)
    assert decision.status == DecisionStatus.applied.value
    assert len(decision_service.tool_audits_for(db, int(decision.id))) == 2

    with pytest.raises(decision_service.DecisionError):
        decision_service.execute_actions(
            db,
            decision,
            [{"tool": "create_task", "args": {"project_id": int(project.id), "title": "第三阶段"}}],
            ctx,
        )


def test_envelope_requires_at_least_one_action(db, default_company_id):
    manager, _definition = _manager(db, default_company_id)
    with pytest.raises(decision_service.DecisionError):
        decision_service.submit_envelope(
            db,
            _context(db, manager),
            decision_type=C.DecisionKind.decompose_project,
            reason="空信封",
            scope=f"company:{int(manager.company_id)}",
            actions=[],
        )


def test_structure_is_validated_but_content_is_not(db, default_company_id):
    """W18/DR10：结构非法要拒；理由**内容**从不评价。"""
    manager, _definition = _manager(db, default_company_id)
    with pytest.raises(C.WorkContractError):
        decision_service.open_decision(
            db,
            _context(db, manager),
            decision_type=C.DecisionKind.decompose_project,
            reason="   ",
            scope=f"company:{int(manager.company_id)}",
        )
    with pytest.raises(C.WorkContractError):
        decision_service.open_decision(
            db,
            _context(db, manager),
            decision_type=C.DecisionKind.decompose_project,
            reason="形状不对的 scope",
            scope="galaxy:1",
        )
    # 内容再草率也接受（系统不评价管理判断）
    row = decision_service.open_decision(
        db,
        _context(db, manager),
        decision_type=C.DecisionKind.decompose_project,
        reason="I felt like it",
        scope=f"company:{int(manager.company_id)}",
    )
    assert row.reason == "I felt like it"


# ---------------------------------------------------------------------------
# DR10 / 观测 / 读面
# ---------------------------------------------------------------------------


def test_outcome_is_traceable_without_scoring(db, default_company_id):
    """DR10：Decision → Outcome 可追踪；**没有**任何能力/质量评分字段。"""
    manager, _definition = _manager(db, default_company_id)
    project = _project(db, default_company_id, "DR10 可追踪")
    result = decision_service.submit_envelope(
        db,
        _context(db, manager),
        decision_type=C.DecisionKind.decompose_project,
        reason="可追踪性",
        scope=f"project:{int(project.id)}",
        actions=[{"tool": "create_task", "args": {"project_id": int(project.id), "title": "T"}}],
    )
    view = decision_service.decision_view(db, _decision_row(db, result.decision_id))
    assert view["status"] == DecisionStatus.applied.value
    assert view["resolved_at"] is not None
    banned = ("score", "rating", "quality", "recommendation", "advice", "verdict")
    assert not [key for key in view if any(token in key for token in banned)]
    decision_columns = set(DecisionRecord.__table__.columns.keys())
    assert not [name for name in decision_columns if any(t in name for t in banned)]


def test_decision_read_api_and_audit_query(client, db, default_company_id):
    """读面：`GET /decisions` + `/{id}` + `/{id}/tool-audits`（反查执行事实）。"""
    manager, _definition = _manager(db, default_company_id)
    project = _project(db, default_company_id, "读面")
    result = decision_service.submit_envelope(
        db,
        _context(db, manager),
        decision_type=C.DecisionKind.decompose_project,
        reason="读面测试",
        scope=f"project:{int(project.id)}",
        actions=[
            {"tool": "create_task", "args": {"project_id": int(project.id), "title": "R1"}},
            {"tool": "create_task", "args": {"project_id": int(project.id), "title": "R2"}},
        ],
    )

    listed = client.get("/api/v1/decisions", params={"project_id": int(project.id)}).json()
    assert [row["decision_id"] for row in listed] == [result.decision_id]
    detail = client.get(f"/api/v1/decisions/{result.decision_id}").json()
    assert detail["decision_type"] == C.DecisionKind.decompose_project.value
    assert detail["action_summary"]["total"] == 2
    assert detail["context_hash"]
    audits = client.get(f"/api/v1/decisions/{result.decision_id}/tool-audits").json()
    assert len(audits) == 2
    assert {row["tool_name"] for row in audits} == {"create_task"}
    assert audits[0]["arguments"]["title"]
    assert audits[0]["authority_allowed"] is True
    assert audits[0]["authority_grant_ids"]
    stats = client.get("/api/v1/decisions/stats").json()
    assert stats["decisions_by_status"].get(DecisionStatus.applied.value, 0) >= 1


def test_decision_api_has_no_write_endpoint(client, api_paths):
    """决策**只读**：人类不直接写决策记录（写动作走各领域正式 API）。"""
    decision_paths = {path for path in api_paths if "/decisions" in path}
    assert decision_paths == {
        "/api/v1/decisions",
        "/api/v1/decisions/stats",
        "/api/v1/decisions/{decision_id}",
        "/api/v1/decisions/{decision_id}/tool-audits",
    }
    openapi = client.app.openapi()
    for path in decision_paths:
        methods = set(openapi["paths"][path])
        assert methods == {"get"}, f"{path} 竟然有写方法：{sorted(methods)}"


def test_decision_api_is_company_scoped(client, db, default_company_id):
    manager, _definition = _manager(db, default_company_id)
    other_project = project_repo.create_project(
        db,
        company_id=default_company_id + 777,
        name="别家公司",
        description="",
        status="in_progress",
        source_order_text="",
    )
    db.commit()
    result = decision_service.submit_envelope(
        db,
        _context(db, manager),
        decision_type=C.DecisionKind.decompose_project,
        reason="本公司决策",
        scope=f"company:{int(default_company_id)}",
        actions=[
            {"tool": "create_task", "args": {"project_id": int(other_project.id), "title": "越界"}}
        ],
    )
    # 动作被领域拒绝（跨公司），但决策本身属于本公司 ⇒ 读得到
    assert result.status is DecisionStatus.failed
    assert client.get(f"/api/v1/decisions/{result.decision_id}").status_code == 200
    foreign = DecisionRecord(
        company_id=int(default_company_id) + 777,
        actor_employee_id=int(manager.id),
        decision_type=C.DecisionKind.decompose_project.value,
        scope="company:0",
        reason="别家公司",
    )
    db.add(foreign)
    db.commit()
    assert client.get(f"/api/v1/decisions/{foreign.id}").status_code == 404


def test_decision_stats_endpoint_is_read_only(client, api_paths):
    assert "/api/v1/decisions/stats" in api_paths
    body = client.get("/api/v1/decisions/stats").json()
    assert set(body) == {
        "decisions_by_status",
        "tool_audits_by_outcome",
        "tool_audits_by_decision_semantics",
    }


# ---------------------------------------------------------------------------
# tool_audits 取代 audit_logs 作为工具事实的落点
# ---------------------------------------------------------------------------


def test_tool_facts_live_in_tool_audits_not_audit_logs(db, default_company_id):
    """同一个事实只留一个落点：`tool.*` 不再写进 `audit_logs`。"""
    from app.models.lifecycle import AuditLog

    manager, _definition = _manager(db, default_company_id)
    project = _project(db, default_company_id, "审计落点")
    before = int(db.scalar(select(func.count()).select_from(AuditLog)) or 0)
    result = decision_service.submit_envelope(
        db,
        _context(db, manager),
        decision_type=C.DecisionKind.decompose_project,
        reason="审计落点",
        scope=f"project:{int(project.id)}",
        actions=[{"tool": "create_task", "args": {"project_id": int(project.id), "title": "A"}}],
    )
    assert result.status is DecisionStatus.applied
    after = int(db.scalar(select(func.count()).select_from(AuditLog)) or 0)
    assert after == before, "工具事实不该再写进 audit_logs"
    assert int(db.scalar(select(func.count()).select_from(ToolAudit)) or 0) >= 1


def test_tool_audit_keeps_authority_evidence_for_replay(db, default_company_id):
    """执行事实留够证据：命中的 grant id + 当时的授权摘要（可重算对拍）。"""
    manager, _definition = _manager(db, default_company_id)
    project = _project(db, default_company_id, "授权证据")
    result = decision_service.submit_envelope(
        db,
        _context(db, manager),
        decision_type=C.DecisionKind.decompose_project,
        reason="授权证据",
        scope=f"project:{int(project.id)}",
        actions=[{"tool": "create_task", "args": {"project_id": int(project.id), "title": "A"}}],
    )
    audit = decision_service.tool_audits_for(db, result.decision_id)[0]
    assert audit.authority_allowed is True
    assert audit.authority_reason == "authorized"
    assert audit.authority_grant_ids, "要留下是哪几条授权批的"
    # `authority_grants_hash` 是**这次动作用到的那几条**授权的摘要（不是整个职位的全部授权）
    actor = authority_service.resolve_actor_authority(db, int(manager.id))
    matched = tuple(
        grant.to_contract()
        for grant in actor.grants
        if grant.grant_id in set(audit.authority_grant_ids)
    )
    assert audit.authority_grants_hash == C.hash_effective_grants(matched)
    assert audit.authority_grants_hash != C.hash_effective_grants(actor.contract_grants) or len(
        matched
    ) == len(actor.grants)
    assert audit.started_at is not None and audit.finished_at is not None
