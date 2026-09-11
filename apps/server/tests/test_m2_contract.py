"""M2.0 工作与组织运行域契约测试（Acceptance A1–A14）。

三条纪律（沿用 M1/T2 的做法）：

1. **契约不是注释**：每个数据类/枚举/注册表都有真实断言，包括**字段级**扫描
   （例如 `RoleResource` 不许出现数值字段 —— 那会让"资源"演化成"注入"）。
2. **守卫不是声明式装饰**：AST 守卫断言的是"某段代码不存在/不被引用"，
   抽掉入口就会转红；收尾时必须做反例注入验证（注入违规 → 红 → 撤回）。
3. **不假装完成**：`INVARIANTS` 区分 `enforced`（M2.0 已有现存锚点）与
   `owner_stage`（M2.0 只冻结归属）。本文件断言**没有任何一条被静默丢弃**，
   但不假装 W1/W2/W16/W17 已经实现 —— 那是 M2.4/M2.5/M2.7 的交付项。
"""

from __future__ import annotations

import ast
import dataclasses
import random
import re
from pathlib import Path

import pytest

from app.models import Base
from app.work import contracts as C

SERVER_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = SERVER_ROOT / "app"
DESIGN_DOC = SERVER_ROOT.parents[1] / "docs" / "m2-agent-work-runtime-design.md"


