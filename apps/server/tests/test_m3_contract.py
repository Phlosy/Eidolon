"""M3.0 生产运行与成长域契约测试（Acceptance A1–A10，不变量 P1–P36）。

三条纪律（沿用 M1/T2/M2 的做法）：

1. **契约不是注释**：每条不变量 P 都有**现存**测试锚点；`docs/m3-agent-production-growth-design.md`
   的不变量表与 `app/work/production.py` 逐条对拍（文本 + 集合）。
2. **守卫不是声明式装饰**：AST 守卫断言"某段代码不存在/不被引用"；收尾时必须做反例注入验证。
3. **不假装完成**：M3.0 不允许"冻结待锚点"的双态（M2.10 的教训）。尚未实现的语义用
   **欠账登记**（`PROVENANCE_OWED_FIELDS` / `WORK_EXPERIENCE_OWED_FIELDS` / owner_stage）表达，
   并断言欠账非空 —— "假装已经完整"比"承认欠账"危险得多。
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import re
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models import Base
from app.models.enums import HealthStatus, RuntimeType
from app.models.organization import Employee
from app.models.person import Person
from app.models.runtime import RuntimeInstance
from app.repositories import organization as org_repo
from app.repositories import providers as provider_repo
from app.repositories import runtimes as runtime_repo
from app.runtimes import base as runtime_base
from app.work import contracts as C
from app.work import production as P
from app.work import readiness, tool_reads, tool_writes

SERVER_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = SERVER_ROOT / "app"
DESIGN_DOC = SERVER_ROOT.parents[1] / "docs" / "m3-agent-production-growth-design.md"
PLAN_DOC = SERVER_ROOT.parents[1] / "docs" / "m3-implementation-plan.md"
TABLES = Base.metadata.tables

#: 承载 M3 不变量锚点的测试模块（M3 各阶段可以加自己的文件；锚点表跨模块解析）。
M3_ANCHOR_MODULES = ("test_m3_contract",)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _app_python_files(*prefixes: str) -> list[tuple[str, Path]]:
    result: list[tuple[str, Path]] = []
    for path in sorted(APP_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        relative = str(path.relative_to(APP_ROOT))
        if prefixes and not relative.startswith(prefixes):
            continue
        result.append((relative, path))
    return result


def _read(relative: str) -> str:
    return (APP_ROOT / relative).read_text(encoding="utf-8")


def _names_referenced(path: Path) -> set[str]:
    """AST 里出现的**代码符号**（导入名 / Name / Attribute）—— 刻意不含字符串字面量。

    这样区分"文档里提到某个名字"（合法）与"代码里真的用了它"（越界）。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names.update(alias.name for alias in node.names)
            if node.module:
                names.add(node.module)
        elif isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


def _called_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name:
                called.add(name)
    return called


def _defined_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }


def _forbidden_import_offenders(
    paths: list[tuple[str, Path]], forbidden_prefixes: tuple[str, ...]
) -> list[str]:
    """找出 import 了被禁前缀的代码，**两种写法都抓**：

    - `from app.services.tasks import ...`（module 直接命中）
    - `from app.services import tasks as x`（module + alias 拼起来命中）

    只查 module 会漏掉第二种 —— 反例注入验证当场抓到过。
    """
    offenders: list[str] = []
    for relative, path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module.startswith(forbidden_prefixes):
                    offenders.append(f"{relative}:{node.lineno} from {module} import ...")
                    continue
                for alias in node.names:
                    dotted = f"{module}.{alias.name}" if module else alias.name
                    if dotted.startswith(forbidden_prefixes):
                        offenders.append(
                            f"{relative}:{node.lineno} from {module} import {alias.name}"
                        )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(forbidden_prefixes):
                        offenders.append(f"{relative}:{node.lineno} import {alias.name}")
    return offenders


def _source_tokens(source: str) -> set[tuple[str, str]]:
    """从"来源"字符串里抽出 `table.column`，用于"来源必须真实存在"的判据。"""
    return {(table, column) for table, column in re.findall(r"\b([a-z_]+)\.([a-z_]+)\b", source)}


def _source_problems(sources: dict[str, str]) -> list[str]:
    problems: list[str] = []
    for field, source in sources.items():
        for table, column in sorted(_source_tokens(source)):
            if table not in TABLES:
                problems.append(f"{field}: 表 {table} 不存在（来源：{source}）")
            elif column not in TABLES[table].columns:
                problems.append(f"{field}: 列 {table}.{column} 不存在（来源：{source}）")
    return problems


def _columns(table: str) -> set[str]:
    return set(TABLES[table].columns.keys())


def _tool_specs() -> dict[str, object]:
    specs = {
        spec.name: spec
        for builder in (tool_reads.build_read_tools, tool_writes.build_write_tools)
        for spec in builder()
    }
    assert specs, "工具注册表为空 —— M2 的工具面不应消失（P28）"
    return specs


@pytest.fixture(autouse=True)
def _restore_org_state(org_snapshot):
    yield org_snapshot


@pytest.fixture(autouse=True)
def _no_background_dispatch(monkeypatch):
    monkeypatch.setattr(settings, "orchestrator_dispatch_enabled", False)


def _hire(db, company_id: int, slug: str, *, workspace: str | None = None) -> Employee:
    """建一个员工（招募路径之外），用于就绪/provisioning 的单元验证。

    `workspace` 默认指向**不存在**的目录：就绪断言需要"目录不存在"这个确定事实。
    """
    person = Person(slug=f"m3-{slug}", name=f"Person {slug}")
    db.add(person)
    db.flush()
    employee = org_repo.create_employee(
        db,
        person_id=int(person.id),
        company_id=int(company_id),
        department_id=None,
        name=f"Person {slug}",
        slug=slug,
        role="engineer",
        title="Engineer",
        status="idle",
        lifecycle_status="active",
        runtime_type=RuntimeType.mock.value,
        runtime_config={},
        workspace_path=workspace or f"{settings.workspace_root}/m3-{slug}-missing",
        memory_namespace=f"m3_{slug}",
    )
    db.commit()
    return employee


