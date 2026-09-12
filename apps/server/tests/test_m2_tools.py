"""M2.3 Management Agent Tooling —— 契约与行为测试（T1–T12）。

三块：

1. **工具机制**：注册表完备性、参数校验、身份字段拒绝、自主等级门禁、公司作用域。
2. **执行面**：Authority 在内部面照样生效（T4）、Actor 由上下文注入（T5）、
   成功 = 决定 + 校验 + 应用（T7）、每次调用都留审计（T12）。
3. **边界**：没有玩家面 `/tools` 写路由（T3）、工具不拥有业务真相（T1）、
   HTTP 与 Tool 共用同一应用服务（T2/T11）、资源包不等于授权（T9）、
   transport 不改变领域不变量（T10）。

纪律：授权行为用例用**独立职位定义**（`_lab`）隔离 grant 状态 —— 共享库 + append-only
的授权会让"别的用例的授权"把断言兜住（M2.2 踩过一次）。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.models import Base
from app.models.base import utcnow
from app.models.decision import ToolAudit
from app.models.enums import (
    AuthorityScopeKind,
    AutonomyLevel,
    DecisionSemantics,
    LifecycleStatus,
    TaskKind,
    TaskStatus,
    ToolSideEffect,
    ToolTransport,
)
from app.models.organization import Employee
from app.models.position import (
    PositionAuthorityGrant,
    PositionDefinition,
    PositionDefinitionPackage,
)
from app.models.project import Task
from app.repositories import organization as org_repo
from app.repositories import project as project_repo
from app.schemas.position import AssignmentIn, PositionDefinitionIn, SlotIn
from app.services import position_service
from app.services import tasks as task_service
from app.work import authority as authority_service
from app.work import authority_seed, tool_reads, tool_writes
from app.work import contracts as C
from app.work import tool_executor as executor
from app.work import tools as machinery

SERVER_ROOT = Path(__file__).resolve().parents[1]
APP = SERVER_ROOT / "app"
TOOLS_MODULE = APP / "work" / "tools.py"
READS_MODULE = APP / "work" / "tool_reads.py"
WRITES_MODULE = APP / "work" / "tool_writes.py"
EXECUTOR_MODULE = APP / "work" / "tool_executor.py"


def _ast(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _imports_service(module: str, name: str, path: Path) -> bool:
    """`from app.services import tasks` 与 `from app.services.tasks import X` 都算复用。"""
    for node in ast.walk(_ast(path)):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module == f"{module}.{name}":
            return True
        if node.module == module and any(alias.name == name for alias in node.names):
            return True
    return False


def _called_attrs(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                names.add(func.attr)
            elif isinstance(func, ast.Name):
                names.add(func.id)
    return names


#: 本文件会动**组织事实**（任职时间轴 / 生命周期）—— 每个用例后自动还原，
#: 否则会污染后续用例（实测踩到：把某人 lifecycle 改成 suspended，
#: 后跑的 roster 用例期望 available）。
@pytest.fixture(autouse=True)
def _restore_org_state(org_snapshot):
    yield org_snapshot


def _employees(db, company_id: int) -> dict[str, Employee]:
    return {row.slug: row for row in org_repo.list_employees(db, company_id)}


def _lab(db, company_id: int, code: str, slug: str = "charlie"):
    """隔离实验台：新职位定义 + 编制 + 任职（不依赖任何默认授权）。"""
    employee = _employees(db, company_id)[slug]
    position_service.release_position(db, employee, reason="m2.3 lab")
    db.flush()
    definition = position_service.create_definition(
        db,
        PositionDefinitionIn(code=code, name=f"Tool Lab {code}", job_family="engineering", level=3),
        company_id=company_id,
    )
    db.flush()
    slot = position_service.open_slots(
        db,
        int(definition.id),
        SlotIn(department_id=int(employee.department_id), count=1, note="m2.3 lab"),
        company_id,
    )[0]
    position_service.assign_position(
        db, employee, AssignmentIn(slot_id=int(slot.id), reason="m2.3 lab", kind="assign")
    )
    db.commit()
    return employee, definition


def _grant(db, definition: PositionDefinition, *kinds: C.AuthorityKind, max_amount: int = 1000):
    for kind in kinds:
        authority_service.grant_authority(
            db,
            position_definition_id=int(definition.id),
            kind=kind,
            scope_kind=AuthorityScopeKind.company,
            # 金额类授权必须声明上限（default-deny：没有上限 = 无法确认在授权内）
            max_amount=max_amount if kind in C.AMOUNT_BEARING_AUTHORITIES else None,
            commit=False,
        )
    db.commit()


def _context(db, employee: Employee):
    return executor.context_for_employee(db, employee, origin="test")


def _decision_id(db, ctx, *, scope: str = "") -> int:
    """为 REQUIRED 类写工具开一条决策（DR7：管理动作必须隶属决策）。

    工具机制测试关心的是"授权/传输/审计"是否正确，不关心决策内容 ——
    所以这里开一条最小决策，把 decision_id 传给工具调用即可。
    """
    from app.work import decisions

    return int(
        decisions.open_decision(
            db,
            ctx,
            decision_type=C.DecisionKind.assign_task,
            reason="test fixture decision",
            scope=scope or f"company:{ctx.company_id}",
        ).id
    )


def _project(db, company_id: int, name: str = "Tool 项目"):
    project = project_repo.create_project(
        db,
        company_id=company_id,
        name=name,
        description="M2.3 测试项目",
        status="in_progress",
        source_order_text="M2.3 测试项目",
    )
    db.commit()
    return project


def _task(db, project_id: int, title: str = "既有任务") -> Task:
    task = task_service.create_task(
        db,
        project_id=project_id,
        title=title,
        kind=TaskKind.development.value,
        assignee_id=None,
        status=TaskStatus.backlog.value,
    )
    db.commit()
    return task


# ---------------------------------------------------------------------------
# T1 · 工具不拥有业务真相
# ---------------------------------------------------------------------------


def test_tools_do_not_own_business_truth():
    """T1：工具是适配器 —— 不自建表、不直接写库、不缓存业务状态。"""
    tables = set(Base.metadata.tables)
    # `tool_audits` 是**执行事实**（M2.4 / DR1），不是业务真相；真正要禁的是
    # "工具自己长出领域状态表"（那样工具就成了第二个真相源）。
    assert not {
        name for name in tables if name.startswith(("agent_tool", "tool_state", "tool_registry"))
    }, "出现了工具自己的状态表 —— 工具不拥有业务真相"
    audited = set(Base.metadata.tables["tool_audits"].columns.keys())
    assert not (audited & {"title", "task_title", "project_status", "assignee_id"}), (
        "tool_audits 复制了领域状态字段 —— 它只该记执行事实"
    )
    for path in (READS_MODULE, WRITES_MODULE):
        called = _called_attrs(_ast(path))
        offenders = called & {"add", "delete", "flush", "commit_stub"}
        assert not offenders, f"{path.name} 直接写库（应经 `: {sorted(offenders)}`）"
    # 写工具必须调用既有领域服务（而不是自己实现规则）
    assert _imports_service("app.services", "tasks", WRITES_MODULE), "写工具必须复用 task service"
    write_calls = _called_attrs(_ast(WRITES_MODULE))
    assert {"create_task", "transition_task"} <= write_calls


# ---------------------------------------------------------------------------
# T2 / T11 · 与 HTTP 共用同一应用服务
# ---------------------------------------------------------------------------


def test_read_tools_reuse_the_same_query_services_as_http(client, db, default_company_id):
    """T2：读工具与 HTTP 读面产出**同一批事实**（不是第二套读实现）。"""
    project = _project(db, default_company_id, "T2 一致性")
    employee, _definition = _lab(db, default_company_id, "lab-t2")
    context = _context(db, employee)

    # inspect_project ↔ GET /projects/{id}
    tool_project = executor.execute_tool(
        db, name="inspect_project", args={"project_id": int(project.id)}, context=context
    )
    http_project = client.get(f"/api/v1/projects/{int(project.id)}").json()
    assert tool_project.ok
    assert tool_project.data["project"]["name"] == http_project["name"]
    assert tool_project.data["project"]["status"] == http_project["status"]
    assert tool_project.data["spec"]["goal"] == http_project["goal"]
    assert tool_project.data["spec"]["context"] == http_project["description"]

    # inspect_position ↔ GET /organizations/definitions（同一个 definitions_out 出口）
    tool_position = executor.execute_tool(
        db, name="inspect_position", args={"position_code": "lab-t2"}, context=context
    )
    http_positions = client.get("/api/v1/organizations/definitions").json()
    same_row = [row for row in http_positions if row["code"] == "lab-t2"]
    assert tool_position.ok and same_row
    assert tool_position.data["positions"][0]["slot_count"] == same_row[0]["slot_count"]
    assert tool_position.data["positions"][0]["vacant_count"] == same_row[0]["vacant_count"]


def test_human_and_agent_paths_produce_equivalent_domain_effects(db, default_company_id):
    """T11：人类 API 与 Agent 工具调用**同一个**应用服务 ⇒ 领域效果等价。

    做法：同一个应用服务，两条入口各造一个任务，逐字段对拍。
    """
    employee, definition = _lab(db, default_company_id, "lab-t11")
    _grant(db, definition, C.AuthorityKind.plan_project_work)
    project = _project(db, default_company_id, "T11 等价性")
    context = _context(db, employee)

    via_tool = executor.execute_tool(
        db,
        name="create_task",
        args={
            "project_id": int(project.id),
            "title": "等价性任务",
            "kind": TaskKind.development.value,
            "acceptance_criteria": "可复现",
            "priority": 3,
        },
        context=context,
        decision_id=_decision_id(db, context),
    )
    assert via_tool.ok, via_tool.error

    # 人类/领域侧：直接调同一个应用服务
    human_task = task_service.create_task(
        db,
        project_id=int(project.id),
        title="等价性任务",
        kind=TaskKind.development.value,
        assignee_id=None,
        status=TaskStatus.backlog.value,
        acceptance_criteria="可复现",
        priority=3,
    )
    db.commit()
    agent_task = project_repo.get_task(db, int(via_tool.data["task"]["task_id"]))
    assert agent_task is not None
    for field_name in ("kind", "status", "acceptance_criteria", "priority", "assignee_id"):
        assert getattr(agent_task, field_name) == getattr(human_task, field_name), field_name
    assert agent_task.title == human_task.title


def test_http_task_router_and_tools_share_one_service_module():
    """T11（结构面）：HTTP 路由与工具都只经 `app.services.tasks`，没有第二套规则。"""
    http_router = APP / "api" / "v1" / "tasks.py"
    assert _imports_service("app.services", "tasks", http_router), "HTTP 路由必须走同一应用服务"
    assert _imports_service("app.services", "tasks", WRITES_MODULE), "工具也必须走同一应用服务"


# ---------------------------------------------------------------------------
# T3 · 没有玩家面 /tools 写路由
# ---------------------------------------------------------------------------


def test_no_player_facing_tool_router_exists(client):
    """T3：不得存在通用玩家面 `/tools` 写接口。"""
    routes = {getattr(route, "path", "") for route in client.app.routes}
    offenders = sorted(path for path in routes if "/tools" in path)
    assert not offenders, f"出现了玩家面工具路由：{offenders}"

    sources = "\n".join(path.read_text(encoding="utf-8") for path in (APP / "api").rglob("*.py"))
    assert 'prefix="/tools"' not in sources
    assert "tool_executor" not in sources, "API 层不得直接接工具执行面（写工具没有 HTTP 面）"


# ---------------------------------------------------------------------------
# 注册表完备性 / T4 结构面
# ---------------------------------------------------------------------------


def test_registry_is_sound_and_write_specs_declare_authority_and_target():
    """T4：非读工具必须同时声明 `required_authority` 与 `authority_target`。"""
    machinery.assert_registry_is_sound(executor.registry)
    read_specs = executor.registry.by_side_effect(ToolSideEffect.read)
    write_specs = executor.registry.by_side_effect(ToolSideEffect.write)
    assert len(read_specs) >= 12, "读工具第一批至少 12 个"
    assert len(write_specs) >= 9, "写工具第一批至少 9 个"
    for spec in read_specs:
        assert spec.required_authority is None and spec.authority_target is None
    for spec in write_specs:
        assert spec.required_authority is not None
        assert spec.authority_target is not None
        assert spec.autonomy is AutonomyLevel.auto_allowed


def test_declaring_a_write_tool_without_authority_is_impossible():
    """T4（反例）：漏声明授权在**注册时**就炸，而不是运行时悄悄放行。"""
    with pytest.raises(machinery.ToolError):
        machinery.ToolSpec(
            name="unsafe_write",
            description="缺少授权声明",
            side_effect=ToolSideEffect.write,
            decision_semantics=DecisionSemantics.optional,
            input_schema=machinery.object_schema({}),
            output_schema=machinery.object_schema({}),
            handler=lambda db, ctx, args: {},
        )
    with pytest.raises(machinery.ToolError):
        machinery.ToolSpec(
            name="read_with_authority",
            description="读工具不该声明授权",
            side_effect=ToolSideEffect.read,
            input_schema=machinery.object_schema({}),
            output_schema=machinery.object_schema({}),
            required_authority=C.AuthorityKind.assign_task,
            authority_target=lambda db, ctx, args: C.AuthorityTarget(),
            handler=lambda db, ctx, args: {},
        )


def test_high_impact_tools_are_deliberately_absent():
    """用户拍板 §9/§10：等级先冻结，M2.3 **不**写空业务。"""
    assert executor.registry.by_side_effect(ToolSideEffect.high_impact) == ()
    info = executor.describe_tools()
    assert info["high_impact_count"] == 0
    assert "spend_credits" in info["reserved_high_impact_authorities"]
    assert info["autonomy_by_side_effect"]["high_impact"] == "requires_confirmation"


def test_autonomy_gate_refuses_actions_requiring_confirmation(db, default_company_id):
    """用户拍板 §11：`requires_confirmation` 在 M2.3 没有确认通道 ⇒ **拒绝执行**。"""
    called: list[str] = []
    scratch = machinery.ToolRegistry()
    scratch.register(
        machinery.ToolSpec(
            name="fake_high_impact",
            description="测试用：高影响动作必须被自主等级门禁挡住",
            side_effect=ToolSideEffect.high_impact,
            # optional：让"自主等级门禁"成为第一个拒绝者（正是本用例要测的那一道）
            decision_semantics=DecisionSemantics.optional,
            required_authority=C.AuthorityKind.spend_credits,
            authority_target=lambda db, ctx, args: C.AuthorityTarget(company_id=ctx.company_id),
            amount_arg="amount",
            input_schema=machinery.object_schema({"amount": {"type": "integer"}}, ("amount",)),
            output_schema=machinery.object_schema({}),
            handler=lambda db, ctx, args: called.append("handler") or {},
        )
    )
    employee, definition = _lab(db, default_company_id, "lab-autonomy")
    _grant(db, definition, C.AuthorityKind.spend_credits)
    result = executor.execute_tool(
        db,
        name="fake_high_impact",
        args={"amount": 10},
        context=_context(db, employee),
        registry_override=scratch,
    )
    assert result.ok is False
    assert result.reason == "autonomy_requires_confirmation"
    assert called == [], "自主等级不允许时 handler 绝不能被执行"


# ---------------------------------------------------------------------------
# T4 行为面 / T8 / T9：授权来自 grant 表
# ---------------------------------------------------------------------------


def test_internal_transport_still_enforces_authority(db, default_company_id):
    """T4：内部面照样拒绝无授权调用，且**领域状态不被改动**。"""
    employee, _definition = _lab(db, default_company_id, "lab-noauth")
    project = _project(db, default_company_id, "T4 无授权")
    before = int(db.scalar(select(func.count()).select_from(Task)) or 0)
    result = executor.execute_tool(
        db,
        name="create_task",
        args={"project_id": int(project.id), "title": "不该被建出来"},
        context=_context(db, employee),
        decision_id=_decision_id(db, _context(db, employee)),
    )
    assert result.ok is False and result.reason == "not_authorized"
    assert "no_grant" in result.error
    assert int(db.scalar(select(func.count()).select_from(Task)) or 0) == before


def test_authority_source_is_the_grant_table(db, default_company_id):
    """T8：`position_authority_grants` 是授权的唯一来源（撤回即刻失效）。"""
    employee, definition = _lab(db, default_company_id, "lab-t8")
    _grant(db, definition, C.AuthorityKind.plan_project_work)
    project = _project(db, default_company_id, "T8 授权来源")
    context = _context(db, employee)

    first = executor.execute_tool(
        db,
        name="create_task",
        args={"project_id": int(project.id), "title": "A"},
        context=context,
        decision_id=_decision_id(db, context),
    )
    assert first.ok is True

    grants = list(
        db.scalars(
            select(PositionAuthorityGrant).where(
                PositionAuthorityGrant.position_definition_id == int(definition.id),
                PositionAuthorityGrant.effective_to.is_(None),
            )
        )
    )
    for row in grants:
        authority_service.revoke_authority(db, grant_id=int(row.id), reason="t8")

    second = executor.execute_tool(
        db,
        name="create_task",
        args={"project_id": int(project.id), "title": "B"},
        context=context,
        decision_id=_decision_id(db, context),
    )
    assert second.ok is False and second.reason == "not_authorized"


def test_resource_packages_do_not_grant_authority(db, default_company_id):
    """T9：资源开通包**不是**授权来源。给满包也不会让工具可用。"""
    employee, definition = _lab(db, default_company_id, "lab-t9")
    project = _project(db, default_company_id, "T9 资源包")
    # 塞一条 package 关系（资源开通语义），授权依然为空 ⇒ 依然拒绝
    from app.models.lifecycle import AccessPackage

    package = db.scalar(select(AccessPackage).limit(1))
    if package is not None:
        db.add(
            PositionDefinitionPackage(
                position_definition_id=int(definition.id), package_id=int(package.id)
            )
        )
        db.commit()
    result = executor.execute_tool(
        db,
        name="create_task",
        args={"project_id": int(project.id), "title": "C"},
        context=_context(db, employee),
        decision_id=_decision_id(db, _context(db, employee)),
    )
    assert result.ok is False and result.reason == "not_authorized"


# ---------------------------------------------------------------------------
# T5 · Actor 身份由上下文注入
# ---------------------------------------------------------------------------


def test_actor_identity_comes_from_context_and_args_are_rejected(db, default_company_id):
    """T5：参数里的身份字段一律拒绝；审计记录的是**上下文里的** actor。"""
    employee, definition = _lab(db, default_company_id, "lab-t5")
    _grant(db, definition, C.AuthorityKind.plan_project_work)
    project = _project(db, default_company_id, "T5 身份")
    other = _employees(db, default_company_id)["bob"]
    before_id = int(db.scalar(select(func.max(ToolAudit.id))) or 0)

    spoofed = executor.execute_tool(
        db,
        name="create_task",
        args={
            "project_id": int(project.id),
            "title": "伪造身份",
            "actor_employee_id": int(other.id),
        },
        context=_context(db, employee),
        decision_id=_decision_id(db, _context(db, employee)),
    )
    assert spoofed.ok is False and spoofed.reason == "invalid_arguments"
    assert "identity" in spoofed.error

    audit = db.scalar(
        select(ToolAudit).where(ToolAudit.id > before_id).order_by(ToolAudit.id.desc())
    )
    assert audit is not None and audit.actor_employee_id == int(employee.id)
    assert audit.outcome == "invalid_arguments"


def test_actor_identity_for_work_session_requires_a_running_session(db, default_company_id):
    """T5（续）：Runtime 路径的身份来自 WorkSession —— 已结束的会话不得发起组织动作。"""
    employee, definition = _lab(db, default_company_id, "lab-t5b")
    _grant(db, definition, C.AuthorityKind.plan_project_work)
    project = _project(db, default_company_id, "T5 会话")
    task = _task(db, int(project.id))
    session = project_repo.create_work_session(
        db,
        task_id=int(task.id),
        employee_id=int(employee.id),
        runtime_type="mock",
        status="running",
        started_at=utcnow(),
    )
    db.commit()
    context = executor.context_for_work_session(db, int(session.id))
    assert context.employee_id == int(employee.id)
    assert context.task_id == int(task.id)
    assert context.project_id == int(project.id)
    assert context.origin == "work_session"

    session.status = "completed"
    db.commit()
    with pytest.raises(executor.ToolExecutionError) as excinfo:
        executor.context_for_work_session(db, int(session.id))
    assert excinfo.value.reason == "work_session_not_running"


# ---------------------------------------------------------------------------
# T6 · 读工具只给事实
# ---------------------------------------------------------------------------


def test_calculate_task_fit_returns_facts_without_ranking(db, default_company_id):
    """T6 / W11：没有 rank / recommended / best；未知不当 0；顺序按 employee_id。"""
    employee, _definition = _lab(db, default_company_id, "lab-t6")
    project = _project(db, default_company_id, "T6 fit")
    task = _task(db, int(project.id))
    result = executor.execute_tool(
        db,
        name="calculate_task_fit",
        args={"task_id": int(task.id)},
        context=_context(db, employee),
    )
    assert result.ok, result.error
    data = result.data
    assert data["result_kind"] == "facts_only"
    assert data["requirement_source"] == "evidence.policy.TASK_KIND_HINTS"
    ids = [row["employee_id"] for row in data["candidates"]]
    assert ids == sorted(ids), "候选必须按 employee_id 排序 —— 按分数排就是推荐"
    assert not (set(data) & {"ranking", "recommended", "best", "top_candidate", "suggestion"})
    for row in data["candidates"]:
        assert not (set(row) & {"rank", "recommended", "is_best", "advice"})
        for item in row["competencies"]:
            # Unknown != Bad：未知项 score 必须是 null，而不是 0
            if item["known"] is False:
                assert item["score"] is None


# ---------------------------------------------------------------------------
# T7 · 成功 = 决定 + 校验 + 应用
# ---------------------------------------------------------------------------


def test_successful_write_is_decided_validated_and_applied(db, default_company_id):
    """T7：成功结果必然带"凭什么"（authority）与"落在哪条审计"（audit_id）。"""
    employee, definition = _lab(db, default_company_id, "lab-t7")
    _grant(db, definition, C.AuthorityKind.plan_project_work)
    project = _project(db, default_company_id, "T7 应用")
    result = executor.execute_tool(
        db,
        name="create_task",
        args={"project_id": int(project.id), "title": "被应用的任务", "priority": 5},
        context=_context(db, employee),
        decision_id=_decision_id(db, _context(db, employee)),
    )
    assert result.ok is True and result.reason == "applied"
    assert result.authority is not None and result.authority["allowed"] is True
    assert result.authority["grant_ids"], "必须记录是哪一条授权批的"
    assert result.audit_id is not None

    created = project_repo.get_task(db, int(result.data["task"]["task_id"]))
    assert created is not None
    assert created.status == TaskStatus.backlog.value
    assert created.title == "被应用的任务"


def test_assign_task_applies_state_machine_and_audits(db, default_company_id):
    """指派：backlog → todo（状态机唯一合法路径）+ 事件 + 审计。"""
    employee, definition = _lab(db, default_company_id, "lab-assign")
    _grant(db, definition, C.AuthorityKind.plan_project_work, C.AuthorityKind.assign_task)
    project = _project(db, default_company_id, "指派")
    task = _task(db, int(project.id))
    target = _employees(db, default_company_id)["bob"]

    result = executor.execute_tool(
        db,
        name="assign_task",
        args={"task_id": int(task.id), "employee_id": int(target.id)},
        context=_context(db, employee),
        decision_id=_decision_id(db, _context(db, employee)),
    )
    assert result.ok, result.error
    db.refresh(task)
    assert task.assignee_id == int(target.id)
    assert task.status == TaskStatus.todo.value


def test_assign_task_rejects_non_active_target(db, default_company_id):
    """生命周期冲突是硬约束：停用/离职中的人不能被派活。"""
    employee, definition = _lab(db, default_company_id, "lab-lifecycle")
    _grant(db, definition, C.AuthorityKind.plan_project_work, C.AuthorityKind.assign_task)
    project = _project(db, default_company_id, "生命周期")
    task = _task(db, int(project.id))
    target = _employees(db, default_company_id)["dana"]
    target.lifecycle_status = LifecycleStatus.suspended.value
    db.commit()

    result = executor.execute_tool(
        db,
        name="assign_task",
        args={"task_id": int(task.id), "employee_id": int(target.id)},
        context=_context(db, employee),
        decision_id=_decision_id(db, _context(db, employee)),
    )
    assert result.ok is False and result.reason == "domain_rejected"
    assert "not active" in result.error
    db.refresh(task)
    assert task.assignee_id is None


def test_create_dependency_rejects_cycles(db, default_company_id):
    """DAG 正确性是系统职责（W16）：环在工具层就被挡住。"""
    employee, definition = _lab(db, default_company_id, "lab-dag")
    _grant(db, definition, C.AuthorityKind.plan_project_work)
    project = _project(db, default_company_id, "DAG")
    first = _task(db, int(project.id), "A")
    second = _task(db, int(project.id), "B")
    context = _context(db, employee)

    assert (
        executor.execute_tool(
            db,
            name="create_dependency",
            args={"task_id": int(second.id), "depends_on_id": int(first.id)},
            context=context,
            decision_id=_decision_id(db, context),
        ).ok
        is True
    )
    cycle = executor.execute_tool(
        db,
        name="create_dependency",
        args={"task_id": int(first.id), "depends_on_id": int(second.id)},
        context=context,
        decision_id=_decision_id(db, context),
    )
    assert cycle.ok is False and cycle.reason == "domain_rejected"
    self_loop = executor.execute_tool(
        db,
        name="create_dependency",
        args={"task_id": int(first.id), "depends_on_id": int(first.id)},
        context=context,
        decision_id=_decision_id(db, context),
    )
    assert self_loop.ok is False


def test_request_rework_requires_a_reason_and_returns_to_todo(db, default_company_id):
    employee, definition = _lab(db, default_company_id, "lab-rework")
    _grant(db, definition, C.AuthorityKind.request_rework)
    project = _project(db, default_company_id, "返工")
    task = _task(db, int(project.id))
    task.status = TaskStatus.in_review.value
    db.commit()
    context = _context(db, employee)

    missing_reason = executor.execute_tool(
        db,
        name="request_rework",
        args={"task_id": int(task.id)},
        context=_context(db, employee),
        decision_id=_decision_id(db, _context(db, employee)),
    )
    assert missing_reason.ok is False and missing_reason.reason == "invalid_arguments"

    result = executor.execute_tool(
        db,
        name="request_rework",
        args={"task_id": int(task.id), "reason": "缺少边界用例"},
        context=context,
        decision_id=_decision_id(db, context),
    )
    assert result.ok, result.error
    db.refresh(task)
    assert task.status == TaskStatus.todo.value


def test_mark_blocked_and_cancel_are_honest_states(db, default_company_id):
    employee, definition = _lab(db, default_company_id, "lab-states")
    _grant(db, definition, C.AuthorityKind.plan_project_work)
    project = _project(db, default_company_id, "状态")
    blocked_task = _task(db, int(project.id), "会被阻塞")
    blocked_task.status = TaskStatus.in_progress.value
    cancelled_task = _task(db, int(project.id), "会被取消")
    done_task = _task(db, int(project.id), "已经完成")
    done_task.status = TaskStatus.done.value
    db.commit()
    context = _context(db, employee)

    blocked = executor.execute_tool(
        db,
        name="mark_task_blocked",
        args={"task_id": int(blocked_task.id), "reason": "等外部输入"},
        context=context,
    )
    assert blocked.ok, blocked.error
    db.refresh(blocked_task)
    assert blocked_task.status == TaskStatus.blocked.value

    cancelled = executor.execute_tool(
        db,
        name="cancel_task",
        args={"task_id": int(cancelled_task.id), "reason": "计划变了"},
        context=context,
        decision_id=_decision_id(db, context),
    )
    assert cancelled.ok, cancelled.error
    db.refresh(cancelled_task)
    assert cancelled_task.status == TaskStatus.cancelled.value

    # 终态不可改写：已经做完的工作不能被"取消"（历史不改写）
    refused = executor.execute_tool(
        db,
        name="cancel_task",
        args={"task_id": int(done_task.id), "reason": "手滑"},
        context=context,
        decision_id=_decision_id(db, context),
    )
    assert refused.ok is False and refused.reason == "domain_rejected"
    db.refresh(done_task)
    assert done_task.status == TaskStatus.done.value


def test_delegate_project_moves_only_the_management_pointer(db, default_company_id):
    employee, definition = _lab(db, default_company_id, "lab-delegate")
    _grant(db, definition, C.AuthorityKind.delegate_management)
    project = _project(db, default_company_id, "委派")
    target = _employees(db, default_company_id)["morgan"]

    result = executor.execute_tool(
        db,
        name="delegate_project",
        args={
            "project_id": int(project.id),
            "to_employee_id": int(target.id),
            "note": "技术型项目",
        },
        context=_context(db, employee),
        decision_id=_decision_id(db, _context(db, employee)),
    )
    assert result.ok, result.error
    db.refresh(project)
    assert project.management_employee_id == int(target.id)
    assert project.management_person_id == int(target.person_id)
    assert project.management_assigned_at is not None
    assert project.owner_id != int(target.id), "委派不改 owner（那是既有的项目负责人字段）"


# ---------------------------------------------------------------------------
# 作用域 / 公司隔离
# ---------------------------------------------------------------------------


def test_write_tools_are_company_scoped(db, default_company_id):
    """跨公司一律"不存在"：不泄露存在性，也不允许跨公司动作。"""
    employee, definition = _lab(db, default_company_id, "lab-scope")
    _grant(db, definition, C.AuthorityKind.plan_project_work)
    other_project = project_repo.create_project(
        db,
        company_id=default_company_id + 777,
        name="别家公司的项目",
        description="",
        status="in_progress",
        source_order_text="",
    )
    db.commit()
    result = executor.execute_tool(
        db,
        name="create_task",
        args={"project_id": int(other_project.id), "title": "越界"},
        context=_context(db, employee),
        decision_id=_decision_id(db, _context(db, employee)),
    )
    assert result.ok is False and result.reason == "domain_rejected"
    assert "not found" in result.error


def test_read_tools_are_company_scoped(db, default_company_id):
    employee, _definition = _lab(db, default_company_id, "lab-readscope")
    other_project = project_repo.create_project(
        db,
        company_id=default_company_id + 777,
        name="别家公司",
        description="",
        status="in_progress",
        source_order_text="",
    )
    db.commit()
    result = executor.execute_tool(
        db,
        name="inspect_project",
        args={"project_id": int(other_project.id)},
        context=_context(db, employee),
    )
    assert result.ok is False and result.reason == "domain_rejected"


# ---------------------------------------------------------------------------
# T10 · Transport 不改变领域不变量
# ---------------------------------------------------------------------------


def test_transport_does_not_change_domain_invariants(db, default_company_id, monkeypatch):
    """T10：调试口与内部面走同一段代码 —— 开关只决定"能不能发起"。"""
    employee, definition = _lab(db, default_company_id, "lab-t10")
    project = _project(db, default_company_id, "T10 transport")
    context = _context(db, employee)

    # 默认关：调试口被拒（但没有授权也进不来，见下）
    monkeypatch.setattr(settings, "agent_tool_cli_enabled", False)
    disabled = executor.execute_tool(
        db,
        name="create_task",
        args={"project_id": int(project.id), "title": "X"},
        context=context,
        transport=ToolTransport.debug_cli,
        decision_id=_decision_id(db, context),
    )
    assert disabled.ok is False and disabled.reason == "cli_disabled"

    # 打开调试口，但**没有授权** ⇒ 仍然拒绝（transport 不代表信任）
    monkeypatch.setattr(settings, "agent_tool_cli_enabled", True)
    still_denied = executor.execute_tool(
        db,
        name="create_task",
        args={"project_id": int(project.id), "title": "X"},
        context=context,
        transport=ToolTransport.debug_cli,
        decision_id=_decision_id(db, context),
    )
    assert still_denied.ok is False and still_denied.reason == "not_authorized"

    # 有了授权，两条 transport 产出**同一份**领域效果
    _grant(db, definition, C.AuthorityKind.plan_project_work)
    via_cli = executor.execute_tool(
        db,
        name="create_task",
        args={"project_id": int(project.id), "title": "经由调试口"},
        context=context,
        transport=ToolTransport.debug_cli,
        decision_id=_decision_id(db, context),
    )
    via_internal = executor.execute_tool(
        db,
        name="create_task",
        args={"project_id": int(project.id), "title": "经由内部面"},
        context=context,
        decision_id=_decision_id(db, context),
    )
    assert via_cli.ok and via_internal.ok
    cli_task = project_repo.get_task(db, int(via_cli.data["task"]["task_id"]))
    internal_task = project_repo.get_task(db, int(via_internal.data["task"]["task_id"]))
    assert cli_task.status == internal_task.status == TaskStatus.backlog.value
    assert cli_task.kind == internal_task.kind


def test_unknown_tool_is_reported_not_guessed(db, default_company_id):
    employee, _definition = _lab(db, default_company_id, "lab-unknown")
    result = executor.execute_tool(db, name="make_coffee", args={}, context=_context(db, employee))
    assert result.ok is False and result.reason == "unknown_tool"


# ---------------------------------------------------------------------------
# T12 · 每次调用都可审计
# ---------------------------------------------------------------------------


def test_every_tool_call_is_audited(db, default_company_id):
    """T12：读、写、被拒绝 —— 三种结果都留审计。"""
    employee, definition = _lab(db, default_company_id, "lab-audit")
    project = _project(db, default_company_id, "审计")
    context = _context(db, employee)
    before = int(db.scalar(select(func.max(ToolAudit.id))) or 0)

    read_result = executor.execute_tool(
        db,
        name="inspect_project",
        args={"project_id": int(project.id)},
        context=context,
    )
    denied = executor.execute_tool(
        db,
        name="create_task",
        args={"project_id": int(project.id), "title": "无授权"},
        context=context,
        decision_id=_decision_id(db, context),
    )
    _grant(db, definition, C.AuthorityKind.plan_project_work)
    applied = executor.execute_tool(
        db,
        name="create_task",
        args={"project_id": int(project.id), "title": "有授权"},
        context=context,
        decision_id=_decision_id(db, context),
    )

    assert read_result.ok and denied.ok is False and applied.ok
    rows = list(db.scalars(select(ToolAudit).where(ToolAudit.id > before).order_by(ToolAudit.id)))
    # 同一个 action 会有多条（被拒 + 成功）—— 用列表，别用 dict（会互相覆盖）
    tools = [row.tool_name for row in rows]
    assert "inspect_project" in tools
    assert tools.count("create_task") == 2, "被拒与成功各留一条，不许合并"
    outcomes = [row.outcome for row in rows]
    assert "read" in outcomes
    assert "not_authorized" in outcomes
    assert "applied" in outcomes
    for row in rows:
        assert row.transport in {"internal", "debug_cli"}
        assert row.actor_employee_id == int(employee.id)
        assert row.arguments_digest
    assert read_result.audit_id and applied.audit_id and denied.audit_id


def test_audit_never_stores_raw_arguments(db, default_company_id):
    """审计只存参数**摘要**与键名：不把提示词/长正文抄进审计表。"""
    employee, definition = _lab(db, default_company_id, "lab-audit2")
    _grant(db, definition, C.AuthorityKind.plan_project_work)
    project = _project(db, default_company_id, "审计摘要")
    secret = "SENSITIVE-BODY-" * 20
    result = executor.execute_tool(
        db,
        name="create_task",
        args={"project_id": int(project.id), "title": "标题", "description": secret},
        context=_context(db, employee),
        decision_id=_decision_id(db, _context(db, employee)),
    )
    assert result.ok
    row = db.get(ToolAudit, int(result.audit_id))
    assert row is not None
    # 执行事实原样留档入参（M2.4）；决策行**不**复制它，但审计表要有它（DR5）
    assert row.arguments_json["description"] == secret
    assert row.arguments_digest


# ---------------------------------------------------------------------------
# 种子 / 工具清单一致性
# ---------------------------------------------------------------------------


def test_seed_grants_the_planning_authority_to_managers_only():
    """M2.3 新增的 `plan_project_work` 只给管理岗：CEO 与 PM。"""
    assert C.AuthorityKind.plan_project_work in authority_seed.DEFAULT_AUTHORITY["ceo"]
    assert C.AuthorityKind.plan_project_work in authority_seed.DEFAULT_AUTHORITY["product_manager"]
    assert C.AuthorityKind.plan_project_work not in authority_seed.DEFAULT_AUTHORITY["engineer"]
    assert C.AuthorityKind.plan_project_work not in authority_seed.DEFAULT_AUTHORITY["researcher"]
    assert C.AuthorityKind.plan_project_work not in authority_seed.DEFAULT_AUTHORITY["qa_engineer"]


def test_catalog_exposes_every_spec_without_handlers():
    catalog = executor.catalog()
    assert len(catalog) == len(executor.registry.names())
    for entry in catalog:
        assert "handler" not in entry
        assert entry["side_effect"] in {level.value for level in ToolSideEffect}
        assert entry["autonomy"] in {level.value for level in AutonomyLevel}
        assert entry["input_schema"]["type"] == "object"


def test_read_and_write_tool_names_are_the_agreed_first_batch():
    """第一批清单固定下来（用户拍板 §8/§9）—— 增删要走评审。"""
    read_names = {spec.name for spec in executor.registry.by_side_effect(ToolSideEffect.read)}
    assert {
        "inspect_project",
        "list_company_people",
        "inspect_person",
        "inspect_position",
        "inspect_assignments",
        "get_competencies",
        "get_evidence",
        "calculate_task_fit",
        "get_current_load",
        "get_runtime_status",
        "search_company_knowledge",
        "inspect_artifact",
    } <= read_names
    write_names = {spec.name for spec in executor.registry.by_side_effect(ToolSideEffect.write)}
    assert write_names == {
        "create_task",
        "update_task",
        "create_dependency",
        "assign_task",
        "delegate_project",
        "request_review",
        "request_rework",
        "mark_task_blocked",
        "cancel_task",
    }


def test_build_helpers_are_pure_constructors():
    """构建函数只造 spec，不注册到全局表（注册只发生一次，且显式）。"""
    assert len(tool_reads.build_read_tools()) >= 12
    assert len(tool_writes.build_write_tools()) == 9
    assert isinstance(machinery.ToolRegistry(), machinery.ToolRegistry)