def _app_python_files() -> list[tuple[str, Path]]:
    result: list[tuple[str, Path]] = []
    for path in sorted(APP_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        result.append((str(path.relative_to(APP_ROOT)), path))
    return result


def _read(relative: str) -> str:
    return (APP_ROOT / relative).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# A1 · 决策边界：系统事实与 Agent 决策互斥
# ---------------------------------------------------------------------------


def test_system_facts_and_agent_decisions_are_disjoint():
    """A1 / W24 / W25：系统事实与 Agent 决策是两个**不重叠**的集合。

    "系统拥有事实、Agent 拥有判断"如果在词汇层就重叠，后面的守卫全是空话 ——
    所以先在这里把两个集合钉死。
    """
    facts = {f.value for f in C.SYSTEM_FACTS}
    decisions = {d.value for d in C.AGENT_DECISIONS}
    assert facts, "SYSTEM_FACTS 不能为空"
    assert decisions, "AGENT_DECISIONS 不能为空"
    overlap = facts & decisions
    assert not overlap, f"同一名称既是系统事实又是 Agent 决策：{sorted(overlap)}"


def test_every_decision_kind_has_a_default_authority():
    """路由建议必须覆盖全部决策类型（缺一条 = 那条决策无人被通知）。"""
    missing = set(C.DecisionKind) - set(C.DEFAULT_DECISION_AUTHORITY)
    assert not missing, f"缺少默认职责面：{[d.value for d in missing]}"
    unknown = set(C.DEFAULT_DECISION_AUTHORITY) - set(C.DecisionKind)
    assert not unknown, f"登记了不存在的决策类型：{unknown}"
    for decision, areas in C.DEFAULT_DECISION_AUTHORITY.items():
        assert areas, f"{decision.value} 的职责面不能为空"
        for area in areas:
            assert isinstance(area, C.ResponsibilityArea)


def test_decision_default_authority_is_advisory_only_and_documented():
    """W5 / W12：默认职责面是**路由建议**，契约层不得提供"谁能做"的判定函数。"""
    forbidden = (
        "can_decide",
        "may_decide",
        "is_authorized_for",
        "require_area",
        "enforce_area",
        "area_allows",
    )
    source = _read("work/contracts.py")
    tree = ast.parse(source)
    defined = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    assert not (defined & set(forbidden)), (
        "契约层不允许出现按职责面判定权限的函数 —— 权限是 Authority（硬边界），"
        f"职责面只是路由建议（W5/W12）。发现了：{sorted(defined & set(forbidden))}"
    )


# ---------------------------------------------------------------------------
# A2 · 硬/软约束互斥且完备
# ---------------------------------------------------------------------------


def test_constraint_classes_are_disjoint_and_complete():
    """A2 / W6：Hard 与 Soft 两类必须互斥，且覆盖 `ConstraintName` 的全部值。

    一条约束若同时出现在两边，「系统能不能拒绝」就变成运气问题。
    """
    hard = set(C.HARD_CONSTRAINTS)
    soft = set(C.SOFT_CONSTRAINTS)
    assert hard and soft
    overlap = hard & soft
    assert not overlap, f"同一约束同时属于硬/软两类：{sorted(c.value for c in overlap)}"
    missing = set(C.ConstraintName) - (hard | soft)
    assert not missing, f"未分类的约束：{sorted(c.value for c in missing)}"


def test_position_scope_is_a_soft_constraint():
    """W5：职位范围是 soft —— 系统可以报告，但**不得**据此拒绝工作。"""
    assert C.ConstraintName.position_scope in C.SOFT_CONSTRAINTS
    assert C.ConstraintName.position_scope not in C.HARD_CONSTRAINTS
    # 反面对照：权限与安全必须是硬的（否则 W6 形同虚设）
    for hard_only in (
        C.ConstraintName.permission,
        C.ConstraintName.security,
        C.ConstraintName.company_isolation,
        C.ConstraintName.economic_authority,
    ):
        assert hard_only in C.HARD_CONSTRAINTS
        assert hard_only not in C.SOFT_CONSTRAINTS


def test_advisory_constraints_are_exactly_the_decision_support_set():
    """Fit / 期望 / 经历 / 专业 / 习惯 / 建议负载 —— 全部只做决策支持（W11 的同源约束）。"""
    support = {
        C.ConstraintName.fit_score,
        C.ConstraintName.competency_expectation,
        C.ConstraintName.experience_match,
        C.ConstraintName.specialization,
        C.ConstraintName.work_habit,
        C.ConstraintName.advisory_load,
    }
    assert support <= C.SOFT_CONSTRAINTS
    assert not (support & C.HARD_CONSTRAINTS)


# ---------------------------------------------------------------------------
# A3 · Role Resource 不带分值（资源 ≠ 注入）
# ---------------------------------------------------------------------------


def test_role_resource_carries_no_numeric_field():
    """A3 / W27：`RoleResource` 一旦带分值就会演化成"任命即加分"。"""
    numeric = {"int", "float", "Decimal", "number"}
    for field in dataclasses.fields(C.RoleResource):
        assert field.type not in numeric, (
            f"RoleResource.{field.name} 是数值字段 —— 资源只能建议读什么，不能携带分值（W27）"
        )
    names = {f.name for f in dataclasses.fields(C.RoleResource)}
    assert names == {"kind", "ref", "note", "required"}


def test_role_resource_rejects_empty_ref():
    with pytest.raises(C.WorkContractError):
        C.RoleResource(kind=C.RoleResourceKind.policy, ref="   ")


# ---------------------------------------------------------------------------
# A4 · RoleContext 字段全部可推导（派生读模型，不落表）
# ---------------------------------------------------------------------------


def test_role_context_fields_are_all_derivable():
    """A4 / W7：每个 RoleContext 字段都必须能映射到**现有表/列**。

    违反这条的典型症状：先写一个字段（如 `leadership_score`），再想办法造一个写路径 ——
    那就成了"任命即注入能力"（W7）。所以字段集与来源映射必须双向一致。
    """
    fields = {f.name for f in dataclasses.fields(C.RoleContext)}
    documented = set(C.ROLE_CONTEXT_SOURCES)
    assert fields == documented, (
        f"RoleContext 字段与来源映射不一致：新增 {sorted(fields - documented)}、"
        f"缺少说明 {sorted(documented - fields)}"
    )


def test_role_context_sources_resolve_to_real_tables_and_columns():
    """A4（续）：来源里写出的 `table.column` 必须真实存在（M2.x 规划项显式标注后跳过）。"""
    tables = Base.metadata.tables
    problems: list[str] = []
    for field, source in C.ROLE_CONTEXT_SOURCES.items():
        if "(M2." in source:
            continue  # 规划中的载体：M2.0 只冻结来源说明，不假装它已存在
        for table, column in re.findall(r"\b([a-z_]+)\.([a-z_]+)\b", source):
            if table not in tables:
                problems.append(f"{field}: 表 {table} 不存在")
            elif column not in tables[table].columns:
                problems.append(f"{field}: {table}.{column} 不存在")
    assert not problems, "\n".join(problems)


def test_role_context_carries_no_earned_capability_field():
    """W7：RoleContext 只能携带**期望**（引用），不能携带**已获得**的能力值。"""
    forbidden_tokens = ("score", "rating", "level", "rank", "grade", "proficiency")
    for field in dataclasses.fields(C.RoleContext):
        assert not any(token in field.name for token in forbidden_tokens), (
            f"RoleContext.{field.name} 看起来像'已获得的能力' —— "
            "能力必须经 Evidence 证明，不能由职位上下文携带（W7）"
        )


# ---------------------------------------------------------------------------
# A5 · 禁止的上岗注入动作不存在
# ---------------------------------------------------------------------------


def test_forbidden_onboarding_actions_do_not_exist_anywhere():
    """A5 / W4 / W8 / W26：`FORBIDDEN_ONBOARDING_ACTIONS` 里的名字**不得**出现在 `app/`。

    这条守卫防的是"某天有人觉得注入一下更方便"。它扫描**全部** Python 定义
    （函数、方法、类、变量赋值），因为注入可以伪装成任何一种。
    """
    forbidden = C.FORBIDDEN_ONBOARDING_ACTIONS
    violations: list[str] = []
    for relative, path in _app_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            name = getattr(node, "name", None)
            if isinstance(name, str) and name in forbidden:
                violations.append(f"{relative}:{getattr(node, 'lineno', '?')} 定义了 {name}")
            if isinstance(node, ast.Attribute) and node.attr in forbidden:
                violations.append(f"{relative}:{node.lineno} 调用了 {node.attr}")
    assert not violations, "出现了禁止的职位能力注入动作（W4/W8/W26）：\n" + "\n".join(violations)


def test_onboarding_path_is_the_adaptive_route():
    """W4：上岗路径是"读上下文 → 找差距 → 学习 → 工作"，不是"注入 → 工作"。"""
    steps = [s.value for s in C.ROLE_ONBOARDING_PATH]
    assert steps == [
        "read_role_context",
        "inspect_expectations",
        "inspect_own_competencies",
        "find_gaps",
        "search_company_knowledge",
        "create_learning_priorities",
        "learn",
        "work",
    ]


def test_position_contract_exposes_no_prompt_or_workflow_surface():
    """W4 / W26：职位契约里**不得**出现 prompt / SOP / workflow / steps 这类字段。"""
    forbidden_tokens = ("prompt", "sop", "workflow", "steps", "graph", "script", "template")
    for field in dataclasses.fields(C.PositionContract):
        assert not any(token in field.name for token in forbidden_tokens), (
            f"PositionContract.{field.name} 把职位变成了流程模板 —— "
            "职位只表达责任/权限/期望（W4/W26）"
        )


def test_position_contract_is_responsibility_authority_expectations():
    """W4：三段式是**全部**内容 —— 多出来的字段必须走设计评审。"""
    names = {f.name for f in dataclasses.fields(C.PositionContract)}
    assert names == {"responsibilities", "authority", "expectations", "advisory_scope"}


def test_position_contract_carries_no_derived_capability():
    """W7：契约本身不含能力分；分值只允许出现在 `PositionExpectation`（需求侧投影）。"""
    for field in dataclasses.fields(C.PositionContract):
        assert "score" not in field.name and "rating" not in field.name
    # 需求侧：期望的分数字段必须与 position_competency_requirements 列一一对应
    expectation_fields = {f.name for f in dataclasses.fields(C.PositionExpectation)}
    table = Base.metadata.tables["position_competency_requirements"]
    for score_field in ("minimum_score", "target_score", "minimum_confidence"):
        assert score_field in expectation_fields
        assert score_field in table.columns, (
            f"{score_field} 在契约里存在但表里没有 —— 期望必须来自既有需求表，不是新造的第二套标准"
        )


def test_authority_grant_mirrors_the_v41_table():
    """M2.2：`AuthorityGrant` 与 `position_authority_grants` 一一对应（契约即表）。"""
    from app.models import Base

    fields = {field.name for field in dataclasses.fields(C.AuthorityGrant)}
    assert fields == {"kind", "scope_kind", "scope_ref", "max_amount", "grant_id"}
    columns = set(Base.metadata.tables["position_authority_grants"].columns.keys())
    for field_name in ("scope_kind", "scope_ref", "max_amount"):
        assert field_name in columns, f"{field_name} 在契约里存在但表里没有"
    # 作用域只有三种；金额只在金额类授权上
    assert {scope.value for scope in C.AuthorityScopeKind} == {
        "company",
        "department",
        "direct_reports",
    }
    assert C.AMOUNT_BEARING_AUTHORITIES == {C.AuthorityKind.spend_credits}
    assert C.AuthorityKind.offboard in C.SELF_TARGET_FORBIDDEN_AUTHORITIES


def test_authority_scope_is_not_a_general_abac_engine():
    """M2-ADR-20 / 用户拍板：作用域只有三个值，不做策略语言。"""
    assert len(list(C.AuthorityScopeKind)) == 3
    source = _read("work/authority.py")
    for forbidden in ("eval(", "jsonpath", "cel", "policy_expression", "attribute_"):
        assert forbidden not in source, f"出现了通用策略引擎的迹象：{forbidden}"


# ---------------------------------------------------------------------------
# A6 · 记忆平面：制度 vs 个人
# ---------------------------------------------------------------------------


def test_memory_planes_are_disjoint_and_resolve_to_real_tables():
    """A6 / W8 / W9：每一张承载记忆的表都被声明到**唯一**平面。

    例外是被显式登记的"表级切开"载体（`knowledge_items`：private 属个人，
    department/company 属制度）—— 它必须在两边都有声明。
    """
    tables = Base.metadata.tables
    institutional = {
        s.table for s in C.MEMORY_PLANE_SURFACES if s.plane is C.MemoryPlane.institutional
    }
    personal = {s.table for s in C.MEMORY_PLANE_SURFACES if s.plane is C.MemoryPlane.personal}
    split = set(C.SPLIT_MEMORY_SURFACES)

    unknown = (institutional | personal) - set(tables)
    assert not unknown, f"声明了不存在的表：{sorted(unknown)}"

    overlap = (institutional & personal) - split
    assert not overlap, (
        f"同一张表被声明到两个平面且没有登记为 SPLIT：{sorted(overlap)} —— "
        "制度资产与个人资产混在一张表里就是 W9/W10 的破口"
    )

    for table, surfaces in C.SPLIT_MEMORY_SURFACES.items():
        assert table in tables, f"SPLIT 表 {table} 不存在"
        planes = {s.plane for s in surfaces}
        assert planes == {C.MemoryPlane.institutional, C.MemoryPlane.personal}, (
            f"{table} 被登记为 SPLIT，但两边声明不齐：{sorted(p.value for p in planes)}"
        )


def test_personal_memory_surfaces_are_person_scoped_where_claimed():
    """A6 / W10：声明 `person_scoped=True` 的表必须**真的**带人的口径列。

    这条断言把"个人记忆随 Person"从文档承诺变成模型事实：有人删掉人的口径列，
    这里立刻转红。本仓有多个人的口径命名（`person_id` / `owner_person_id` /
    `author_person_id`），所以判据是"存在以 `person_id` 结尾的列"，而不是固定列名。
    """
    tables = Base.metadata.tables
    problems: list[str] = []
    for surface in (
        *C.MEMORY_PLANE_SURFACES,
        *(s for t in C.SPLIT_MEMORY_SURFACES.values() for s in t),
    ):
        if surface.plane is not C.MemoryPlane.personal or not surface.person_scoped:
            continue
        if surface.table not in tables:
            problems.append(f"{surface.table} 不存在")
            continue
        # `.keys()` 给出列名字符串：直接 set(columns) 会把 SQLAlchemy Column 放进集合，
        # 后续的成员比较会退化成 SQL 表达式（bool() 未定义）。
        columns = set(tables[surface.table].columns.keys())
        if not any(column.endswith("person_id") for column in columns):
            problems.append(f"{surface.table} 声明 person_scoped 但没有任何 person_id 口径列")
    assert not problems, "\n".join(problems)


def test_institutional_and_personal_planes_cover_the_documented_surfaces():
    """审计 §9 的 K1 分层与设计 §7 的清单都依赖这些表存在；少一张就说明有人拆了地基。"""
    institutional = {
        s.table for s in C.MEMORY_PLANE_SURFACES if s.plane is C.MemoryPlane.institutional
    }
    personal = {s.table for s in C.MEMORY_PLANE_SURFACES if s.plane is C.MemoryPlane.personal}
    required_institutional = {
        "knowledge_items",
        "projects",
        "drive_nodes",
        "drive_revisions",
        "review_meetings",
        "baselines",
        "work_orders",
        "contracts",
        "escrows",
        "ledger_transactions",
        "events",
        "audit_logs",
    }
    required_personal = {
        "competency_evidence",
        "employee_competencies",
        "employee_brains",
        "skills",
        "skill_usages",
        "memory_entries",
        "learning_records",
        "career_events",
    }
    assert required_institutional <= institutional | set(C.SPLIT_MEMORY_SURFACES)
    assert required_personal <= personal | set(C.SPLIT_MEMORY_SURFACES)


# ---------------------------------------------------------------------------
# A7 · 不新建 Mission / Agent SoT
# ---------------------------------------------------------------------------


def test_no_mission_or_agent_source_of_truth_table_exists():
    """A7 / W20 / W21：不新建 Mission / Agent 的 Source of Truth 表。

    匹配的是**整表名**（不用子串 —— `work_order_submissions` 里就含 "mission"，
    子串匹配会误报，而误报会让人把这条件守卫删掉）。
    """
    forbidden_exact = {"mission", "missions", "agent", "agents", "mission_specs", "agent_records"}
    tables = set(Base.metadata.tables)
    assert not (tables & forbidden_exact), (
        f"出现了被禁止的 SoT 表：{sorted(tables & forbidden_exact)}"
    )

    # 也不允许任何表带 mission_id 列（那等于把 Mission 塞进了别的实体）
    offenders = [
        f"{name}.mission_id"
        for name, table in Base.metadata.tables.items()
        if "mission_id" in table.columns
    ]
    assert not offenders, f"不允许 mission_id 外键列（W20）：{offenders}"


def test_work_order_is_not_an_execution_or_graph_container():
    """W23：WorkOrder 是商业包装，不是执行图 —— 不许长出 Task/DAG 结构。"""
    tables = set(Base.metadata.tables)
    derived = {
        t for t in tables if re.fullmatch(r"work_order_(tasks|dependencies|nodes|edges|graphs)", t)
    }
    assert not derived, f"WorkOrder 长出了执行图结构（W23）：{sorted(derived)}"
    columns = set(Base.metadata.tables["work_orders"].columns.keys())
    forbidden = {"task_id", "dag", "graph", "workflow", "pipeline", "agent_prompt"}
    assert not (columns & forbidden), (
        f"work_orders 出现了执行语义的列（W23）：{sorted(columns & forbidden)}"
    )


def test_canonical_project_fields_all_have_existing_sources():
    """W22：Canonical Project Spec 的每个字段都必须有**现有承载** —— 否则会诱人新建一张表。

    这条也是"M2 不需要新表"的证据：契约里 9 个字段全部落到既有列上。
    """
    tables = Base.metadata.tables
    assert set(C.PROJECT_FIELD_SOURCES) == set(C.CANONICAL_PROJECT_FIELDS)
    problems: list[str] = []
    for field, source in C.PROJECT_FIELD_SOURCES.items():
        for table, column in re.findall(r"\b([a-z_]+)\.([a-z_]+)\b", source):
            if table not in tables:
                problems.append(f"{field}: 表 {table} 不存在")
            elif column not in tables[table].columns:
                problems.append(f"{field}: {table}.{column} 不存在")
    assert not problems, "\n".join(problems)


def test_work_modes_are_a_product_axis_with_two_values():
    """D2/M2-ADR-11（W36）：工作模式只有 guided / managed，差别是**人类参与程度**。"""
    assert {mode.value for mode in C.ProjectWorkMode} == {"guided", "managed"}
    assert C.WORK_MODE_AFTER_ONBOARDING is C.ProjectWorkMode.managed
    assert set(C.WORK_MODE_BY_COMPANY_STAGE) == {"FOUNDING", "OPERATING"}
    assert C.WORK_MODE_BY_COMPANY_STAGE["FOUNDING"] is C.ProjectWorkMode.guided
    assert C.WORK_MODE_BY_COMPANY_STAGE["OPERATING"] is C.ProjectWorkMode.managed
    # 未知阶段保守回落 guided（宁可多一层人类确认，不默默自主）
    assert C.default_work_mode_for_stage("WHATEVER") is C.ProjectWorkMode.guided
    assert C.default_work_mode_for_stage(None) is C.ProjectWorkMode.guided


def test_planning_fixture_is_a_separate_infrastructure_axis():
    """D3/M2-ADR-12（W33）：确定性模板**不在**产品模式枚举里，是独立的基础设施轴。"""
    assert {f.value for f in C.PlanningFixture} == {"none", "deterministic_template"}
    assert "deterministic_template" not in {m.value for m in C.ProjectWorkMode}
    assert "template" not in {m.value for m in C.ProjectWorkMode}
    # fixture 必须显式请求 + 受部署门控，且禁止的隐式来源被点名
    assert C.PLANNING_FIXTURE_SETTING == "allow_planning_fixtures"
    assert {
        "missing_manager_fallback",
        "manager_timeout_fallback",
        "manager_failure_fallback",
        "empty_task_list_fallback",
        "company_default_fixture_in_request_path",
    } <= C.FORBIDDEN_IMPLICIT_PLANNING_SOURCES


def test_responsibility_routing_is_declared_and_configurable():
    """D1/M2-ADR-11（W32）：责任 → 默认职位，公司可覆盖；默认是 CEO 但不是特权。"""
    assert C.RESPONSIBILITY_DEFAULTS[C.ResponsibilityKind.work_intake] == "ceo"
    assert C.WORK_INTAKE_DEFAULT_POSITION == "ceo"
    assert C.RESPONSIBILITY_SETTINGS_KEY == "work_routing"
    assert C.WORK_MODE_SETTINGS_KEY == "work_mode_default"
    # 责任类型是**枚举 + 默认表**，不是散落的字符串比较
    assert set(C.RESPONSIBILITY_DEFAULTS) == set(C.ResponsibilityKind)


def test_spec_questions_are_declared_as_a_machine_checkable_list():
    """B1：Project 必须回答的 8 个问题在契约里点名（读面按它核对）。"""
    assert len(C.PROJECT_SPEC_QUESTIONS) == 8
    for key in (
        "canonical_spec",
        "work_mode",
        "work_intake_responsibility",
        "work_intake_assignment",
        "management_actor",
        "requirements_deliverables_acceptance",
        "spec_version",
        "execution_entered",
    ):
        assert key in C.PROJECT_SPEC_QUESTIONS


# ---------------------------------------------------------------------------
# A8 · 决策记录：只校验结构，不评价意图
# ---------------------------------------------------------------------------


def _decision(**overrides) -> C.DecisionRecord:
    payload = {
        "actor_person_id": 7,
        "acting_employee_id": 11,
        "decision": C.DecisionKind.assign_task,
        "scope": "task:34",
        "reason": "Bob 的 Rust Fit 88%，有网络经验，负载 20%",
        "context_snapshot": {"project": 3, "candidates": [11, 12]},
        "actions": (C.DecisionAction(tool="assign_task", args={"task_id": 34, "employee_id": 57}),),
    }
    payload.update(overrides)
    return C.DecisionRecord(**payload)  # type: ignore[arg-type]


def test_decision_validation_never_judges_intent():
    """A8 / W18：系统只记录"做了什么决定、依据是什么"，**不评价**理由是否合理。

    所以一个明显草率的理由必须被接受 —— 这是特性不是疏漏。若哪天有人给这里加上
    "理由质量"校验，这条测试会转红，提醒他那是中央判断。
    """
    sloppy = _decision(reason="I felt like it")
    assert C.validate_decision_record(sloppy) is sloppy

    # 反面对照：**结构**问题必须被拒绝（否则"只校验结构"就成了什么都不校验）。
    with pytest.raises(C.WorkContractError):
        C.validate_decision_record(_decision(reason="   "))
    with pytest.raises(C.WorkContractError):
        C.validate_decision_record(_decision(scope="task"))
    with pytest.raises(C.WorkContractError):
        C.validate_decision_record(_decision(scope="galaxy:1"))
    with pytest.raises(C.WorkContractError):
        C.validate_decision_record(_decision(actor_person_id=0))
    with pytest.raises(C.WorkContractError):
        C.validate_decision_record(_decision(decision="promote_myself"))  # type: ignore[arg-type]


def test_decision_validation_checks_provided_ids():
    with pytest.raises(C.WorkContractError):
        C.validate_decision_record(_decision(acting_position_definition_id=-1))
    record = C.validate_decision_record(_decision(acting_position_definition_id=5))
    assert record.acting_position_definition_id == 5


def test_decision_record_is_frozen_as_append_only():
    """W28：决策不可重写；结果只能**追加**。

    - 数据类必须 frozen（结构上不可就地改写）；
    - `outcome` / `outcome_ref` 必须是**独立字段**（回填是追加一条事实，不是改理由）；
    - 不允许出现"修订后的理由"这类字段。
    """
    params = C.DecisionRecord.__dataclass_params__
    assert params.frozen, "DecisionRecord 必须是 frozen dataclass（W28 append-only）"
    names = {f.name for f in dataclasses.fields(C.DecisionRecord)}
    assert {"outcome", "outcome_ref"} <= names
    assert not (names & {"revised_reason", "edited_reason", "amended_reason", "deleted_at"})
    with pytest.raises(dataclasses.FrozenInstanceError):
        _decision().reason = "changed"  # type: ignore[misc]


def test_decision_record_allows_backfilling_an_outcome():
    pending = _decision()
    assert pending.outcome is None
    settled = dataclasses.replace(
        pending, outcome=C.DecisionOutcome.succeeded, outcome_ref="project:3"
    )
    assert settled.reason == pending.reason  # 理由没被改写
    assert C.validate_decision_record(settled).outcome is C.DecisionOutcome.succeeded


# ---------------------------------------------------------------------------
# A9 · Task DAG 结构校验
# ---------------------------------------------------------------------------


def test_task_graph_validation_accepts_a_healthy_dag_and_reports_readiness():
    nodes = [
        C.TaskGraphNode(1, "done", ()),
        C.TaskGraphNode(2, "todo", (1,)),
        C.TaskGraphNode(3, "todo", (1,)),
        C.TaskGraphNode(4, "backlog", (2, 3)),
    ]
    report = C.validate_task_graph(nodes)
    assert report.is_valid, report.error
    assert report.ready == (2, 3), "就绪 = 依赖全部 done 且自己还没开始"
    assert 4 not in report.ready, "扇入：必须等全部前置完成"


def test_task_graph_validation_detects_structural_problems():
    self_loop = C.validate_task_graph([C.TaskGraphNode(1, "todo", (1,))])
    assert not self_loop.is_valid and self_loop.self_loops == (1,)

    dangling = C.validate_task_graph([C.TaskGraphNode(1, "todo", (99,))])
    assert not dangling.is_valid and dangling.dangling == ((1, 99),)

    duplicate = C.validate_task_graph(
        [C.TaskGraphNode(1, "todo", (2, 2)), C.TaskGraphNode(2, "done")]
    )
    assert not duplicate.is_valid and duplicate.duplicates == ((1, 2),)

    cycle = C.validate_task_graph(
        [C.TaskGraphNode(1, "todo", (2,)), C.TaskGraphNode(2, "todo", (1,))]
    )
    assert not cycle.is_valid and cycle.cycles == ((1, 2),)
    assert cycle.ready == (), "非法图不回答就绪集合"


def test_ready_tasks_excludes_running_and_finished_work():
    """W16：系统只回答"哪些 Task 现在可以执行"，且不把运行中/已结束的算进去。"""
    nodes = [
        C.TaskGraphNode(1, "in_progress", ()),
        C.TaskGraphNode(2, "in_review", ()),
        C.TaskGraphNode(3, "done", ()),
        C.TaskGraphNode(4, "rejected", ()),
        C.TaskGraphNode(5, "failed", ()),
        C.TaskGraphNode(6, "todo", ()),
    ]
    assert C.resolve_ready_tasks(nodes) == (5, 6)


def test_task_graph_validation_property_always_accepts_random_dags():
    """属性测试：随机生成的真 DAG 永远合法，且就绪集合非空（前驱全 done 的节点存在）。"""
    rng = random.Random(20260911)
    for _ in range(120):
        size = rng.randint(1, 8)
        node_ids = list(range(1, size + 1))
        nodes: list[C.TaskGraphNode] = []
        for node_id in node_ids:
            earlier = [i for i in node_ids if i < node_id]
            deps = tuple(sorted(rng.sample(earlier, rng.randint(0, len(earlier)))))
            status = rng.choice(["done", "todo", "backlog"])
            nodes.append(C.TaskGraphNode(node_id, status, deps))
        report = C.validate_task_graph(nodes)
        assert report.is_valid, report.error
        for node_id in report.ready:
            node = next(n for n in nodes if n.task_id == node_id)
            assert node.status != "done"
            assert all(
                dep in {n.task_id for n in nodes if n.status == "done"} for dep in node.depends_on
            )


# ---------------------------------------------------------------------------
# A10 · 四个 verdict 面不得互相替代
# ---------------------------------------------------------------------------


def test_verdict_surfaces_are_distinct_and_owned():
    """A10 / W29：四个面的值集互不相同、判定者各自明确，且**不互相引用**。"""
    boundaries = C.verdict_boundaries()
    assert len(boundaries) == 4
    names = [b.enum_name for b in boundaries]
    assert len(set(names)) == len(names)
    values = [b.values for b in boundaries]
    for i, left in enumerate(values):
        for right in values[i + 1 :]:
            assert left != right, "两个 verdict 面的值集完全相同 —— 一定是同一套东西被复制了"
    for boundary in boundaries:
        assert boundary.decider and boundary.object_of and boundary.effect
        assert boundary.module_prefix.startswith("app.")
    task_review = next(b for b in boundaries if b.surface == "task_review")
    assert task_review.values == {v.value for v in C.ReviewVerdict}
    assert task_review.decider == "reviewer_agent"


def test_task_review_verdict_maps_to_explicit_task_status_targets():
    """W17：verdict 是**判定**，状态目标是**迁移**；两者必须显式对应，不允许隐式兜底。"""
    from app.models.enums import TaskStatus

    targets = {
        C.ReviewVerdict.passed: "done",
        C.ReviewVerdict.rework: "todo",
        C.ReviewVerdict.rejected: "rejected",
    }
    assert set(targets.values()) <= {s.value for s in TaskStatus}
    assert C.ReviewVerdict.escalated not in targets, (
        "ESCALATE 刻意没有自动状态目标 —— 它停下来等人/管理层（W17）"
    )


def _names_referenced(path: Path) -> set[str]:
    """AST 里出现的**代码符号**（导入名 / Name / Attribute）—— 刻意不含字符串字面量。

    这样做是为了区分"文档里提到另一个面"（合法，边界表需要写清各面名字）
    与"代码里真的用了另一个面的枚举"（越界）。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            names.update(alias.name.split(".")[-1] for alias in node.names)
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


def test_work_and_economy_verdict_modules_do_not_cross_reference():
    """W29：`ReviewVerdict` 只属工作域，`EvaluationVerdict` 只属经济域。"""
    work_files = [(p, r) for r, p in _app_python_files() if r.startswith("work/")]
    economy_files = [
        (p, r) for r, p in _app_python_files() if r.startswith(("economy/", "services/economy/"))
    ]
    assert work_files and economy_files
    for path, relative in work_files:
        assert "EvaluationVerdict" not in _names_referenced(path), (
            f"{relative} 引用了商业验收枚举 —— 任务级评审与商业结算必须分开（W29）"
        )
    for path, relative in economy_files:
        assert "ReviewVerdict" not in _names_referenced(path), (
            f"{relative} 引用了任务级评审枚举 —— 质量判定不得被经济模块复用（W29）"
        )


# ---------------------------------------------------------------------------
# A12 · Fit 不进执行路径
# ---------------------------------------------------------------------------


def test_fit_module_is_not_imported_by_execution_paths():
    """W11（P4d ADR-9 的加强版）：执行路径不得 import Fit。

    Fit 是决策支持 —— 它只能经 M2.3 的工具面交给 Manager Agent，
    不能出现在 `orchestrator` / `tasks` / `projects` 的自动逻辑里。
    """
    watched = (
        "workflow/orchestrator.py",
        "services/tasks.py",
        "services/projects.py",
    )
    violations: list[str] = []
    for relative in watched:
        tree = ast.parse(_read(relative))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("app.talent"):
                violations.append(f"{relative}:{node.lineno} from {node.module} import ...")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("app.talent"):
                        violations.append(f"{relative}:{node.lineno} import {alias.name}")
    assert not violations, (
        "执行路径 import 了 Fit —— 系统不得替 Manager 选人（W11）：\n" + "\n".join(violations)
    )


# ---------------------------------------------------------------------------
# W13 · 没有自动辞退路径
# ---------------------------------------------------------------------------


def test_no_automatic_offboarding_path_exists():
    """W13：辞退只能由**人/管理 Agent 显式发起**，系统不得自动触发。

    断言方式：全仓对 `offboard(...)` 的调用点只允许出现在
      (a) service 的定义处；(b) 人类发起的 API 路由。
    任何后台消费者、orchestrator、事件处理器调用它都会让这条转红。
    """
    allowed = {"services/lifecycle.py", "api/v1/lifecycle.py"}
    callers: set[str] = set()
    for relative, path in _app_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name == "offboard":
                callers.add(relative)
    assert callers <= allowed, (
        f"出现了非人类发起的 offboard 调用点（W13）：{sorted(callers - allowed)}"
    )


def test_role_expectation_failure_does_not_imply_termination_in_contract():
    """W13 的契约面：`DecisionKind.offboard` 存在，但**没有任何**"期望不达标 ⇒ 辞退"的映射。"""
    assert C.DecisionKind.offboard in C.AGENT_DECISIONS
    source = _read("work/contracts.py")
    assert "expectation_failure" not in source
    assert "auto_offboard" not in source
    assert "discipline" not in source


# ---------------------------------------------------------------------------
# 契约模块自身的命名守卫：不允许出现"替 Agent 做决定"的函数
# ---------------------------------------------------------------------------


def test_contract_module_declares_no_decision_making_helpers():
    """最高原则的词汇层守卫：契约层不得出现"系统替 Agent 决策"的函数名。"""
    forbidden_patterns = (
        r"^(pick|choose|select|rank|recommend)_",
        r"^(best|top)_",
        r"^auto_",
        r"^(should|must)_",
        r"^(score|grade|judge|assess)_decision",
        r"^evaluate_decision",
        r"^(approve|reject)_task$",
    )
    tree = ast.parse(_read("work/contracts.py"))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for pattern in forbidden_patterns:
            if re.match(pattern, node.name):
                offenders.append(f"{node.name} (line {node.lineno})")
    assert not offenders, (
        "契约层出现了替 Agent 做决定的函数 —— 系统只提供事实与执行（设计 §1）：\n"
        + "\n".join(offenders)
    )


# ---------------------------------------------------------------------------
# A11 · 不变量注册表：无静默丢弃
# ---------------------------------------------------------------------------


def test_every_invariant_has_a_live_anchor_or_an_owner_stage():
    """A11：每条不变量要么有现存锚点，要么有合法 owner 阶段 —— 不允许两头都空。

    这条测试是 M2.0 最诚实的地方：它**不**假装 W1/W2/W16/W17 已经实现，
    但也**不允许**任何一条不变量在没有归属的情况下消失。
    """
    problems: list[str] = []
    for invariant in C.INVARIANTS:
        if invariant.enforced:
            if not invariant.anchors:
                problems.append(f"{invariant.id}: enforced 但没有锚点")
            continue
        if invariant.owner_stage not in C.M2_STAGES:
            problems.append(f"{invariant.id}: owner_stage 非法（{invariant.owner_stage!r}）")
        if invariant.anchors:
            problems.append(f"{invariant.id}: 未强制却声明了锚点（会让人误以为已实现）")
    assert not problems, "\n".join(problems)


#: 承载 M2 不变量锚点的测试模块（每个阶段可以有自己的文件；锚点表跨模块解析）。
M2_TEST_MODULES = (
    "test_m2_contract",
    "test_m2_project_spec",
    "test_m2_role_context",
    "test_m2_tools",
)


def test_enforced_invariants_have_existing_anchor_tests():
    """A11（续）：enforced 的锚点必须**真实存在**；删掉测试就会转红。

    锚点可以住在任一 M2 阶段测试文件里（本文件 + `M2_TEST_MODULES` 列出的模块），
    但必须能被解析到 —— 这样"某个阶段的测试被删了"不会静默通过。
    """
    import importlib

    modules = [importlib.import_module(name) for name in M2_TEST_MODULES]
    missing: list[str] = []
    for invariant in C.INVARIANTS:
        if not invariant.enforced:
            continue
        for anchor in invariant.anchors:
            if not any(hasattr(module, anchor) for module in modules):
                missing.append(f"{invariant.id} -> {anchor}")
    assert not missing, f"不变量锚点不存在（锚点表与测试已漂移）：{missing}"


def test_invariant_ids_are_unique_and_sequential_per_family():
    """每个前缀家族内部必须连续（W1…Wn / T1…Tm）—— 编号空洞说明有人删了却没登记。"""
    ids = [invariant.id for invariant in C.INVARIANTS]
    assert len(ids) == len(set(ids)), "不变量编号重复"
    families: dict[str, list[int]] = {}
    for invariant_id in ids:
        families.setdefault(re.sub(r"\d", "", invariant_id), []).append(
            int(re.sub(r"\D", "", invariant_id))
        )
    assert set(families) == {"W", "T"}, f"未知的不变量家族：{sorted(families)}"
    for prefix, numbers in families.items():
        assert sorted(numbers) == list(range(1, len(numbers) + 1)), (
            f"{prefix} 家族编号不连续：{sorted(numbers)}"
        )


def test_invariant_ids_and_texts_match_the_design_document():
    """W 表是**设计文档与代码的同一份事实**：任一侧改动而另一侧没跟就转红。"""
    design = DESIGN_DOC.read_text(encoding="utf-8")
    rows = re.findall(r"^\|\s*\*\*([WT]\d+)\*\*\s*\|\s*(.+?)\s*\|", design, flags=re.MULTILINE)
    documented = {wid: text for wid, text in rows}
    assert documented, "设计文档里没有解析到不变量表（§14 的格式可能被改了）"
    code = {invariant.id: invariant.text for invariant in C.INVARIANTS}
    assert set(documented) == set(code), (
        f"设计文档与代码的不变量集合不一致：文档多 {sorted(set(documented) - set(code))}、"
        f"代码多 {sorted(set(code) - set(documented))}"
    )
    mismatched = {wid: (documented[wid], code[wid]) for wid in code if documented[wid] != code[wid]}
    assert not mismatched, f"不变量文本不一致：{mismatched}"


def test_target_invariants_name_a_real_m2_stage():
    """target 不变量必须有**真实存在的阶段**承接（不是"以后再说"）。"""
    owners = {invariant.owner_stage for invariant in C.INVARIANTS if not invariant.enforced}
    assert owners
    for owner in owners:
        assert owner in C.M2_STAGES, f"{owner} 不是合法的 M2 阶段"


# ---------------------------------------------------------------------------
# 契约与设计文档的其它一致性
# ---------------------------------------------------------------------------


def test_design_document_exists_and_freezes_the_key_decisions():
    """契约文件与设计文档必须同时在位 —— 代码里的每条纪律都要能指回文档。"""
    assert DESIGN_DOC.exists(), f"缺少设计文档：{DESIGN_DOC}"
    design = DESIGN_DOC.read_text(encoding="utf-8")
    for anchored in (
        "System provides facts. Agent makes decisions.",
        "Responsibility + Authority + Expectations",
        "RoleContext",
        "Role Resource Index",
        "Institutional Memory",
        "Personal Memory",
        "DecisionRecord",
        "ReviewVerdict",
        "M2-ADR-1",
        "Canonical Executable Work Root",
    ):
        assert anchored in design, f"设计文档缺少关键裁决/术语：{anchored}"


def test_implementation_plan_covers_every_stage():
    plan_path = DESIGN_DOC.parent / "m2-implementation-plan.md"
    assert plan_path.exists()
    plan = plan_path.read_text(encoding="utf-8")
    for stage in sorted(C.M2_STAGES, key=lambda s: int(s.split(".")[1])):
        assert stage in plan, f"实施计划缺少 {stage}"