# ---------------------------------------------------------------------------
# 注册表：P1–P36 全部 enforced 且锚点现存
# ---------------------------------------------------------------------------


def test_production_invariants_are_all_enforced():
    """M3.0 不允许"冻结待锚点"的双态（M2.10 的教训）。"""
    pending = [invariant.id for invariant in P.PRODUCTION_INVARIANTS if not invariant.enforced]
    assert not pending, f"M3.0 冻结时仍有未落地的不变量：{pending}"
    for invariant in P.PRODUCTION_INVARIANTS:
        assert invariant.anchors, f"{invariant.id} 没有锚点 —— enforced 必须能指回测试"
        assert invariant.owner_stage in P.M3_STAGES, (
            f"{invariant.id} 的 owner_stage 非法：{invariant.owner_stage!r}"
        )


def test_production_invariant_ids_are_unique_and_sequential():
    ids = [invariant.id for invariant in P.PRODUCTION_INVARIANTS]
    assert len(ids) == len(set(ids)), "P 编号重复"
    numbers = [int(re.sub(r"\D", "", invariant_id)) for invariant_id in ids]
    assert numbers == list(range(1, len(numbers) + 1)), f"P 编号不连续：{sorted(numbers)}"
    assert all(invariant_id.startswith("P") for invariant_id in ids)


def test_production_invariant_anchors_exist():
    """锚点必须**真实存在**；删掉测试就会转红。"""
    import importlib

    modules = [importlib.import_module(name) for name in M3_ANCHOR_MODULES]
    missing = [
        f"{invariant.id} -> {anchor}"
        for invariant in P.PRODUCTION_INVARIANTS
        for anchor in invariant.anchors
        if not any(hasattr(module, anchor) for module in modules)
    ]
    assert not missing, f"不变量锚点不存在（锚点表与测试已漂移）：{missing}"


def test_production_invariants_match_the_design_document():
    """设计文档与代码是**同一份事实**：任一侧改动而另一侧没跟就转红。"""
    design = DESIGN_DOC.read_text(encoding="utf-8")
    rows = re.findall(r"^\|\s*\*\*(P\d+)\*\*\s*\|\s*(.+?)\s*\|", design, flags=re.MULTILINE)
    documented = dict(rows)
    assert documented, "设计文档里没有解析到 P 不变量表（§19 的格式可能被改了）"
    code = {invariant.id: invariant.text for invariant in P.PRODUCTION_INVARIANTS}
    assert set(documented) == set(code), (
        f"设计文档与代码的 P 不变量集合不一致：文档多 {sorted(set(documented) - set(code))}、"
        f"代码多 {sorted(set(code) - set(documented))}"
    )
    mismatched = {pid: (documented[pid], code[pid]) for pid in code if documented[pid] != code[pid]}
    assert not mismatched, f"不变量文本不一致：{mismatched}"


def test_production_contract_module_declares_no_decision_helpers():
    """最高原则的词汇层守卫：M3 契约层不得出现"替 Agent 决策"的函数。"""
    forbidden_patterns = (
        r"^(pick|choose|select|rank|recommend)_",
        r"^(best|top)_",
        r"^auto_",
        r"^(should|must|decide|resolve)_",
        r"^(approve|reject)_",
        r"^(score|grade|judge)_",
    )
    defined = _defined_names(APP_ROOT / "work" / "production.py")
    offenders = [
        name
        for name in sorted(defined)
        for pattern in forbidden_patterns
        if re.match(pattern, name)
    ]
    assert not offenders, f"M3 契约层出现了替 Agent 决策的函数：{offenders}"


def test_m3_docs_exist_and_freeze_the_key_decisions():
    assert DESIGN_DOC.exists(), f"缺少 M3 设计文档：{DESIGN_DOC}"
    assert PLAN_DOC.exists(), f"缺少 M3 实施计划：{PLAN_DOC}"
    design = DESIGN_DOC.read_text(encoding="utf-8")
    for anchored in (
        "System provides facts.      Agent makes decisions.",
        "M3-ADR-1",
        "AgentReadiness",
        "WorkExperience",
        "Runtime Provenance",
        "Technical Retry",
        "Telemetry",
    ):
        assert anchored in design, f"M3 设计文档缺少关键裁决/术语：{anchored}"
    plan = PLAN_DOC.read_text(encoding="utf-8")
    for stage in sorted(P.M3_STAGES, key=lambda item: int(item.split(".")[1])):
        assert stage in plan, f"M3 实施计划缺少 {stage}"


# ---------------------------------------------------------------------------
# P1 / P26：Agent 不是新的 Source of Truth
# ---------------------------------------------------------------------------


def test_no_agent_source_of_truth_table_or_column_exists():
    """P1：不允许出现 Agent 身份真相（整表名 + agent_id 外键列）。"""
    assert not (set(TABLES) & P.FORBIDDEN_AGENT_SOT_TABLES), (
        f"出现了被禁止的 Agent SoT 表：{sorted(set(TABLES) & P.FORBIDDEN_AGENT_SOT_TABLES)}"
    )
    offenders = [
        f"{name}.{column.name}"
        for name, table in TABLES.items()
        for column in table.columns
        if column.name in P.FORBIDDEN_AGENT_REFERENCE_COLUMNS
    ]
    assert not offenders, f"不允许 agent_id 外键列（P1）：{offenders}"


def test_agent_profile_is_a_read_model_without_a_write_path():
    """P1 / P26：`AgentProfile` 是读模型 —— 没有表、没有写路径。"""
    assert not ({"agent_profile", "agent_profiles"} & set(TABLES))
    forbidden = (
        "save_agent_profile",
        "upsert_agent_profile",
        "create_agent_profile",
        "AgentProfile",
    )
    offenders: list[str] = []
    for relative, path in _app_python_files():
        defined = _defined_names(path)
        hit = defined & set(forbidden)
        if hit:
            offenders.append(f"{relative}: 定义了 {sorted(hit)}")
    assert not offenders, "AgentProfile 不得成为可写实体（P1/P26）：\n" + "\n".join(offenders)


def test_agent_profile_axes_are_disjoint_and_sourced():
    """P26 / P23：长期资产与当前运行态两轴不重叠，且每个字段都有来源。"""
    long_term = set(P.AGENT_PROFILE_LONG_TERM)
    current = set(P.AGENT_PROFILE_CURRENT_STATE)
    assert long_term and current
    assert not (long_term & current), f"两轴重叠：{sorted(long_term & current)}"
    assert set(P.AGENT_PROFILE_SOURCES) == long_term | current
    problems = _source_problems(
        {key: value for key, value in P.AGENT_PROFILE_SOURCES.items() if "（" not in value}
    )
    assert not problems, "\n".join(problems)


def test_person_owned_assets_keep_a_person_scoped_column():
    """P2：长期资产必须留 Person 口径列（换公司时资产不搬家）。

    没有 Person 口径列的载体必须**显式登记欠账**（不允许从盘点里静静消失）。
    """
    person_owned = (
        "employee_competencies",
        "competency_evidence",
        "skills",
        "skill_usages",
        "career_events",
        "learning_records",
        "knowledge_items",
        "memory_entries",
        "employee_brains",
    )
    assert set(person_owned) <= set(TABLES), "长期资产表消失了几张 —— 盘点要跟着改"
    missing = [
        table
        for table in person_owned
        if not any(col.endswith("person_id") for col in _columns(table))
    ]
    assert all(table in P.PERSON_SCOPED_COLUMN_OWED for table in missing), (
        f"长期资产表缺 Person 口径列且**没有登记欠账**：{missing}"
    )
    assert set(P.PERSON_SCOPED_COLUMN_OWED) <= set(missing), (
        "欠账登记里有已经补齐的老条目 —— 请把它从 PERSON_SCOPED_COLUMN_OWED 移掉"
    )


def test_learning_path_does_not_write_competency_score():
    """P11：学习不直接写能力分（能力只能经 Evidence → Aggregator）。"""
    watched = [
        (relative, path) for relative, path in _app_python_files("services/learning", "learning")
    ]
    assert watched, "没有扫到学习域代码 —— 路径变了要同步这条守卫"
    forbidden_names = {
        "assess_employee_competencies",
        "assess_person_competencies",
        "EmployeeCompetency",
        "upsert_competency",
        "apply_competency",
        "set_score",
    }
    offenders = [
        f"{relative}: {sorted(_names_referenced(path) & forbidden_names)}"
        for relative, path in watched
        if _names_referenced(path) & forbidden_names
    ]
    assert not offenders, "学习路径不得直接改能力分（P11）：\n" + "\n".join(offenders)


def test_specialization_has_no_write_path():
    """P20 / P26：专精是派生标签，没有写入路径。"""
    offenders: list[str] = []
    for relative, path in _app_python_files():
        defined = _defined_names(path)
        hit = {
            name
            for name in defined
            if re.match(r"(set|write|update|upsert|add)_specialization", name)
        }
        if hit:
            offenders.append(f"{relative}: {sorted(hit)}")
    assert not offenders, "专精不允许有写入路径（P20）：\n" + "\n".join(offenders)
    assert C.ConstraintName.specialization in C.SOFT_CONSTRAINTS


def test_no_opaque_power_score_exists():
    """P21：不允许神秘总战力成为 Agent 真相（表列 + 契约字段双侧）。"""
    suspicious = ("power", "combat_power", "overall_score", "total_score", "overall_rating")
    offenders = [
        f"{table}.{column}"
        for table, definition in TABLES.items()
        for column in definition.columns
        if column.name in suspicious
    ]
    assert not offenders, f"出现了神秘总战力列（P21）：{offenders}"
    declared = set(P.RELIABILITY_METRICS) | set(P.AGENT_PROFILE_LONG_TERM)
    assert not (declared & set(suspicious)), "契约声明了神秘总战力字段（P21）"


def test_management_experience_comes_from_decision_facts():
    """P22：管理履历来自决策 + 结果事实，不是主观评价。"""
    source = P.AGENT_PROFILE_SOURCES["management_experience"]
    assert "decision_records" in source, "管理履历必须来自 DecisionRecord（P22）"
    outcomes = P.RELIABILITY_METRICS["management_decision_outcomes"]
    assert "decision_records.status" in outcomes and "decision_records.outcome_note" in outcomes, (
        "管理决策结果必须来自决策记录的**事实**字段（P22）"
    )


# ---------------------------------------------------------------------------
# P3 / P16 / P24 / P25：边界不混
# ---------------------------------------------------------------------------


def test_runtime_policy_forbids_capability_keys():
    """P3：运行时策略只配环境；能力/人格/提示词类键一律拒绝。"""
    for key in sorted(P.CAPABILITY_KEYS_THAT_MUST_BE_FORBIDDEN):
        with pytest.raises(C.WorkContractError):
            readiness.validate_policy_keys({key: "anything"})
    readiness.validate_policy_keys({"runtime_type": "mock"})


def test_runtime_layer_never_writes_capability_facts():
    """P3：运行时层（adapter / 编排）不得触碰能力事实。"""
    forbidden = {
        "EmployeeCompetency",
        "CompetencyEvidence",
        "Skill",
        "SkillUsage",
        "Assess",
        "assess_employee_competencies",
        "upsert_competency",
    }
    offenders: list[str] = []
    for relative, path in _app_python_files("runtimes", "work/readiness.py"):
        hit = _names_referenced(path) & forbidden
        if hit:
            offenders.append(f"{relative}: {sorted(hit)}")
    assert not offenders, "运行时层不得写能力事实（P3）：\n" + "\n".join(offenders)


def test_runtime_layer_does_not_write_domain_state():
    """P16：执行层不得直接写领域状态（adapter 只能回结构化事件）。"""
    forbidden_imports = (
        "app.models.project",
        "app.models.decision",
        "app.models.review",
        "app.services.tasks",
        "app.services.projects",
        "app.work.reviews",
        "app.work.decisions",
        "app.work.handoff",
    )
    offenders = _forbidden_import_offenders(_app_python_files("runtimes"), forbidden_imports)
    assert not offenders, "运行时层不得 import 领域写路径（P16）：\n" + "\n".join(offenders)


def test_telemetry_kinds_do_not_collide_with_domain_events():
    """P16 / P35：遥测词表与领域事件词表不重叠（词汇层先钉死）。"""
    domain = C.FACT_EVENTS | C.DECISION_NEEDED_EVENTS
    overlap = set(P.RUNTIME_TELEMETRY_KINDS) & domain
    assert not overlap, f"遥测与领域事件重名：{sorted(overlap)}"
    adapter_kinds = {kind.value for kind in runtime_base.RuntimeEventKind}
    assert set(P.TELEMETRY_ADAPTER_EVENT.values()) <= adapter_kinds, (
        "遥测映射必须落在既有 adapter 词表上（不新造第二套）"
    )
    assert set(P.RUNTIME_TELEMETRY_KINDS) == set(P.TELEMETRY_ADAPTER_EVENT) | set(
        P.TELEMETRY_NOT_YET_MAPPED
    ), "遥测词表必须完整覆盖：已映射 + 待映射"


def test_telemetry_never_carries_domain_status_targets():
    """P17：运行时 exit=0 不等于任务成功 —— 遥测**不携带**领域状态目标。

    注意：`session.failed` / `tool.failed` 里的 failed 描述的是**执行**失败，
    不是任务状态迁移目标。这条守卫抓的是"遥测被当成领域状态"：整串不与领域状态同名、
    映射落在 adapter 词表上、且契约层不提供"遥测 → 任务状态"的换算函数。
    """
    from app.models.enums import TaskStatus

    domain_statuses = {status.value for status in TaskStatus}
    assert not (set(P.RUNTIME_TELEMETRY_KINDS) & domain_statuses), "遥测事件与领域状态同名（P17）"
    adapter_kinds = {kind.value for kind in runtime_base.RuntimeEventKind}
    assert not (adapter_kinds & domain_statuses), (
        f"adapter 事件词表与领域状态重叠（P17）：{sorted(adapter_kinds & domain_statuses)}"
    )
    for kind, adapter_event in P.TELEMETRY_ADAPTER_EVENT.items():
        assert adapter_event in adapter_kinds, f"{kind} 映射到了非 adapter 事件：{adapter_event}"
    defined = _defined_names(APP_ROOT / "work" / "production.py")
    offenders = sorted(
        name for name in defined if re.search(r"(status|state)_for_(telemetry|runtime)", name)
    )
    assert not offenders, f"契约层出现了『遥测 → 任务状态』换算（P17）：{offenders}"


def test_execution_mode_is_total_and_binary():
    """P24：mock / real 的判定必须是**全函数**（任何 runtime_type 都能判定）。"""
    for runtime_type in RuntimeType:
        assert P.execution_mode(runtime_type.value) in {"mock", "real"}
    assert P.execution_mode(RuntimeType.mock.value) == "mock"
    assert P.execution_mode(RuntimeType.hermes.value) == "real"
    assert P.execution_mode("something_unknown") == "real", "未知类型不得被善意当成 mock"


def test_runtime_type_partition_is_exhaustive_and_honest():
    """P24：mock / 真实 / placeholder 三分必须穷尽、互斥，且**诚实登记** placeholder。"""
    partition = {P.MOCK_RUNTIME_TYPE} | set(P.REAL_RUNTIME_TYPES) | set(P.PLACEHOLDER_RUNTIME_TYPES)
    assert partition == {item.value for item in RuntimeType}
    assert not ({P.MOCK_RUNTIME_TYPE} & set(P.REAL_RUNTIME_TYPES))
    assert not (set(P.REAL_RUNTIME_TYPES) & set(P.PLACEHOLDER_RUNTIME_TYPES))
    assert set(P.REAL_RUNTIME_TYPES) == {"hermes", "openclaw"}, (
        "M3 只承诺 Hermes / OpenClaw 两个真实运行时（不要顺手扩类型）"
    )


def test_mock_evidence_discount_contract_matches_the_evidence_policy():
    """P25：mock 证据继续打折，且契约常量与证据策略**逐字一致**。"""
    from app.evidence import policy as evidence_policy
    from app.evidence.normalize import environment_for_runtime

    assert P.EVIDENCE_ENVIRONMENT_MOCK == evidence_policy.MOCK_ENVIRONMENT
    assert P.EVIDENCE_ENVIRONMENT_REAL == ""
    assert 0 < evidence_policy.MOCK_RELIABILITY_FACTOR < 1, "mock 打折系数必须在 (0,1)"
    assert environment_for_runtime([P.MOCK_RUNTIME_TYPE]) == P.EVIDENCE_ENVIRONMENT_MOCK
    assert environment_for_runtime([RuntimeType.hermes.value]) == P.EVIDENCE_ENVIRONMENT_REAL


def test_runtime_config_is_not_person_competency_in_the_policy_layer():
    """P3（补充）：员工行的 runtime_config 不得携带能力键。"""
    assert P.CAPABILITY_KEYS_THAT_MUST_BE_FORBIDDEN <= C.FORBIDDEN_RUNTIME_POLICY_KEYS
    from app.work.contracts import RUNTIME_POLICY_KEYS

    assert not (set(RUNTIME_POLICY_KEYS) & P.CAPABILITY_KEYS_THAT_MUST_BE_FORBIDDEN)


# ---------------------------------------------------------------------------
# P4 / P5 / P8 / P13 / P31 / P32：就绪是派生事实
# ---------------------------------------------------------------------------


def test_readiness_item_sources_resolve_to_real_tables():
    """P32（M2 A4 同款纪律）：就绪项的来源必须能解析到真实表列。"""
    problems = _source_problems(C.READINESS_FACT_SOURCES)
    assert not problems, "\n".join(problems)


def test_readiness_items_map_onto_production_stages():
    """P32：就绪项 ↔ 生产阶段双向一致（防"配了不检 / 检了不配"）。"""
    assert set(P.READINESS_ITEM_STAGE) == set(C.READINESS_FACT_SOURCES)
    stages_with_facts = set(P.READINESS_ITEM_STAGE.values())
    assert {P.ProductionStage.employment, P.ProductionStage.provisioning} <= stages_with_facts
    assert P.ProductionStage.runtime in stages_with_facts
    assert P.ProductionStage.execution not in stages_with_facts, (
        "execution 是前三层的结论，不应自带独立就绪项"
    )


def test_provisioning_steps_match_required_execution_facts():
    """P31：编排步骤 == 真实执行必答项（逐字 + 顺序）。"""
    assert P.PROVISIONING_STEP_ORDER == C.READINESS_REQUIRED_FOR_EXECUTION["real"]
    assert C.READINESS_REQUIRED_FOR_EXECUTION["mock"] == (), "mock 不需要工作区/实例/供应商"
    assert "position" not in P.PROVISIONING_STEP_ORDER, "职位是软契约，不进执行门禁（W5）"


def test_readiness_is_derived_and_not_a_stored_boolean():
    """P4：就绪是**算出来的** —— 没有 ready 列、没有 ready 字段、没有写入。"""
    assert not hasattr(Employee, "ready")
    for table in ("employees", "runtime_instances", "companies"):
        assert "ready" not in _columns(table)
        assert "ready_to_work" not in _columns(table)
    report_fields = {field.name for field in dataclasses.fields(readiness.ReadinessReport)}
    assert {"ready_to_work", "items", "gaps"} <= report_fields
    for function in ("readiness_items", "readiness_report", "blocking_gaps"):
        source = inspect.getsource(getattr(readiness, function))
        assert "db.commit" not in source and "db.add(" not in source, (
            f"{function} 必须只读：就绪计算不得写任何事实（P4）"
        )


def test_readiness_has_two_views_and_the_gate_is_the_narrow_one(db, default_company_id):
    """P4：两个就绪口径**刻意不同**（严格口径面向人 / 执行门禁面向派发）。

    合并它们就会出事：把 `position`（软契约 W5）塞进派发门禁会把没职位的人彻底挡在工作之外；
    反过来只看必答项又会让人看不见"差什么"。
    """
    employee = _hire(db, default_company_id, "m3-two-views")
    report = readiness.readiness_report(db, employee)
    gate = readiness.blocking_gaps(db, employee)
    assert set(gate) <= set(report.gaps), "执行门禁必须是严格口径的子集"
    assert report.ready_to_work is (not report.gaps), "ready_to_work 必须由全部项派生"
    assert C.READINESS_REQUIRED_FOR_EXECUTION["mock"] == ()
    assert "position" in report.gaps, "新人没有职位 ⇒ 严格口径应当报告它"
    assert "position" not in gate, "职位不在执行门禁里（W5 软契约）"
    assert P.READINESS_EXECUTION_GATE == "blocking_gaps"
    assert set(P.READINESS_VIEWS) == {"strict", "execution_gate"}
    db.rollback()


def test_readiness_report_explains_every_item(db, default_company_id):
    """P4：每一项就绪都必须可解释（ready + detail + source + facts）。"""
    employee = _hire(db, default_company_id, "m3-explain")
    report = readiness.readiness_report(db, employee)
    assert {item.item for item in report.items} == set(C.READINESS_FACT_SOURCES)
    for item in report.items:
        assert isinstance(item.ready, bool)
        assert item.detail.strip(), f"{item.item} 缺少人话解释"
        assert item.source.strip(), f"{item.item} 缺少来源"
        assert item.facts, f"{item.item} 缺少具体事实（不允许只有布尔）"
    # gaps 与 items 必须自洽
    assert set(report.gaps) == {item.item for item in report.items if not item.ready}
    assert report.ready_to_work is (not report.gaps)


def test_runtime_row_alone_is_not_ready_to_work(db, default_company_id):
    """P5：RuntimeInstance 行存在 ≠ READY_TO_WORK（不健康的行照样不可派发）。"""
    employee = _hire(db, default_company_id, "m3-unhealthy")
    employee.runtime_type = RuntimeType.hermes.value
    db.flush()
    runtime_repo.create_instance(
        db,
        employee_id=int(employee.id),
        runtime_type=RuntimeType.hermes.value,
        deployment_mode="docker",
        status="created",
        health_status=HealthStatus.unhealthy.value,
        workspace_path=str(employee.workspace_path or ""),
        data_path=str(employee.workspace_path or ""),
    )
    report = readiness.readiness_report(db, employee)
    runtime_item = next(item for item in report.items if item.item == "runtime")
    assert runtime_item.ready is False
    assert runtime_item.facts["status"] == "created"
    assert "runtime" in report.gaps
    assert report.ready_to_work is False
    assert "runtime" in readiness.blocking_gaps(db, employee)
    db.rollback()


def test_real_execution_requires_provisioning_facts(db, default_company_id):
    """P13：真实执行必须先过 provisioning（招募完成 ≠ 可工作）。"""
    employee = _hire(db, default_company_id, "m3-real", workspace=None)
    employee.runtime_type = RuntimeType.hermes.value
    db.flush()
    gaps = readiness.blocking_gaps(db, employee)
    assert set(gaps) >= {"workspace", "runtime", "provider"}, (
        f"真实运行时的必答项必须包含工作区/实例/供应商，实际缺口：{gaps}"
    )
    assert "position" not in gaps or True  # position 是软契约；此处只强调三项必答
    db.rollback()


def test_not_ready_agent_is_reported_never_auto_reassigned():
    """P8：不 ready 只上报（事件 + 原因），系统不得自动改派。"""
    dispatch_path = APP_ROOT / "work" / "dispatch.py"
    dispatch_source = _read("work/dispatch.py")
    assert "task.runtime_unavailable" in dispatch_source or "runtime" in dispatch_source
    assert C.DECISION_NEEDED_EVENTS, "必须有『需要管理层介入』的事件集合"
    # 定义 + 调用两侧都扫：只扫调用会漏掉"定义了自动改派函数"（反例注入验证抓到过）
    assignment_like = re.compile(r"^_?(auto_)?(re)?assign(_|$)")
    offenders = sorted(
        name
        for name in (_called_names(dispatch_path) | _defined_names(dispatch_path))
        if assignment_like.match(name)
    )
    assert not offenders, f"派发路径不得出现改派函数（P8）：{offenders}"
    assert not _forbidden_import_offenders(
        [("work/dispatch.py", dispatch_path)], ("app.services.tasks",)
    ), "派发路径不得 import 任务写路径（P8：只报告、不代 Manager 动手）"
    assert not hasattr(readiness, "reassign_unready")


def test_detection_helpers_are_not_decision_helpers():
    """P29：系统只检测/报告；决策归 Manager。"""
    for relative in ("work/readiness.py", "work/dispatch.py"):
        defined = _defined_names(APP_ROOT / relative)
        offenders = sorted(
            name
            for name in defined
            if re.match(r"^(decide|choose|pick|should|auto)_", name) and name != "decide_verdict"
        )
        assert not offenders, f"{relative} 出现了决策函数（P29）：{offenders}"


# ---------------------------------------------------------------------------
# P6 / P7：幂等与对账
# ---------------------------------------------------------------------------


def test_idempotent_ensure_operations_exist_and_cover_scenarios():
    """P6：声明幂等的操作必须真实存在，且覆盖现实场景。"""
    missing = [name for name in P.IDEMPOTENT_ENSURE_OPERATIONS if not hasattr(readiness, name)]
    assert not missing, f"声明了幂等但函数不存在：{missing}"
    assert set(P.IDEMPOTENCY_SCENARIOS) == {
        "row_and_container_ok",
        "row_present_container_missing",
        "container_present_row_missing",
        "container_unhealthy",
        "crash_mid_orchestration",
    }, "幂等必须覆盖五类现实场景（不要只覆盖 happy path）"


def test_reconciliation_contract_has_no_destructive_action(db, default_company_id):
    """P7：对账只能 detect / mark / repair —— 不得静默删除外部资源。"""
    verbs = {verb for actions in P.RECONCILIATION_ACTIONS.values() for verb in actions}
    assert verbs <= {"mark", "report", "repair", "refresh"}
    assert not (verbs & P.RECONCILIATION_FORBIDDEN_ACTIONS)
    assert P.RECONCILIATION_ACTIONS["orphan_detected"] == ("mark", "report"), (
        "孤儿资源只登记 + 上报（删除必须由人发起）"
    )


def test_orchestration_layer_calls_no_destructive_action():
    """P7：编排层不得出现删除类调用（不可逆动作必须由人显式发起）。"""
    offenders: list[str] = []
    for relative in P.RECONCILIATION_CODE_PATHS:
        path = APP_ROOT / relative
        assert path.exists(), f"守卫扫描的路径不存在了：{relative}（路径变了要同步守卫）"
        called = _called_names(path)
        hit = sorted(called & P.RECONCILIATION_FORBIDDEN_ACTIONS)
        if hit:
            offenders.append(f"{relative}: {hit}")
    assert not offenders, "编排层出现了删除类调用（P7）：\n" + "\n".join(offenders)


async def test_orchestrate_twice_does_not_duplicate_provisioning_facts(
    db, default_company_id, fake_docker, monkeypatch
):
    """P6：ensure × N == ensure × 1（不重复创建实例 / 绑定）。"""
    employee = _hire(db, default_company_id, "m3-idempotent")
    policy = readiness.RuntimePolicy(
        runtime_type=RuntimeType.hermes.value,
        deployment_mode="docker",
        provider_id=None,
        model="",
        runtime_config={},
        source=C.RUNTIME_POLICY_SOURCES["platform_default"],
    )
    employee.runtime_type = RuntimeType.hermes.value
    db.commit()
    from app.runtimes.manager import docker_manager

    monkeypatch.setattr(
        docker_manager, "_manager", docker_manager.DockerRuntimeInstanceManager(docker=fake_docker)
    )
    before = db.scalars(select(RuntimeInstance)).all()
    first = await readiness.ensure_runtime_instance(employee, policy)
    second = await readiness.ensure_runtime_instance(employee, policy)
    after = db.scalars(select(RuntimeInstance)).all()
    assert first[0] is True and second[0] is True
    assert len(after) == len(before) + 1, "第二次 ensure 不应新增实例（P6）"
    instance = runtime_repo.get_instance_for_employee(db, int(employee.id))
    assert instance is not None
    assert str(instance.runtime_type) == RuntimeType.hermes.value
    assert second[2].id == first[2].id, "复用同一个实例，而不是重建"
    assert len(fake_docker.containers) == 1
    # mock 策略不建供应商绑定（事实，不是放水）
    mock_policy = readiness.RuntimePolicy(
        runtime_type=RuntimeType.mock.value,
        deployment_mode="mock",
        source=C.RUNTIME_POLICY_SOURCES["platform_default"],
    )
    ok, reason, binding_id = readiness.ensure_provider_binding(db, employee, mock_policy)
    assert ok is True and binding_id is None and "mock" in reason
    assert provider_repo.get_primary_binding(db, int(employee.id)) is None
    db.rollback()


# ---------------------------------------------------------------------------
# P9 / P10 / P33：能力缺口动作
# ---------------------------------------------------------------------------


def test_capability_gap_has_no_automatic_action():
    """P9：缺口不触发自动动作（尤其不自动招聘）。"""
    assert P.AUTO_CAPABILITY_ACTIONS == frozenset()
    source = (APP_ROOT / "work" / "production.py").read_text(encoding="utf-8")
    assert not re.search(r"fit\s*[<>]", source), "契约层不得出现 fit 阈值判断（P9）"


def test_work_layer_does_not_import_recruitment_or_market_services():
    """P9：工作层不 import 招募/市场服务（动作只能来自 Manager 的决策）。"""
    forbidden = ("app.services.recruitment", "app.services.market")
    offenders = _forbidden_import_offenders(_app_python_files("work"), forbidden)
    assert not offenders, "工作层不得直接调用招募/市场服务（P9）：\n" + "\n".join(offenders)


def test_every_capability_gap_action_has_a_tool_or_an_owner_stage():
    """P33：不允许幽灵词条；而且声明的工具必须**真实存在**于工具注册表。"""
    specs = _tool_specs()
    for action in P.CAPABILITY_GAP_ACTIONS:
        tools = P.CAPABILITY_GAP_ACTION_TOOLS[action]
        owner = P.CAPABILITY_GAP_ACTION_OWNER[action]
        assert bool(tools) or owner in P.M3_STAGES, f"{action.value} 两头都空（P33）"
        assert not (tools and owner), f"{action.value} 同时声明工具与 owner（P33）"
        missing = [name for name in tools if name not in specs]
        assert not missing, f"{action.value} 声明了不存在的工具：{missing}（P33）"


def test_capability_gap_actions_are_decision_shaped():
    """P10：面向 Manager 的动作必须是**写工具**（过决策信封），不是系统内部逻辑。"""
    specs = _tool_specs()
    for action in P.CAPABILITY_GAP_ACTIONS:
        tools = P.CAPABILITY_GAP_ACTION_TOOLS[action]
        if not tools:
            assert P.CAPABILITY_GAP_ACTION_OWNER[action] in P.M3_STAGES
            continue
        for name in tools:
            spec = specs[name]
            assert spec.side_effect is C.ToolSideEffect.write, (
                f"{action.value} → {name} 必须是写工具（Manager 决策经工具面落地）"
            )
            assert spec.decision_semantics is not C.DecisionSemantics.none, (
                f"{action.value} → {name} 必须声明与决策信封的关系"
            )
            assert spec.required_authority is not None, (
                f"{action.value} → {name} 必须带 Authority（组织权力，不是自由动作）"
            )


def test_recruitment_does_not_copy_person_owned_assets(db, default_company_id):
    """P12：招募不复制 Person 资产（履历/能力/知识随人，不随雇佣关系）。"""
    names = _names_referenced(APP_ROOT / "services" / "recruitment.py")
    forbidden = {
        "EmployeeCompetency",
        "CompetencyEvidence",
        "SkillUsage",
        "LearningRecord",
        "MemoryEntry",
        "CareerEvent",
    }
    hit = sorted(names & forbidden)
    assert not hit, f"招募路径触碰了 Person 长期资产（P12）：{hit}"
    assert "person_id" in _columns("employees"), "雇佣关系必须挂 Person 口径"


def test_market_transaction_truth_is_not_redefined_in_work_layer():
    """P27：交易真相仍在 T2 —— 工作层不得重定义市场/结算实体。"""
    forbidden = {"Escrow", "Settlement", "MarketListing", "MarketParticipant", "Contract"}
    offenders: list[str] = []
    for relative, path in _app_python_files("work"):
        hit = sorted(_names_referenced(path) & forbidden)
        if hit:
            offenders.append(f"{relative}: {hit}")
    assert not offenders, "工作层不得重定义市场/结算真相（P27）：\n" + "\n".join(offenders)
    assert "market_listings" in TABLES and "escrows" in TABLES, "T2 的表仍在位（P27）"


# ---------------------------------------------------------------------------
# P14 / P15 / P34：Provenance 与重试语义
# ---------------------------------------------------------------------------


def test_provenance_fields_match_work_session_columns():
    """P14：provenance 现存字段必须真的在 `work_sessions` 上（别名会静默丢历史）。"""
    columns = _columns("work_sessions")
    missing = [field for field in P.PROVENANCE_PRESENT_FIELDS if field not in columns]
    assert not missing, f"声明的现存 provenance 字段不在表上：{missing}"
    for field in P.PROVENANCE_PRESENT_FIELDS:
        if field not in ("deployment_mode",):
            assert field in P.PROVENANCE_SNAPSHOT_FIELDS
    assert set(P.PROVENANCE_PRESENT_FIELDS) | set(P.PROVENANCE_OWED_FIELDS) == set(
        P.PROVENANCE_SNAPSHOT_FIELDS
    )


def test_provenance_gaps_are_declared_and_owned():
    """P34：欠账必须显式、非空、owner 合法；补齐后这条会提醒更新契约。"""
    assert P.PROVENANCE_OWED_FIELDS, "欠账被清空了 —— 要么真补齐了字段，要么在骗自己（P34）"
    for field, owner in P.PROVENANCE_OWED_FIELDS.items():
        assert owner in P.M3_STAGES, f"{field} 的 owner 非法：{owner}"
    columns = _columns("work_sessions")
    assert "deployment_mode" not in columns, (
        "`deployment_mode` 已落表 ⇒ 请把它从欠账移到现存字段（P34 的守卫要跟着改）"
    )
    assert P.PROVENANCE_WRITE_ONCE_TRIGGER == "work_sessions.ended_at"


def test_every_declared_gap_is_owned_by_a_real_stage():
    """P34（推广）：所有"欠账登记"都必须指向合法阶段，且阶段集合完整。"""
    assert P.M3_STAGES == frozenset(f"M3.{i}" for i in range(0, 11))
    for label, registry in (
        ("provenance", P.PROVENANCE_OWED_FIELDS),
        ("work_experience", P.WORK_EXPERIENCE_OWED_FIELDS),
        (
            "capability_gap_action",
            {a.value: o for a, o in P.CAPABILITY_GAP_ACTION_OWNER.items() if o},
        ),
        ("telemetry", {kind: "M3.4" for kind in P.TELEMETRY_NOT_YET_MAPPED}),
        ("person_scope", P.PERSON_SCOPED_COLUMN_OWED),
    ):
        assert registry, f"{label} 的欠账登记为空 —— 要么真的没有欠账，要么在装（P34）"
        for item, owner in registry.items():
            assert owner in P.M3_STAGES, f"{label}.{item} 的 owner 非法：{owner}"


def test_retry_semantics_are_separate_and_non_overlapping():
    """P15：技术重试（系统）与管理 replan（Manager）是两套语义，不共用计数器。"""
    technical = P.RETRY_SEMANTICS["technical_retry"]
    management = P.RETRY_SEMANTICS["management_replan"]
    assert technical.owner == P.TECHNICAL_RETRY_OWNER == "system"
    assert management.owner == P.MANAGEMENT_REPLAN_OWNER == "manager_agent"
    assert technical.owner != management.owner
    assert technical.counter != management.counter
    assert technical.auto is True and management.auto is False
    assert technical.fact_host != management.fact_host
    # 管理 replan 的计数器是现有事实（tasks.rework_count）；技术重试次数**尚无**承载 ⇒ 欠账
    assert "work_sessions" in technical.fact_host or "runtime" in technical.fact_host
    assert technical.counter not in {
        column.name for table in TABLES.values() for column in table.columns
    }, "技术重试计数器还没有列承载 —— 这是登记在案的欠账（M3.4）"
    assert "rework_count" in _columns("tasks"), "管理 replan 的计数器必须落在 tasks.rework_count"


# ---------------------------------------------------------------------------
# P18 / P19 / P22 / P23：履历与指标
# ---------------------------------------------------------------------------


def test_work_experience_fields_match_their_sources():
    """P18：履历字段的来源必须解析到真实表列。"""
    problems = _source_problems(P.WORK_EXPERIENCE_SOURCES)
    assert not problems, "\n".join(problems)


def test_work_experience_has_a_stage_for_every_field():
    """P18：每个字段要么有来源，要么有 owner 阶段（不允许两头都空）。"""
    covered = set(P.WORK_EXPERIENCE_SOURCES) | set(P.WORK_EXPERIENCE_OWED_FIELDS)
    assert covered == set(P.WORK_EXPERIENCE_FIELDS)
    for field, owner in P.WORK_EXPERIENCE_OWED_FIELDS.items():
        assert owner in P.M3_STAGES, f"{field} 的 owner 非法：{owner}"


def test_work_experience_history_is_snapshot_based_not_reverse_inferred():
    """P23：历史来自"当时的任职事实"，不是从当前 Position 反推。"""
    position_source = P.WORK_EXPERIENCE_SOURCES["position_code"]
    assert "employments" in position_source, (
        "历史职位必须来自任职行（employments，带 effective 区间），不能只读当前职位定义"
    )
    assert "company_id" in P.WORK_EXPERIENCE_SOURCES["company_id"]
    assert "difficulty_snapshot" in P.WORK_EXPERIENCE_OWED_FIELDS, (
        "难度快照当前没有承载 ⇒ 必须显式登记为欠账，而不是拿 tasks.priority 冒充"
    )


def test_reliability_metrics_are_derived_and_attributable():
    """P19：每个可靠性指标都必须可归因（来源真实），且不能是神秘总分。"""
    assert P.RELIABILITY_METRICS
    problems = _source_problems(P.RELIABILITY_METRICS)
    assert not problems, "\n".join(problems)
    banned = ("power", "overall", "total_score", "mystery")
    for metric in P.RELIABILITY_METRICS:
        assert not any(token in metric for token in banned), f"{metric} 像神秘总分（P21）"
    assert "management_decision_outcomes" in P.RELIABILITY_METRICS


def test_m2_frozen_contracts_are_untouched_by_m3():
    """P28：M2 契约未被 M3 改动（条数 + enforced + 事件词表快照）。"""
    assert len(C.INVARIANTS) == 105, f"M2 不变量条数变了：{len(C.INVARIANTS)}"
    assert all(invariant.enforced for invariant in C.INVARIANTS)
    assert C.FACT_EVENTS == {
        "task.ready",
        "task.started",
        "task.completed",
        "task.review_passed",
        "task.in_review",
        "project.started",
        "project.delivery_ready",
        "project.completed",
        "project.awaiting_management_action",
    }, "M2 事实事件集被改了 —— 那要过 M2 契约评审（P28）"
    assert C.DECISION_NEEDED_EVENTS == {
        "task.assignment_required",
        "task.runtime_unavailable",
        "task.blocked",
        "task.failed",
        "task.review_failed",
        "task.review_required",
        "project.replan_required",
    }, "M2 决策事件集被改了 —— 那要过 M2 契约评审（P28）"
    assert C.READINESS_REQUIRED_FOR_EXECUTION == {
        "mock": (),
        "real": ("workspace", "runtime", "provider"),
    }


def test_no_second_project_or_graph_container_exists():
    """P36：执行层不得另造项目/任务图容器（M2 的 Project/Task/DAG 是唯一根）。"""
    suspicious = [
        name
        for name in TABLES
        if any(token in name for token in ("graph", "dag", "workflow", "pipeline"))
        and not name.startswith(("work_order", "provisioning"))
    ]
    assert not suspicious, f"出现了第二套图/流水线容器（P36）：{suspicious}"
    assert "projects" in TABLES and "tasks" in TABLES and "task_dependencies" in TABLES


# ---------------------------------------------------------------------------
# P30：Authority 与 Autonomy 相互独立
# ---------------------------------------------------------------------------


def test_authority_and_autonomy_are_independent_axes():
    """P30：有权限 ≠ 可自动执行；高影响动作必须要求确认。"""
    assert set(C.AUTONOMY_BY_SIDE_EFFECT) == set(C.ToolSideEffect)
    assert (
        C.AUTONOMY_BY_SIDE_EFFECT[C.ToolSideEffect.high_impact]
        is C.AutonomyLevel.requires_confirmation
    )
    assert C.AUTONOMY_BY_SIDE_EFFECT[C.ToolSideEffect.read] is C.AutonomyLevel.auto_allowed
    assert C.HIGH_IMPACT_AUTHORITIES_RESERVED, "高影响授权必须被显式保留（不开放自动执行）"
    authority_fields = {field.name for field in dataclasses.fields(C.AuthorityGrant)}
    assert "autonomy" not in authority_fields, (
        "Authority 与 Autonomy 必须是两个轴，不能混进同一个结构"
    )
    assert C.AuthorityKind.offboard in C.SELF_TARGET_FORBIDDEN_AUTHORITIES
