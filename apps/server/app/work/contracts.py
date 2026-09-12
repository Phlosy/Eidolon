"""M2 工作与组织运行域契约（M2.0 Work & Role Domain Contract Freeze）。

docs/m2-agent-work-runtime-design.md 的代码化。**本模块是纯契约层**——不碰 Session、
不建表、不发事件、不改任何业务行为。

最高原则（设计 §1）：

    System provides facts.   Agent makes decisions.
    System validates.        System executes.        System records.

本模块把这句话变成**可执行、可测试**的边界：

| 契约 | 内容 | owner 阶段 |
| --- | --- | --- |
| 决策边界 | `SYSTEM_FACTS` / `AGENT_DECISIONS`（互斥） | M2.0 |
| 职责面与路由建议 | `ResponsibilityArea` + `DEFAULT_DECISION_AUTHORITY` | M2.0 |
| Position 三段式 | `PositionContract` = Responsibility + Authority + Expectations | M2.2 |
| 硬/软约束 | `HARD_CONSTRAINTS` / `SOFT_CONSTRAINTS` | M2.0 |
| 履职上下文 | `RoleContext` / `RoleResource`（派生读模型，不落表） | M2.2 |
| 自适应上岗 | `ROLE_ONBOARDING_PATH` / `FORBIDDEN_ONBOARDING_ACTIONS` | M2.2 |
| 记忆平面 | `MEMORY_PLANE_SURFACES`（制度 vs 个人，逐表声明） | M2.0 |
| 决策意图 | `DecisionIntent` / `validate_decision_intent()` | M2.4 |
| 评审归属 | `ReviewVerdict` + `verdict_boundaries()` | M2.7 |
| 工作根 | `ProjectWorkMode` / `CANONICAL_PROJECT_FIELDS` | M2.1 |
| DAG 正确性 | `validate_task_graph()` / `resolve_ready_tasks()` | M2.5 |
| 不变量 | `INVARIANTS`（W1–W31） | M2.0–M2.10 |

纪律：

1. **枚举的唯一家是 `app/models/enums.py`**（模型与迁移要 import 它们）；本模块按 T2 契约层的
   先例做 re-export，保证 `app.work.contracts` 的导入路径稳定。
2. 这里**不允许**出现任何"替 Agent 做决定"的函数 —— 没有 `pick_best_engineer`、
   没有 `auto_decompose`、没有 `should_rework`、没有 `evaluate_decision_quality`。
   由 `tests/test_m2_contract.py` 用 AST + 命名守卫钉住。
3. 改动本模块 = 改 M2 契约：必须走设计文档评审，并同步 `tests/test_m2_contract.py`。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from app.models.enums import (
    AuthorityKind,
    AuthorityScopeKind,
    AutonomyLevel,
    DecisionKind,
    DecisionSemantics,
    DecisionStatus,
    FactKind,
    MemoryPlane,
    PlanningFixture,
    ProjectWorkMode,
    ResponsibilityArea,
    ResponsibilityKind,
    ReviewVerdict,
    RoleResourceKind,
    ToolSideEffect,
    ToolTransport,
)

__all__ = [
    "WorkContractError",
    "FactKind",
    "DecisionKind",
    "DecisionStatus",
    "DecisionSemantics",
    "ReviewVerdict",
    "ProjectWorkMode",
    "PlanningFixture",
    "ResponsibilityKind",
    "RoleResourceKind",
    "MemoryPlane",
    "ResponsibilityArea",
    "SYSTEM_FACTS",
    "AGENT_DECISIONS",
    "DEFAULT_DECISION_AUTHORITY",
    "WORK_INTAKE_DEFAULT_POSITION",
    "AuthorityKind",
    "AuthorityScopeKind",
    "AutonomyLevel",
    "ToolSideEffect",
    "ToolTransport",
    "AUTONOMY_BY_SIDE_EFFECT",
    "FORBIDDEN_TOOL_ARGUMENT_KEYS",
    "HIGH_IMPACT_AUTHORITIES_RESERVED",
    "AuthorityGrant",
    "AMOUNT_BEARING_AUTHORITIES",
    "SELF_TARGET_FORBIDDEN_AUTHORITIES",
    "FORBIDDEN_AUTHORITY_SOURCES",
    "AUTHORITY_SNAPSHOT_VERSION",
    "AuthorityTarget",
    "AuthorityDecision",
    "authority_snapshot",
    "hash_effective_grants",
    "PositionExpectation",
    "PositionExpectationRef",
    "PositionContract",
    "HARD_CONSTRAINTS",
    "SOFT_CONSTRAINTS",
    "ConstraintName",
    "RoleResource",
    "RoleContext",
    "ROLE_CONTEXT_SOURCES",
    "RoleOnboardingStep",
    "ROLE_ONBOARDING_PATH",
    "FORBIDDEN_ONBOARDING_ACTIONS",
    "MemorySurface",
    "MEMORY_PLANE_SURFACES",
    "SPLIT_MEMORY_SURFACES",
    "DecisionAction",
    "DecisionIntent",
    "validate_decision_intent",
    "DECISION_CONTEXT_VERSION",
    "DECISION_CONTEXT_KEYS",
    "VerdictBoundary",
    "verdict_boundaries",
    "CANONICAL_PROJECT_FIELDS",
    "PROJECT_FIELD_SOURCES",
    "PROJECT_SPEC_VERSION",
    "PROJECT_SPEC_QUESTIONS",
    "SPEC_OPTIONAL_FIELDS",
    "canonical_spec_gaps",
    "RESPONSIBILITY_DEFAULTS",
    "RESPONSIBILITY_SETTINGS_KEY",
    "WORK_MODE_SETTINGS_KEY",
    "WORK_MODE_BY_COMPANY_STAGE",
    "WORK_MODE_AFTER_ONBOARDING",
    "default_work_mode_for_stage",
    "PLANNING_FIXTURE_SETTING",
    "FORBIDDEN_IMPLICIT_PLANNING_SOURCES",
    "TaskGraphNode",
    "TaskGraphReport",
    "validate_task_graph",
    "resolve_ready_tasks",
    "REQUIRES_MANAGEMENT_DECISION",
    "SYSTEM_BLOCKING_REASONS",
    "DISPATCH_BLOCK_REASONS",
    "DISPATCHABILITY_CONDITIONS",
    "DECISION_NEEDED_EVENTS",
    "FACT_EVENTS",
    "DagValidationError",
    "Invariant",
    "INVARIANTS",
]


class WorkContractError(ValueError):
    """工作域契约被违反（结构非法 / 边界被越过）。**只表达结构问题，不表达判断。**"""


# ---------------------------------------------------------------------------
# 1. 决策边界：System provides facts. Agent makes decisions.
# ---------------------------------------------------------------------------

#: 系统必须能回答的**事实**（设计 §1.1）。每一条都是"是什么"，不是"该怎么办"。
SYSTEM_FACTS: frozenset[FactKind] = frozenset(FactKind)

#: 只能由**已授权 Agent/User actor** 做出的**决策**（设计 §1.2）。
AGENT_DECISIONS: frozenset[DecisionKind] = frozenset(DecisionKind)

#: 决策面的**默认**归属（soft，供路由与 UI 分组；**不是门禁** —— W5 / W12）。
#:
#: 一条决策可以同时属于多个职责面（例如 `recruit` 既属 people 也属 strategic），
#: 因此值是 frozenset。系统用它决定"先通知谁"，不决定"谁准做"（那由 Authority 判定）。
_STRATEGIC = ResponsibilityArea.strategic
_DELIVERY = ResponsibilityArea.delivery
_QUALITY = ResponsibilityArea.quality
_PEOPLE = ResponsibilityArea.people
_EXECUTION = ResponsibilityArea.execution

DEFAULT_DECISION_AUTHORITY: dict[DecisionKind, frozenset[ResponsibilityArea]] = {
    # 战略与经营
    DecisionKind.accept_project: frozenset({_STRATEGIC, _DELIVERY}),
    DecisionKind.decline_project: frozenset({_STRATEGIC, _DELIVERY}),
    DecisionKind.delegate_management: frozenset({_STRATEGIC}),
    DecisionKind.replan: frozenset({_DELIVERY, _STRATEGIC}),
    DecisionKind.accept_delivery: frozenset({_STRATEGIC, _QUALITY}),
    DecisionKind.offboard: frozenset({_STRATEGIC, _PEOPLE}),
    # 交付与技术组织
    DecisionKind.decompose_project: frozenset({_DELIVERY}),
    DecisionKind.assign_task: frozenset({_DELIVERY}),
    DecisionKind.reassign_task: frozenset({_DELIVERY}),
    DecisionKind.create_dependency: frozenset({_DELIVERY}),
    DecisionKind.mark_blocked: frozenset({_EXECUTION, _DELIVERY}),
    DecisionKind.request_review: frozenset({_DELIVERY, _EXECUTION}),
    # 质量
    DecisionKind.request_rework: frozenset({_QUALITY, _DELIVERY}),
    # 人员
    DecisionKind.recruit: frozenset({_PEOPLE, _STRATEGIC}),
    DecisionKind.purchase_agent: frozenset({_PEOPLE, _STRATEGIC}),
    DecisionKind.assign_position: frozenset({_PEOPLE, _STRATEGIC}),
    DecisionKind.release_position: frozenset({_PEOPLE, _STRATEGIC}),
    DecisionKind.enroll_learning: frozenset({_PEOPLE, _EXECUTION}),
}

#: 默认的 **Work Intake 路由目标职位 code**（M2-ADR-9：这是公司的**可配置规则**，
#: 不是系统硬编码 —— 公司可在 `Company.settings["work_routing"]` 覆盖）。
WORK_INTAKE_DEFAULT_POSITION = "ceo"


# ---------------------------------------------------------------------------
# 2. Position = Responsibility + Authority + Expectations（设计 §3）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuthorityGrant:
    """一项被授予的权限（Authority Projection 的元素）。

    **字段与 `position_authority_grants` 一一对应**（M2.2 / v41）—— 「契约即表」，
    不在这里加表里没有的字段，避免出现第二套授权真相。

    额度语义（`max_amount`）：`None` = **该授权没有声明额度上限**。
    对金额类授权（`spend_credits`）而言这等价于"无法确认在授权内" ⇒ **拒绝**
    （default-deny）。想表达"不限"就给一个大数，而不是留空。
    """

    kind: AuthorityKind
    scope_kind: AuthorityScopeKind = AuthorityScopeKind.company
    #: 部门作用域 = departments.id；company / direct_reports 恒为 0（哨兵，不是 NULL）
    scope_ref: int = 0
    max_amount: int | None = None
    #: 应用这一步时命中的 grant 行 id（审计：这一分钱/这次动作是哪一条授权批的）
    grant_id: int | None = None

    def __post_init__(self) -> None:
        if self.max_amount is not None:
            if isinstance(self.max_amount, bool) or not isinstance(self.max_amount, int):
                raise WorkContractError("authority max_amount must be an int (minor units)")
            if self.max_amount <= 0:
                raise WorkContractError(f"authority max_amount must be > 0, got {self.max_amount}")
        if isinstance(self.scope_ref, bool) or not isinstance(self.scope_ref, int):
            raise WorkContractError("authority scope_ref must be an int (0 = no specific ref)")
        if self.scope_ref < 0:
            raise WorkContractError("authority scope_ref must be >= 0")
        if self.scope_kind is AuthorityScopeKind.department and self.scope_ref <= 0:
            raise WorkContractError("department scope requires a positive scope_ref")
        if self.scope_kind is not AuthorityScopeKind.department and self.scope_ref != 0:
            raise WorkContractError(
                f"{self.scope_kind.value} scope must use scope_ref=0 (nothing to point at)"
            )
        if self.max_amount is not None and self.kind is not AuthorityKind.spend_credits:
            raise WorkContractError(
                f"{self.kind.value} is not an amount-bearing authority; "
                "max_amount must stay None (额度只对金额类授权有意义)"
            )


#: 金额类授权：校验时必须**同时**给出 action 的金额，否则拒绝（default-deny）。
AMOUNT_BEARING_AUTHORITIES: frozenset[AuthorityKind] = frozenset({AuthorityKind.spend_credits})

#: 禁止作用于自己的授权（硬安全约束，不是管理判断）：
#: 「谁能辞退/解任谁」是管理决策，但"能对自己执行"在任何公司里都不成立 ——
#: 所以系统在这里拒绝，而不是替管理层判断该不该。
SELF_TARGET_FORBIDDEN_AUTHORITIES: frozenset[AuthorityKind] = frozenset(
    {AuthorityKind.offboard, AuthorityKind.release_position}
)

#: 授权校验**绝不允许**读取的来源（用户拍板 + 设计 §4.1）。
#: 这些是"用角色名/推荐分数当权限"的典型形态，全部由 AST 守卫钉死。
FORBIDDEN_AUTHORITY_SOURCES: frozenset[str] = frozenset(
    {
        "employee_role_string",
        "legacy_role",
        "fit_score",
        "competency_score",
        "access_package",
        "responsibility_area",
        "work_mode",
    }
)

#: `ToolSideEffect` → 当前**行为**的自主等级（M2.3 冻结的 Authority/Autonomy 边界）。
#:
#: 这张表是"现在到底会不会在无人确认下执行"的**唯一**声明处；它不实现策略引擎，
#: 只把边界钉下来。`requires_confirmation` 在 M2.3 **没有确认通道**，
#: 因此执行面会**拒绝**执行该类动作 —— 这条比"先放行、以后再补确认"安全得多。
AUTONOMY_BY_SIDE_EFFECT: dict[ToolSideEffect, AutonomyLevel] = {
    ToolSideEffect.read: AutonomyLevel.auto_allowed,
    # M2.3 的写工具都在授权内可自主执行（正是"让管理 Agent 自己组织工作"这一步）；
    # 一旦某项写动作升级为 high_impact，就必须先建确认通道再打开。
    ToolSideEffect.write: AutonomyLevel.auto_allowed,
    ToolSideEffect.high_impact: AutonomyLevel.requires_confirmation,
}

#: **M2.3 刻意不实现**的高影响授权（用户拍板：不为完整列表写空业务）。
#: 它们只作为"下一阶段放这里"的显式清单存在；注册表里不允许出现这类工具。
HIGH_IMPACT_AUTHORITIES_RESERVED: frozenset[AuthorityKind] = frozenset(
    {
        AuthorityKind.spend_credits,
        AuthorityKind.approve_hiring,
        AuthorityKind.assign_position,
        AuthorityKind.release_position,
        AuthorityKind.offboard,
        AuthorityKind.accept_delivery,
        AuthorityKind.create_project,
    }
)

#: **不允许**出现在任何工具参数里的键（T5）。
#:
#: 身份只能由 Runtime Session / WorkSession / 系统执行上下文注入；
#: 一旦允许模型在参数里写 `actor_employee_id`，"谁做的这个决定"就变成提示词里的一句话，
#: 审计与权限双双失效。执行面见到这些键一律拒绝，不做"忽略并使用上下文"的仁慈处理 ——
#: 仁慈会让调用方以为参数生效了。
FORBIDDEN_TOOL_ARGUMENT_KEYS: frozenset[str] = frozenset(
    {
        "actor_employee_id",
        "actor_person_id",
        "acting_employee_id",
        "acting_person_id",
        "acting_position_definition_id",
        "acting_position_assignment_id",
        "actor_company_id",
        "company_id",
        "granted_by_user_id",
    }
)

#: 授权快照版本：DecisionRecord 用它 + `grants_hash` 解释"当时为什么有权"（W40）。
AUTHORITY_SNAPSHOT_VERSION = 1


@dataclass(frozen=True)
class AuthorityTarget:
    """一次管理动作**作用到谁 / 多少**（校验的输入）。

    刻意只是一组事实：`company_id` / `department_id` / `employee_id` / `amount`。
    系统拿它回答「在不在授权范围内」；**不**拿它回答「该不该做这件事」——
    后者永远是 Manager Agent 或 Owner 的判断（W39）。
    """

    company_id: int | None = None
    department_id: int | None = None
    employee_id: int | None = None
    amount: int | None = None


@dataclass(frozen=True)
class AuthorityDecision:
    """授权校验的**结果**（W39：只回答"在不在授权内"）。

    `reason` 是一台**机器可读的**拒绝原因码（`no_active_assignment` / `no_grant` /
    `scope_mismatch` / `amount_required` / `amount_exceeds_grant` / `self_target` …），
    不是"我觉得这个决定不好"。系统永远不产生后一种东西。
    """

    allowed: bool
    kind: AuthorityKind
    reason: str
    actor_employee_id: int
    position_definition_id: int | None = None
    #: 被**考察**的授权（按 kind 命中的那些；一个都没命中时=该职位全部授权）。
    #: 目的只有一个：让"当时凭什么"可以从 `grant_ids` 重算对拍。
    grants: tuple[AuthorityGrant, ...] = ()
    evaluated_at: datetime | None = None
    #: `grants` 的摘要（可以从 `grant_ids` 重算 ⇒ 这就是可校验的授权证据）
    grants_hash: str = ""
    #: 该职位在那一刻**持有**的全部授权摘要（岗位状态而非某次动作）。
    #: 两个摘要刻意分开：一个是"这次凭什么"，一个是"当时手里有什么"，
    #: 混成一个字段会让历史解释时说不清在解释哪一层。
    position_grants_hash: str = ""

    @property
    def grant_ids(self) -> tuple[int, ...]:
        return tuple(grant.grant_id for grant in self.grants if grant.grant_id is not None)


def _canonical_grants(grants: tuple[AuthorityGrant, ...]) -> list[list[str]]:
    """授权的**规范化表示**（跨进程稳定、与时间无关 ⇒ 可重算、可对拍）。"""
    rows = [
        [
            grant.kind.value,
            grant.scope_kind.value,
            str(grant.scope_ref),
            "" if grant.max_amount is None else str(grant.max_amount),
        ]
        for grant in grants
    ]
    return sorted(rows)


def hash_effective_grants(grants: tuple[AuthorityGrant, ...]) -> str:
    """生效授权的摘要（W40）。

    `DecisionRecord` 存它 + `grant_ids`：日后有人问
    「当年 CTO 为什么有权委派这个任务」，拿着当时的 grant 行重算一次即可对拍 ——
    不需要重放整个系统的状态。
    """
    payload = json.dumps(_canonical_grants(grants), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def authority_snapshot(decision: AuthorityDecision, *, action_ref: str = "") -> dict:
    """把一次授权校验压成**可长期保存**的快照，交给 `DecisionRecord.context_snapshot`（M2.4）。

    只含事实：谁、以哪个职位的哪些授权、在什么作用域与额度内、判成了什么、何时判的。
    **不含**任何"这个动作对不对"的评语（W18/W39）。
    """
    return {
        "snapshot_version": AUTHORITY_SNAPSHOT_VERSION,
        "authority_kind": decision.kind.value,
        "allowed": bool(decision.allowed),
        "reason": decision.reason,
        "actor_employee_id": decision.actor_employee_id,
        "position_definition_id": decision.position_definition_id,
        "action_ref": action_ref,
        "grant_ids": list(decision.grant_ids),
        "grant_scopes": _canonical_grants(decision.grants),
        "grants_hash": decision.grants_hash or hash_effective_grants(decision.grants),
        "position_grants_hash": decision.position_grants_hash,
        "evaluated_at": decision.evaluated_at.isoformat() if decision.evaluated_at else None,
    }


@dataclass(frozen=True)
class PositionExpectation:
    """**Expectations**（设计 §3.4）—— `PositionCompetencyRequirement` 的只读投影。

    只声明公司**希望**什么水平；**不是任命门禁**（W4 的推论：期望不拒绝任命）。
    字段名与 `position_competency_requirements` 列一一对应（由契约测试核对），
    本类**不新增任何字段**，避免出现第二套岗位要求。
    """

    competency_code: str
    requirement_type: str = "required"  # required | preferred
    minimum_score: int | None = None
    target_score: int | None = None
    minimum_confidence: float | None = None
    critical: bool = False
    weight: float = 1.0


@dataclass(frozen=True)
class PositionExpectationRef:
    """期望的**引用**（不含分值）—— `RoleContext` 里用的就是它（C5）。

    为什么 RoleContext 只带引用、不带分值：分值属于**岗位画像**
    （`position_competency_requirements`），按需另读即可；而 RoleContext 是
    "履职上下文投影"，把它塞满数字就会变成"任命时给你一张成绩单" ——
    正是 W7 要防的东西。用户拍板 C5：「RoleContext 响应不含任何 score/level/rank，
    只含事实引用」。
    """

    competency_code: str
    requirement_type: str = "required"  # required | preferred
    critical: bool = False


@dataclass(frozen=True)
class PositionContract:
    """Position 的三段式契约（设计 §3.1，W4 / W26）。

    **不是** workflow / SOP / prompt / skill package。它只回答：

        「公司希望你负责什么，并授权你做什么。」

    至于「你具体怎么把它做好」——由 Agent 根据自身能力、人格、经验、公司知识、
    历史决策、当前任务与其他成员自主决定。
    """

    #: 通常负责什么（`position_definitions.responsibilities` 的投影）
    responsibilities: tuple[str, ...] = ()
    #: 被授权做什么（硬边界；M2.2 落 Authority Projection）
    authority: tuple[AuthorityGrant, ...] = ()
    #: 希望具备什么能力（soft；soft）
    expectations: tuple[PositionExpectationRef, ...] = ()
    #: 通常做什么工作（**advisory**：只用于路由建议与展示，W5/W12）
    advisory_scope: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# 3. Hard vs Soft Constraints（设计 §4）
# ---------------------------------------------------------------------------


class ConstraintName(StrEnum):
    """约束的**唯一命名**：两类集合必须互斥且覆盖（契约测试钉住）。"""

    # ---- Hard（系统强制，可拒绝）----
    security = "security"
    permission = "permission"
    company_isolation = "company_isolation"
    economic_authority = "economic_authority"
    runtime_capability = "runtime_capability"
    resource_availability = "resource_availability"
    task_lifecycle = "task_lifecycle"
    concurrency_invariant = "concurrency_invariant"
    database_invariant = "database_invariant"
    # ---- Soft（系统只报告，不拒绝）----
    position_scope = "position_scope"
    competency_expectation = "competency_expectation"
    fit_score = "fit_score"
    experience_match = "experience_match"
    specialization = "specialization"
    work_habit = "work_habit"
    advisory_load = "advisory_load"


#: 系统**强制并可拒绝**的约束（设计 §4.1）。
HARD_CONSTRAINTS: frozenset[ConstraintName] = frozenset(
    {
        ConstraintName.security,
        ConstraintName.permission,
        ConstraintName.company_isolation,
        ConstraintName.economic_authority,
        ConstraintName.runtime_capability,
        ConstraintName.resource_availability,
        ConstraintName.task_lifecycle,
        ConstraintName.concurrency_invariant,
        ConstraintName.database_invariant,
    }
)

#: 系统**只报告、不拒绝**的约束（设计 §4.2）。
#:
#: Backend Engineer 被分配 Research Task，即使 Fit 很低，系统也**不能**拒绝 ——
#: 它可以回答"Fit 42% / research experience low"，但决定权在 Manager Agent。
SOFT_CONSTRAINTS: frozenset[ConstraintName] = frozenset(
    {
        ConstraintName.position_scope,
        ConstraintName.competency_expectation,
        ConstraintName.fit_score,
        ConstraintName.experience_match,
        ConstraintName.specialization,
        ConstraintName.work_habit,
        ConstraintName.advisory_load,
    }
)


# ---------------------------------------------------------------------------
# 4. RoleContext + Role Resource Index（设计 §5 / §6）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RoleResource:
    """Role Resource Index 的一项：**建议** Agent 去读/学（设计 §6）。

    表达的是「建议你学习 / 使用这些资源」，
    **不是**「任命之后你自动拥有这些能力」。

    刻意**不携带任何数值**（score / level / weight）—— 「资源」一旦带分值就会演化成
    「注入」。由 `tests/test_m2_contract.py` 做**字段扫描**钉死。
    """

    kind: RoleResourceKind
    ref: str  # drive path / knowledge topic / handbook key / policy key
    note: str = ""
    #: 即使 required 也只表示"建议优先阅读"，不表示"读完后获得能力"。
    required: bool = False

    def __post_init__(self) -> None:
        if not self.ref.strip():
            raise WorkContractError("role resource ref must not be empty")


@dataclass(frozen=True)
class RoleContext:
    """履职上下文（设计 §5）—— **派生读模型 / Context Projection**。

    M2-ADR-4：**不落表**。理由与 P4d ADR-2/ADR-4 同源 —— 派生态一旦落库必然与
    任职时间轴漂移；`WorkforceStatusResolver` 已经证明"读时派生"是可行且更诚实的口径。

    字段纪律：每个字段都能从**现有表**推导（`ROLE_CONTEXT_SOURCES` 是机器可核对的映射）。
    这里**不含**任何分数、评级、"你应该怎么做"、或写能力。
    """

    #: 人级口径（R1）；legacy 行可能还没有 person_id，那是 NULL 而不是 0 —— 不编一个号
    person_id: int | None
    employee_id: int
    position_definition_id: int | None = None
    position_code: str | None = None
    department_id: int | None = None
    responsibilities: tuple[str, ...] = ()
    authority: tuple[AuthorityGrant, ...] = ()
    expectations: tuple[PositionExpectation, ...] = ()
    advisory_scope: tuple[str, ...] = ()
    resource_index: tuple[RoleResource, ...] = ()
    direct_reports: tuple[int, ...] = ()  # employee ids
    company_policy_keys: tuple[str, ...] = ()
    current_project_ids: tuple[int, ...] = ()
    knowledge_scopes: tuple[str, ...] = ()


#: RoleContext 字段 → 推导来源。**机器可核对**：测试会检查
#:   (a) 每个字段都被登记；
#:   (b) 来源里写出的 `table.column` 真实存在（`(M2.x)` 标记的规划项跳过）。
ROLE_CONTEXT_SOURCES: dict[str, str] = {
    "person_id": "persons.id",
    "employee_id": "employees.id",
    "position_definition_id": "position_definitions.id",
    "position_code": "position_definitions.code",
    "department_id": "position_slots.department_id",
    "responsibilities": "position_definitions.responsibilities",
    "authority": "position_authority_grants",
    "expectations": "position_competency_requirements + position_profile_versions.status",
    "advisory_scope": "position_definitions.advisory_scope",
    "resource_index": "position_definition_resources",
    "direct_reports": "position_slots.manager_slot_id",
    "company_policy_keys": "companies.settings",
    "current_project_ids": "projects.status",
    "knowledge_scopes": "knowledge_items.scope",
}


# ---------------------------------------------------------------------------
# 5. Adaptive Role Onboarding（设计 §8）
# ---------------------------------------------------------------------------


class RoleOnboardingStep(StrEnum):
    """上任后的**自适应**上岗路径（Agent 自己走，系统只提供能力）。"""

    read_role_context = "read_role_context"
    inspect_expectations = "inspect_expectations"
    inspect_own_competencies = "inspect_own_competencies"
    find_gaps = "find_gaps"
    search_company_knowledge = "search_company_knowledge"
    create_learning_priorities = "create_learning_priorities"
    learn = "learn"
    work = "work"


#: 路径是**契约顺序**，不是系统流程 —— 系统不驱动它，Agent 自己决定怎么走。
ROLE_ONBOARDING_PATH: tuple[RoleOnboardingStep, ...] = (
    RoleOnboardingStep.read_role_context,
    RoleOnboardingStep.inspect_expectations,
    RoleOnboardingStep.inspect_own_competencies,
    RoleOnboardingStep.find_gaps,
    RoleOnboardingStep.search_company_knowledge,
    RoleOnboardingStep.create_learning_priorities,
    RoleOnboardingStep.learn,
    RoleOnboardingStep.work,
)

#: 被**明确禁止**的上岗动作（设计 §8 / 不变量 W4 / W7 / W8）。
#:
#: `tests/test_m2_contract.py` 扫描 `app/` 全仓：**没有任何函数可以叫这些名字**。
#: 这条守卫防的是"某天有人觉得注入一下更方便"。
FORBIDDEN_ONBOARDING_ACTIONS: frozenset[str] = frozenset(
    {
        "inject_role_skills",
        "inject_role_knowledge",
        "inject_role_competency",
        "grant_role_competency",
        "grant_role_skills",
        "copy_personal_assets_from_previous_holder",
        "inherit_predecessor_skills",
        "inherit_predecessor_knowledge",
        "inherit_predecessor_memory",
        "seed_role_competency_scores",
    }
)


# ---------------------------------------------------------------------------
# 6. Institutional vs Personal Memory（设计 §7，W9 / W10）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MemorySurface:
    """一张承载记忆的表 + 它属于哪个平面。

    `person_scoped` 声明该表是否带 **person 口径列**；契约测试会**核对模型**，
    所以它不是注释而是断言（有人删了 `person_id` 会转红）。
    """

    table: str
    plane: MemoryPlane
    person_scoped: bool = False
    note: str = ""


_MEMORY_SURFACES: tuple[MemorySurface, ...] = (
    # ---- Institutional：随公司存续 ----
    MemorySurface("companies", MemoryPlane.institutional, note="政策与制度（settings）"),
    MemorySurface("departments", MemoryPlane.institutional),
    MemorySurface("projects", MemoryPlane.institutional, note="项目史"),
    MemorySurface("project_requirements", MemoryPlane.institutional),
    MemorySurface("project_phases", MemoryPlane.institutional),
    MemorySurface("baselines", MemoryPlane.institutional),
    MemorySurface("change_requests", MemoryPlane.institutional),
    MemorySurface("review_meetings", MemoryPlane.institutional),
    MemorySurface("delivery_packages", MemoryPlane.institutional),
    MemorySurface("document_artifacts", MemoryPlane.institutional),
    MemorySurface("drive_nodes", MemoryPlane.institutional, note="Playbook/Handbook/Artifact 载体"),
    MemorySurface("drive_revisions", MemoryPlane.institutional),
    MemorySurface("work_orders", MemoryPlane.institutional, note="商业记录"),
    MemorySurface("work_order_submissions", MemoryPlane.institutional),
    MemorySurface("contracts", MemoryPlane.institutional),
    MemorySurface("offers", MemoryPlane.institutional),
    MemorySurface("escrows", MemoryPlane.institutional),
    MemorySurface("evaluations", MemoryPlane.institutional),
    MemorySurface("ledger_transactions", MemoryPlane.institutional),
    MemorySurface("events", MemoryPlane.institutional),
    MemorySurface("audit_logs", MemoryPlane.institutional),
    # ---- Personal：随 Person 存续 ----
    MemorySurface("persons", MemoryPlane.personal, note="身份根（本人）"),
    MemorySurface("character_profiles", MemoryPlane.personal, person_scoped=True),
    MemorySurface("employee_brains", MemoryPlane.personal, person_scoped=True, note="人格 traits"),
    MemorySurface("memory_entries", MemoryPlane.personal, person_scoped=True),
    MemorySurface("learning_records", MemoryPlane.personal, person_scoped=True),
    MemorySurface("learning_priorities", MemoryPlane.personal, person_scoped=True),
    MemorySurface("learning_sessions", MemoryPlane.personal, person_scoped=True),
    MemorySurface("skills", MemoryPlane.personal, person_scoped=True),
    MemorySurface("skill_usages", MemoryPlane.personal, person_scoped=True),
    MemorySurface("competency_evidence", MemoryPlane.personal, person_scoped=True),
    MemorySurface("employee_competencies", MemoryPlane.personal, person_scoped=True),
    MemorySurface("assessment_runs", MemoryPlane.personal, person_scoped=True),
    MemorySurface("assessment_results", MemoryPlane.personal, note="经 assessment_runs 关联"),
    MemorySurface("career_events", MemoryPlane.personal, note="工作履历（employee 口径历史）"),
    MemorySurface("education_events", MemoryPlane.personal, person_scoped=True),
    MemorySurface("training_programs", MemoryPlane.personal, person_scoped=True),
    MemorySurface("development_plans", MemoryPlane.personal, note="发展计划（employee 口径历史）"),
    MemorySurface("development_plan_items", MemoryPlane.personal, note="经 plan 关联"),
)

#: 表级切开、需要两边都声明的载体（同一张表同时承载两个平面）。
#:
#: `knowledge_items` 是唯一形态：`scope=private` 属个人（候选人不属于任何公司的私有知识
#: 也在这里），`scope=department|company` 属制度。审计已明确这套 scope 分层是 K1 的地基，
#: M2 不去改动它，只在契约里把**两个平面**都声明清楚。
SPLIT_MEMORY_SURFACES: dict[str, tuple[MemorySurface, ...]] = {
    "knowledge_items": (
        MemorySurface(
            "knowledge_items", MemoryPlane.institutional, note="scope=company|department"
        ),
        MemorySurface(
            "knowledge_items",
            MemoryPlane.personal,
            person_scoped=True,
            note="scope=private（owner_person_id）",
        ),
    ),
}

MEMORY_PLANE_SURFACES: tuple[MemorySurface, ...] = _MEMORY_SURFACES


# ---------------------------------------------------------------------------
# 7. DecisionRecord（设计 §10，W3 / W15 / W28）
# ---------------------------------------------------------------------------

#: 决策上下文的版本 + **有界键集**（DR9）。
#:
#: 决策上下文是"当时基于什么事实做的决定"的**有界快照 + 稳定引用**，
#: **不是**公司数据库的副本。键集封闭：加字段要走这里的评审，
#: 而不是让某个 handler 顺手把整张表塞进 JSON。
DECISION_CONTEXT_VERSION = 1
DECISION_CONTEXT_KEYS: frozenset[str] = frozenset(
    {
        "project_id",
        "task_ids",
        "candidate_employee_ids",
        "requirement_codes",
        "deliverable_count",
        "open_task_count",
        "load_summary",
        "knowledge_refs",
        "artifact_refs",
        "authority",  # `authority_snapshot()` 的双摘要（凭什么 + 当时手里有什么）
        "note",  # Agent 自己补的一句事实说明（**事实**，不是评语）
    }
)


#: `DecisionRecord.scope` 允许的载体前缀（"kind:id"）。
DECISION_SCOPE_KINDS: frozenset[str] = frozenset(
    {"company", "project", "task", "person", "employee", "position", "listing", "workorder"}
)


@dataclass(frozen=True)
class DecisionAction:
    """决策落成一个**动作**（经 M2.3 的 write tool 应用）。"""

    tool: str
    args: Mapping[str, Any] = field(default_factory=dict)
    result_ref: str = ""  # 应用后产生/影响的实体引用，如 "task:34"


@dataclass(frozen=True)
class DecisionIntent:
    """**决策意图**（Decision Envelope 的可校验形状，设计 §14c）。

    M2.4 起它是**提交前的意图**，不再是持久化实体 —— 落库的是
    `app/models/decision.py::DecisionRecord`（管理语义）与 `ToolAudit`（执行事实），
    两层不混（DR1/DR5）。

    这里只冻结字段契约与**结构**校验。系统**不评价**决策内容 ——
    它只记录「做了什么决定、依据是什么、结果是什么」，由真实结果形成 Evidence。
    (`validate_decision_intent` 甚至会接受 `reason="I felt like it"` —— 这是特性，不是疏漏。)
    """

    actor_person_id: int
    acting_employee_id: int
    decision: DecisionKind
    scope: str
    reason: str
    #: Agent 自述"我希望达成什么"（**意图**，不是判据；系统不核验它是否达成）
    intended_outcome: str = ""
    context_snapshot: Mapping[str, Any] = field(default_factory=dict)
    #: 一次决策落成的 N 个动作 —— **tool call ≠ decision**（DR2）
    actions: tuple[DecisionAction, ...] = ()
    acting_position_definition_id: int | None = None
    #: 决策树（CEO → CTO → Team Lead）：只用一个自引用，不建第二套 workflow 模型（DR8）
    parent_decision_id: int | None = None
    created_at: datetime | None = None


def _positive_id(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise WorkContractError(f"{field_name} must be a positive int id")
    return value


def validate_decision_intent(record: DecisionIntent) -> DecisionIntent:
    """校验 **结构**（不是内容）。W18：系统不做管理判断。

    只检查：actor 身份齐全、决策类型合法、scope 形式正确、理由**已被陈述**（非空）、
    上下文是映射、动作是已知工具名形状、父决策 id 合法。

    **刻意不做**：评价 reason 是否合理、判断 intended_outcome 是否现实、
    推断 outcome 好坏、检查 context 是否"足够"。
    """
    _positive_id(record.actor_person_id, "actor_person_id")
    _positive_id(record.acting_employee_id, "acting_employee_id")
    if record.acting_position_definition_id is not None:
        _positive_id(record.acting_position_definition_id, "acting_position_definition_id")
    if not isinstance(record.decision, DecisionKind):
        raise WorkContractError(f"unknown decision kind: {record.decision!r}")
    scope = record.scope.strip()
    if ":" not in scope:
        raise WorkContractError("scope must be 'kind:id' (e.g. 'project:12')")
    kind, _, raw_id = scope.partition(":")
    if kind not in DECISION_SCOPE_KINDS:
        raise WorkContractError(f"unknown scope kind: {kind!r}")
    _positive_id(int(raw_id) if raw_id.isdigit() else raw_id, "scope id")
    if not record.reason.strip():
        raise WorkContractError("a management decision must state a reason (content is not judged)")
    if not isinstance(record.context_snapshot, Mapping):
        raise WorkContractError("context_snapshot must be a mapping")
    if record.parent_decision_id is not None:
        _positive_id(record.parent_decision_id, "parent_decision_id")
    for action in record.actions:
        if not isinstance(action, DecisionAction) or not action.tool.strip():
            raise WorkContractError("each action must name a tool")
    return record


# ---------------------------------------------------------------------------
# 8. 评审归属：四个面不得互相替代（设计 §12.1，W17 / W29）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VerdictBoundary:
    """一个"评价面"的归属声明。四者**对象不同、判定者不同**，不得互相替代。"""

    surface: str
    enum_name: str
    values: frozenset[str]
    decider: str
    object_of: str  # 判定对象
    effect: str  # 判定的作用
    module_prefix: str  # 该面唯一允许被谁引用（模块前缀，用于交叉引用守卫）


def verdict_boundaries() -> tuple[VerdictBoundary, ...]:
    """四个 verdict/assessment 面的边界表（W29）。

    审计已指出系统里存在 4 套"评价"，M2 把它变成**显式契约**而不是隐含风险：

    | 面 | 枚举 | 判定者 | 对象 | 作用 |
    | --- | --- | --- | --- | --- |
    | 任务级技术评审 | `ReviewVerdict` | Reviewer Agent | Task 产出 | 决定 Task 下一步 |
    | 交付阶段门 | `ReviewDecision` | 人类 | 阶段产出 | Baseline / 阶段推进 |
    | 商业验收 | `EvaluationVerdict` | 管理面 / 确定性规则 | WorkOrder 提交 | 决定结算 |
    | 能力考核 | `AssessmentResult`（数值） | 统计聚合 | 人的能力 | 只影响能力，不是 gate |
    """
    return (
        VerdictBoundary(
            surface="task_review",
            enum_name="ReviewVerdict",
            values=frozenset(v.value for v in ReviewVerdict),
            decider="reviewer_agent",
            object_of="task_output",
            effect="advance_task_status",
            module_prefix="app.work",
        ),
        VerdictBoundary(
            surface="phase_gate",
            enum_name="ReviewDecision",
            values=frozenset(
                {"approved", "conditionally_approved", "changes_requested", "rejected"}
            ),
            decider="human_user",
            object_of="phase_output",
            effect="baseline_and_phase_advance",
            module_prefix="app.services.project_delivery",
        ),
        VerdictBoundary(
            surface="commercial_acceptance",
            enum_name="EvaluationVerdict",
            values=frozenset({"approved", "rejected", "revise"}),
            decider="system_admin_or_deterministic_rule",
            object_of="work_order_submission",
            effect="settlement",
            module_prefix="app.services.economy",
        ),
        VerdictBoundary(
            surface="competency_assessment",
            enum_name="AssessmentResult",
            values=frozenset({"numeric"}),
            decider="statistical_aggregation",
            object_of="person_competency",
            effect="competency_only_not_a_gate",
            module_prefix="app.services",
        ),
    )


# ---------------------------------------------------------------------------
# 9. 工作根：Canonical Executable Project（设计 §11，W20 / W22 / W23 / W30）
# ---------------------------------------------------------------------------

#: Canonical Project Spec 的必需字段（**Facts / Requirements**，不是 Execution Plan）。
CANONICAL_PROJECT_FIELDS: tuple[str, ...] = (
    "background",
    "goal",
    "requirements",
    "constraints",
    "deliverables",
    "acceptance_criteria",
    "priority",
    "deadline",
    "context",
)

#: 契约字段 → 现有承载（M2.0 冻结，M2.1 落地）。
#: 结论：**全部有现有承载，M2 不需要新表**。
PROJECT_FIELD_SOURCES: dict[str, str] = {
    "background": "projects.background",
    "goal": "projects.goal",
    "requirements": "project_requirements (+ projects.source_order_text 兜底)",
    "constraints": "projects.constraints",
    "deliverables": "projects.deliverables",
    "acceptance_criteria": "project_requirements.acceptance_criteria",
    "priority": "projects.priority",
    "deadline": "projects.planned_end_at",
    "context": "projects.description",
}

#: Canonical Spec 的版本号（M2.1 起落在 `projects.spec_version`）。
#: 语义变更（新增必需字段/改变字段含义）必须 bump —— 这样"我使用哪个 spec 版本"可回答。
PROJECT_SPEC_VERSION = 1

#: Project 必须能回答的 8 个问题（用户拍板清单）→ 由哪里回答。
#: 这是"可回答性"的机器可核对清单：契约测试断言每一个 key 都能在
#: `GET /projects/{id}/spec` 的响应里找到。
PROJECT_SPEC_QUESTIONS: dict[str, str] = {
    "canonical_spec": "spec + completeness",
    "work_mode": "work_mode",
    "work_intake_responsibility": "work_intake.responsibility",
    "work_intake_assignment": "work_intake.assignment",
    "management_actor": "management",
    "requirements_deliverables_acceptance": (
        "spec.requirements / spec.deliverables / spec.acceptance_criteria"
    ),
    "spec_version": "spec_version",
    "execution_entered": "execution",
}

#: Spec 的"缺失"判定：字段为空/空列表即缺失。**只有 `context` 允许为空**（一句话立项）。
#: 其余字段缺失只影响完整性报告，**不影响项目创建**（不拿模板当门禁）。
SPEC_OPTIONAL_FIELDS: frozenset[str] = frozenset({"constraints", "deadline", "context"})


def canonical_spec_gaps(spec: Mapping[str, Any]) -> tuple[str, ...]:
    """返回 Canonical Spec 里"未被陈述"的字段（只报告，不拒绝 —— W5 的同源纪律）。

    刻意不做：自动从 `background` 抽出 goal、从 description 编出 acceptance criteria。
    "系统不替公司写需求"是这条函数存在的理由。
    """
    missing: list[str] = []
    for field_name in CANONICAL_PROJECT_FIELDS:
        value = spec.get(field_name)
        empty = value is None or value == "" or value == [] or value == {}
        if empty and field_name not in SPEC_OPTIONAL_FIELDS:
            missing.append(field_name)
    return tuple(missing)


# ---------------------------------------------------------------------------
# 9b. 职责路由（D1 / M2-ADR-11，W32）与工作模式默认（D2，W35）
# ---------------------------------------------------------------------------

#: 责任 → 默认承载职位 code（**默认值，不是特权**）。公司可用 `Company.settings` 覆盖。
RESPONSIBILITY_DEFAULTS: dict[ResponsibilityKind, str] = {
    ResponsibilityKind.work_intake: WORK_INTAKE_DEFAULT_POSITION,
}

#: `Company.settings` 里承载职责路由的键：`{"work_routing": {"work_intake": "ceo"}}`
RESPONSIBILITY_SETTINGS_KEY = "work_routing"

#: `Company.settings` 里承载"公司默认工作模式"的键：`{"work_mode_default": {"work_mode": ...}}`
WORK_MODE_SETTINGS_KEY = "work_mode_default"

#: 公司阶段 → 默认工作模式（D2：冷启动 guided，成熟后 managed）。
#: 未知/新阶段一律按 `guided`（保守：宁可多一层人类确认，不默默自主）。
WORK_MODE_BY_COMPANY_STAGE: dict[str, ProjectWorkMode] = {
    "FOUNDING": ProjectWorkMode.guided,
    "OPERATING": ProjectWorkMode.managed,
}

#: 学习期结束后的目标默认（首次真实项目走完后由 `work_defaults` 推进）。
WORK_MODE_AFTER_ONBOARDING = ProjectWorkMode.managed


def default_work_mode_for_stage(stage: str | None) -> ProjectWorkMode:
    """公司阶段的默认工作模式（纯函数；公司显式覆盖优先于它）。"""
    return WORK_MODE_BY_COMPANY_STAGE.get((stage or "").upper(), ProjectWorkMode.guided)


#: 规划 fixture 的启用键（`Settings.allow_planning_fixtures`，默认 False）。
#: 契约层只写名字；读取由 `app.core.config` 负责（纯契约层不 import settings）。
PLANNING_FIXTURE_SETTING = "allow_planning_fixtures"

#: 生产项目**永不允许**的隐式行为（W33）：这些是"系统偷偷替公司规划"的形态。
FORBIDDEN_IMPLICIT_PLANNING_SOURCES: frozenset[str] = frozenset(
    {
        "missing_manager_fallback",
        "manager_timeout_fallback",
        "manager_failure_fallback",
        "empty_task_list_fallback",
        "company_default_fixture_in_request_path",
    }
)


# ---------------------------------------------------------------------------
# 10. Task DAG 正确性（设计 §12.2，W2 / W16）
# ---------------------------------------------------------------------------


class DagValidationError(WorkContractError):
    """DAG 结构非法（自环 / 环 / 悬空依赖 / 重复边）。"""


@dataclass(frozen=True)
class TaskGraphNode:
    """DAG 节点的最小事实（系统眼里只有 status 与依赖关系）。"""

    task_id: int
    status: str
    depends_on: tuple[int, ...] = ()


@dataclass(frozen=True)
class TaskGraphReport:
    """DAG 校验结果。**只描述结构事实，不给建议。**"""

    is_valid: bool
    self_loops: tuple[int, ...] = ()
    dangling: tuple[tuple[int, int], ...] = ()  # (task_id, missing_dep)
    duplicates: tuple[tuple[int, int], ...] = ()  # (task_id, repeated_dep)
    cycles: tuple[tuple[int, ...], ...] = ()
    ready: tuple[int, ...] = ()

    @property
    def error(self) -> str:
        if self.is_valid:
            return ""
        parts = []
        if self.self_loops:
            parts.append(f"self_loops={list(self.self_loops)}")
        if self.dangling:
            parts.append(f"dangling={[list(p) for p in self.dangling]}")
        if self.duplicates:
            parts.append(f"duplicates={[list(p) for p in self.duplicates]}")
        if self.cycles:
            parts.append(f"cycles={[list(c) for c in self.cycles]}")
        return "; ".join(parts)


#: 未开始、且可以被系统判定就绪的 Task 状态（系统回答"哪些可以执行"的前置）。
READY_CANDIDATE_STATUSES: frozenset[str] = frozenset({"backlog", "todo", "failed"})


def _adjacency(nodes: Sequence[TaskGraphNode]) -> dict[int, tuple[int, ...]]:
    return {node.task_id: tuple(node.depends_on) for node in nodes}


def _find_cycles(adjacency: Mapping[int, tuple[int, ...]]) -> tuple[tuple[int, ...], ...]:
    """迭代式 Tarjan 强连通分量；返回长度 > 1 的 SCC（+ 自环单独处理）。"""
    index_of: dict[int, int] = {}
    low: dict[int, int] = {}
    on_stack: dict[int, bool] = {}
    stack: list[int] = []
    counter = 0
    cycles: list[tuple[int, ...]] = []

    for root in sorted(adjacency):
        if root in index_of:
            continue
        work: list[tuple[int, int]] = [(root, 0)]
        while work:
            node, child_index = work.pop()
            if child_index == 0:
                index_of[node] = low[node] = counter
                counter += 1
                stack.append(node)
                on_stack[node] = True
            children = [d for d in adjacency.get(node, ()) if d in adjacency]
            if child_index < len(children):
                work.append((node, child_index + 1))
                child = children[child_index]
                if child not in index_of:
                    work.append((child, 0))
                elif on_stack.get(child):
                    low[node] = min(low[node], index_of[child])
            else:
                if low[node] == index_of[node]:
                    component: list[int] = []
                    while True:
                        member = stack.pop()
                        on_stack[member] = False
                        component.append(member)
                        if member == node:
                            break
                    if len(component) > 1:
                        cycles.append(tuple(sorted(component)))
                if work:
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[node])
    return tuple(cycles)


def validate_task_graph(nodes: Iterable[TaskGraphNode]) -> TaskGraphReport:
    """校验 Task DAG 的**结构正确性**（系统职责，W16）。

    系统回答的是「这张图是否可执行」，**不是**「下一步应该创建什么 Task」（管理职责）。

    检出：自环、悬空依赖（指向不存在的 Task）、重复边、环（含多节点环）。
    不检出（刻意）：不判断拆解是否合理、粒度是否合适、人手是否够 —— 那些是管理判断。
    """
    materialized = list(nodes)
    ids = {node.task_id for node in materialized}
    self_loops: list[int] = []
    dangling: list[tuple[int, int]] = []
    duplicates: list[tuple[int, int]] = []
    adjacency: dict[int, tuple[int, ...]] = {}

    for node in materialized:
        deps = tuple(node.depends_on)
        if node.task_id in deps:
            self_loops.append(node.task_id)
        seen: set[int] = set()
        for dep in deps:
            if dep in seen:
                duplicates.append((node.task_id, dep))
            seen.add(dep)
            if dep not in ids:
                dangling.append((node.task_id, dep))
        adjacency[node.task_id] = deps

    cycles = _find_cycles(adjacency)
    is_valid = not (self_loops or dangling or duplicates or cycles)
    return TaskGraphReport(
        is_valid=is_valid,
        self_loops=tuple(self_loops),
        dangling=tuple(dangling),
        duplicates=tuple(duplicates),
        cycles=cycles,
        ready=resolve_ready_tasks(materialized) if is_valid else (),
    )


def resolve_ready_tasks(nodes: Iterable[TaskGraphNode]) -> tuple[int, ...]:
    """系统对「哪些 Task 现在可以执行」的**唯一契约定义**（W16）。

    规则（纯函数，只看 status 与依赖终态）：

    1. 候选 = status ∈ `READY_CANDIDATE_STATUSES`（backlog / todo / failed，即未开始或待重做）；
    2. 依赖**全部**为终态成功（`done`）⇒ 就绪；
    3. 运行中 / 已完成 / 已驳回的 Task 不在就绪集合里。

    **所有权**：系统只回答就绪集合；**要不要新开 Task、派给谁由 Manager Agent 决定**
    （W2 / R2）。M2.5 起 `app/work/dispatch.py` 是它的唯一运行时调用方 ——
    编排器不再有 per-`TaskKind` 的推进逻辑。

    注意它**只**回答"结构就绪"（R3）：可派发还要看负责人、运行时、项目状态等，
    那些在 `dispatch.evaluate_dispatch()` 里，且结论只有"能派给**已存在的**负责人"
    或"需要管理决策"两种（不存在"系统自己挑一个"）。
    """
    materialized = list(nodes)
    done = {node.task_id for node in materialized if node.status == "done"}
    ready: list[int] = []
    for node in materialized:
        if node.status not in READY_CANDIDATE_STATUSES:
            continue
        if all(dep in done for dep in node.depends_on):
            ready.append(node.task_id)
    return tuple(sorted(ready))


# ---------------------------------------------------------------------------
# 10b. Ready vs Dispatchable（M2.5，R1–R12）
# ---------------------------------------------------------------------------

#: 需要**管理决策**才能继续的派发阻断原因（用户拍板 §4/§5/§6）。
#:
#: 这些**不是**"系统遇到的错误"，而是"系统按设计不能替管理层决定"的分界线：
#: 系统只报事实，决定由被授权的管理 Agent / Owner 做。因此它们各自对应一个
#: Decision-needed 事件，而不是一个自动补救动作。
REQUIRES_MANAGEMENT_DECISION: frozenset[str] = frozenset(
    {
        "assignee_missing",  # 结构就绪但没人负责 ⇒ 必须有人做指派决定（R4）
        "assignee_inactive",  # 负责人当前不可用 ⇒ 等 / 改派 / 修复，由管理层选（R5）
        "runtime_unavailable",  # 运行时/资源不可用 ⇒ 同上（R5）
        "provider_missing",  # 真实 runtime 缺模型绑定 ⇒ 同上
        "task_failed",  # 执行失败 ⇒ 重做 / 改派 / 改方案是管理决策，系统不自动重试（R10）
    }
)

#: **系统**自己就能判定的阻断原因（不需要管理决策，只需要等或修数据）。
SYSTEM_BLOCKING_REASONS: frozenset[str] = frozenset(
    {
        "not_ready",  # 依赖还没完成（结构未就绪）
        "project_not_executable",  # 项目处于终态/未开始
        "task_held",  # blocked 状态：有人显式标记了阻塞
    }
)

#: 派发阻断的完整原因集（两类的并集；由契约测试钉住完备性）。
DISPATCH_BLOCK_REASONS: frozenset[str] = REQUIRES_MANAGEMENT_DECISION | SYSTEM_BLOCKING_REASONS

#: **结构就绪**的定义（纯函数，见 `resolve_ready_tasks`）：只依赖完成。
STRUCTURAL_READINESS_KEYS: frozenset[str] = frozenset({"dependencies", "status"})

#: **可派发** = 结构就绪 + 这些额外条件（用户拍板 §4）。系统只回答"能不能派"，
#: **不回答"该派给谁"** —— 后者是管理决策（R2）。
DISPATCHABILITY_CONDITIONS: tuple[str, ...] = (
    "assignee exists",
    "assignee active",
    "runtime executable",
    "provider bound (non-mock only)",
    "project executable",
    "task not held",
    "no conflicting running session for the assignee",
)

#: Decision-needed 事件：系统**无法继续自动执行**、需要管理判断时才发（用户拍板 §10）。
#:
#: 纪律：不要让 Manager Agent 响应所有普通 lifecycle 事件 —— 正常的状态推进由系统完成
#: （R11），只有这些才唤醒管理层。
DECISION_NEEDED_EVENTS: frozenset[str] = frozenset(
    {
        "task.assignment_required",  # 就绪但没人负责（R4）
        "task.runtime_unavailable",  # 负责人/Runtime 不可用（R5）
        "task.blocked",  # 有人显式标记阻塞
        "task.failed",  # 任务失败 ⇒ 谁来 replan 是管理决策（R12）
        "task.review_failed",  # 评审不通过（M2.7 落地）
        "project.replan_required",  # 计划需要重做
    }
)

#: **事实**事件：只陈述发生了什么，不是审批请求（R8）。正常 DAG 推进只发这些，
#: 不产生 DecisionRecord（R9）。
FACT_EVENTS: frozenset[str] = frozenset(
    {
        "task.ready",
        "task.started",
        "task.completed",
        "project.started",
        "project.delivery_ready",
        "project.completed",
        # 手里的活干完了、项目还没进入执行态 ⇒ 等管理层下一步（W34）。这是**事实**，
        # 不是审批请求：系统只是报告"我没有可执行的工作了"。
        "project.awaiting_management_action",
    }
)

#: 事实事件与 Decision-needed 事件不得重名（一个事件要么是事实，要么要人决策）。
assert not (FACT_EVENTS & DECISION_NEEDED_EVENTS), "事件语义重叠"


# ---------------------------------------------------------------------------
# 11. 不变量注册表（W1–W31，设计 §14）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Invariant:
    """一条 M2 不变量。

    `enforced=True`：M2.0 就有**现存测试锚点**（`anchors` 必须非空且真实存在）。
    `enforced=False`：M2.0 只冻结归属，`owner_stage` 是它的落地阶段。

    双态设计的理由（M1.10 锚点表同款纪律）：不变量是跨阶段的，把「已强制」与
    「已冻结待落地」区分开，比假装全部完成诚实，也比什么都不写好 ——
    `tests/test_m2_contract.py` 断言**没有任何一条被静默丢弃**。
    """

    id: str
    text: str
    enforced: bool
    owner_stage: str = ""
    anchors: tuple[str, ...] = ()


INVARIANTS: tuple[Invariant, ...] = (
    Invariant(
        "W1",
        "Eidolon system does not choose team members.",
        enforced=False,
        owner_stage="M2.4",
    ),
    Invariant(
        "W2",
        "Eidolon system does not make project decomposition decisions.",
        enforced=False,
        owner_stage="M2.4",
    ),
    Invariant(
        "W3",
        "Management decisions must originate from an authorized Agent/User actor.",
        enforced=False,
        owner_stage="M2.3",
    ),
    Invariant(
        "W4",
        "Position defines responsibility/authority/expectations, not fixed workflow.",
        enforced=True,
        owner_stage="M2.2",
        anchors=(
            "test_position_contract_is_responsibility_authority_expectations",
            "test_forbidden_onboarding_actions_do_not_exist_anywhere",
        ),
    ),
    Invariant(
        "W5",
        "Position scope is advisory, not a hard work boundary.",
        enforced=True,
        anchors=("test_position_scope_is_a_soft_constraint",),
    ),
    Invariant(
        "W6",
        "Permissions and security remain hard boundaries.",
        enforced=True,
        anchors=("test_constraint_classes_are_disjoint_and_complete",),
    ),
    Invariant(
        "W7",
        "Position assignment never grants competency score.",
        enforced=True,
        owner_stage="M2.2",
        anchors=(
            "test_position_contract_carries_no_derived_capability",
            "test_role_context_fields_are_all_derivable",
        ),
    ),
    Invariant(
        "W8",
        "Position assignment never copies Person knowledge/skill/evidence.",
        enforced=True,
        owner_stage="M2.2",
        anchors=(
            "test_memory_planes_are_disjoint_and_resolve_to_real_tables",
            "test_forbidden_onboarding_actions_do_not_exist_anywhere",
        ),
    ),
    Invariant(
        "W9",
        "Company knowledge remains institutional after personnel replacement.",
        enforced=True,
        owner_stage="M2.2",
        anchors=("test_memory_planes_are_disjoint_and_resolve_to_real_tables",),
    ),
    Invariant(
        "W10",
        "Personal memory remains with Person.",
        enforced=True,
        owner_stage="M2.2",
        anchors=("test_personal_memory_surfaces_are_person_scoped_where_claimed",),
    ),
    Invariant(
        "W11",
        "Fit is decision-support only.",
        enforced=True,
        anchors=("test_fit_module_is_not_imported_by_execution_paths",),
    ),
    Invariant(
        "W12",
        "An Agent may work outside its normal role scope if authorized.",
        enforced=False,
        owner_stage="M2.4",
    ),
    Invariant(
        "W13",
        "Failure to satisfy role expectations does not automatically offboard an Agent.",
        enforced=True,
        owner_stage="M2.4",
        anchors=("test_no_automatic_offboarding_path_exists",),
    ),
    Invariant(
        "W14",
        "Reassign/replace decisions belong to authorized management Agents or Owner.",
        enforced=False,
        owner_stage="M2.4",
    ),
    Invariant(
        "W15",
        "All management decisions are auditable.",
        enforced=False,
        owner_stage="M2.4",
    ),
    Invariant(
        "W16",
        "Task DAG execution is system responsibility; DAG design is management responsibility.",
        enforced=False,
        owner_stage="M2.5",
    ),
    Invariant(
        "W17",
        "Task review acceptance is not automatically decided by system heuristics.",
        enforced=False,
        owner_stage="M2.7",
    ),
    Invariant(
        "W18",
        "Deterministic tests provide facts, not management judgment.",
        enforced=True,
        owner_stage="M2.7",
        anchors=("test_decision_validation_never_judges_intent",),
    ),
    Invariant(
        "W19",
        "Artifact lineage must be preserved.",
        enforced=False,
        owner_stage="M2.6",
    ),
    Invariant(
        "W20",
        "No new Mission source-of-truth table.",
        enforced=True,
        anchors=("test_no_mission_or_agent_source_of_truth_table_exists",),
    ),
    Invariant(
        "W21",
        "No new Agent source-of-truth table without explicit future ADR.",
        enforced=True,
        anchors=("test_no_mission_or_agent_source_of_truth_table_exists",),
    ),
    Invariant(
        "W22",
        "Project becomes the canonical executable work root.",
        enforced=False,
        owner_stage="M2.1",
    ),
    Invariant(
        "W23",
        "WorkOrder remains economic/commercial wrapper, not execution truth.",
        enforced=True,
        anchors=("test_work_order_is_not_an_execution_or_graph_container",),
    ),
    Invariant(
        "W24",
        "Task completion must produce real work facts before Evidence is created.",
        enforced=True,
        anchors=("test_system_facts_and_agent_decisions_are_disjoint",),
    ),
    Invariant(
        "W25",
        "Mock completion must never masquerade as real production success.",
        enforced=True,
        anchors=("test_system_facts_and_agent_decisions_are_disjoint",),
    ),
    Invariant(
        "W26",
        "Position never derives a per-person workflow, prompt, or SOP.",
        enforced=True,
        owner_stage="M2.2",
        anchors=(
            "test_position_contract_exposes_no_prompt_or_workflow_surface",
            "test_forbidden_onboarding_actions_do_not_exist_anywhere",
        ),
    ),
    Invariant(
        "W27",
        "Role resources are advisory references; they never carry scores or grants.",
        enforced=True,
        owner_stage="M2.2",
        anchors=("test_role_resource_carries_no_numeric_field",),
    ),
    Invariant(
        "W28",
        "DecisionRecord is append-only; outcomes are appended, decisions are never rewritten.",
        enforced=True,
        owner_stage="M2.4",
        anchors=("test_decision_intent_is_frozen_and_carries_no_execution_details",),
    ),
    Invariant(
        "W29",
        "The four verdict/assessment surfaces must not be substituted for each other.",
        enforced=True,
        owner_stage="M2.7",
        anchors=("test_verdict_surfaces_are_distinct_and_owned",),
    ),
    Invariant(
        "W30",
        "`guided` and `managed` project modes share one Task/Assignment/Review substrate.",
        enforced=False,
        owner_stage="M2.1",
    ),
    Invariant(
        "W31",
        "A recruited Agent is not READY_TO_WORK until provisioning completes.",
        enforced=False,
        owner_stage="M2.8",
    ),
    Invariant(
        "W32",
        "Work Intake is a company-configurable responsibility, not a CEO privilege; "
        "the system never picks an arbitrary employee.",
        enforced=True,
        owner_stage="M2.4",
        anchors=(
            "test_work_intake_is_responsibility_routing_with_configurable_target",
            "test_missing_work_intake_manager_enters_waiting_not_fallback",
        ),
    ),
    Invariant(
        "W33",
        "Deterministic planning fixtures are explicit, gated infrastructure; "
        "production projects never fall back to them.",
        enforced=True,
        anchors=(
            "test_planning_fixture_requires_explicit_request_and_gate",
            "test_no_implicit_template_fallback_path_exists",
        ),
    ),
    Invariant(
        "W34",
        "When the responsible manager is absent or fails, the project waits or escalates; "
        "the system never takes over planning.",
        enforced=True,
        owner_stage="M2.4",
        anchors=(
            "test_missing_work_intake_manager_enters_waiting_not_fallback",
            "test_managed_project_does_not_plan_itself",
        ),
    ),
    Invariant(
        "W35",
        "work_mode is snapshotted per project at creation; "
        "later company-default changes never rewrite it.",
        enforced=True,
        anchors=(
            "test_work_mode_is_snapshotted_and_survives_company_default_change",
            "test_company_default_work_mode_follows_company_stage",
        ),
    ),
    Invariant(
        "W36",
        "guided and managed differ only in human involvement level, never in decision ownership.",
        enforced=True,
        owner_stage="M2.4",
        anchors=(
            "test_guided_and_managed_share_one_substrate",
            "test_work_mode_never_encodes_decision_ownership",
        ),
    ),
    Invariant(
        "W37",
        "Authority is default-deny and resolved from the active PositionAssignment, "
        "never from role strings or scores.",
        enforced=True,
        owner_stage="M2.2",
        anchors=(
            "test_authority_is_default_deny_and_reports_a_reason_code",
            "test_authority_follows_assignment_not_role_string",
            "test_authority_and_role_context_modules_never_read_role_strings_or_scores",
        ),
    ),
    Invariant(
        "W38",
        "Authority follows the assignment and never becomes a permanent Person asset.",
        enforced=True,
        owner_stage="M2.2",
        anchors=(
            "test_authority_follows_assignment_not_role_string",
            "test_c2_c4_appointment_never_touches_person_level_assets",
        ),
    ),
    Invariant(
        "W39",
        "Authority validation only validates; "
        "it never selects, ranks, or judges management actions.",
        enforced=True,
        owner_stage="M2.4",
        anchors=(
            "test_authority_layer_validates_but_never_decides",
            "test_authority_snapshot_is_pinnable_recomputable_and_judgement_free",
        ),
    ),
    Invariant(
        "W40",
        "Authority grants are append-only and time-versioned; "
        "any decision can pin why it was legal.",
        enforced=True,
        owner_stage="M2.4",
        anchors=(
            "test_revoke_closes_the_window_and_history_stays_explainable",
            "test_authority_snapshot_is_pinnable_recomputable_and_judgement_free",
            "test_grants_hash_is_deterministic_and_order_independent",
        ),
    ),
    Invariant(
        "W41",
        "Role resources are pointers into existing content; the index never stores content.",
        enforced=True,
        owner_stage="M2.2",
        anchors=(
            "test_authority_tables_have_no_content_or_ranking_columns",
            "test_c6_role_resource_reports_missing_and_advisory_instead_of_inventing",
        ),
    ),
    Invariant(
        "W42",
        "position_definition_packages stays resource provisioning; "
        "management authority lives only in position_authority_grants.",
        enforced=True,
        owner_stage="M2.2",
        anchors=("test_v41_adds_authority_tables_without_touching_packages_semantics",),
    ),
    Invariant(
        "T1",
        "Agent tools do not own business truth.",
        enforced=True,
        owner_stage="M2.3",
        anchors=("test_tools_do_not_own_business_truth",),
    ),
    Invariant(
        "T2",
        "HTTP APIs and Agent tools share the same application/domain services.",
        enforced=True,
        owner_stage="M2.3",
        anchors=("test_read_tools_reuse_the_same_query_services_as_http",),
    ),
    Invariant(
        "T3",
        "No generic player-facing write /tools API.",
        enforced=True,
        owner_stage="M2.3",
        anchors=("test_no_player_facing_tool_router_exists",),
    ),
    Invariant(
        "T4",
        "Internal tool calls never bypass Authority.",
        enforced=True,
        owner_stage="M2.3",
        anchors=(
            "test_internal_transport_still_enforces_authority",
            "test_registry_is_sound_and_write_specs_declare_authority_and_target",
        ),
    ),
    Invariant(
        "T5",
        "Actor identity is injected by runtime/system context, not trusted from model arguments.",
        enforced=True,
        owner_stage="M2.3",
        anchors=("test_actor_identity_comes_from_context_and_args_are_rejected",),
    ),
    Invariant(
        "T6",
        "Fit and other read tools provide facts, never make management decisions.",
        enforced=True,
        owner_stage="M2.3",
        anchors=("test_calculate_task_fit_returns_facts_without_ranking",),
    ),
    Invariant(
        "T7",
        "A successful Tool call means: Agent decided, System validated, System applied.",
        enforced=True,
        owner_stage="M2.3",
        anchors=("test_successful_write_is_decided_validated_and_applied",),
    ),
    Invariant(
        "T8",
        "PositionAuthorityGrant is the management authorization source.",
        enforced=True,
        owner_stage="M2.3",
        anchors=("test_authority_source_is_the_grant_table",),
    ),
    Invariant(
        "T9",
        "Resource Package is never used as a substitute for Authority.",
        enforced=True,
        owner_stage="M2.3",
        anchors=("test_resource_packages_do_not_grant_authority",),
    ),
    Invariant(
        "T10",
        "Transport choice does not change domain invariants.",
        enforced=True,
        owner_stage="M2.3",
        anchors=("test_transport_does_not_change_domain_invariants",),
    ),
    Invariant(
        "T11",
        "Human management APIs and Agent management tools must produce equivalent domain effects.",
        enforced=True,
        owner_stage="M2.3",
        anchors=("test_human_and_agent_paths_produce_equivalent_domain_effects",),
    ),
    Invariant(
        "T12",
        "Tool execution must be auditable.",
        enforced=True,
        owner_stage="M2.3",
        anchors=("test_every_tool_call_is_audited",),
    ),
    Invariant(
        "DR1",
        "DecisionRecord is intent, ToolAudit is execution, domain state is truth: "
        "three layers never merged.",
        enforced=True,
        owner_stage="M2.4",
        anchors=("test_three_layers_are_separate",),
    ),
    Invariant(
        "DR2",
        "One decision may produce N tool actions; a tool call is never by itself a decision.",
        enforced=True,
        owner_stage="M2.4",
        anchors=("test_one_decision_produces_many_actions",),
    ),
    Invariant(
        "DR3",
        "The decision/audit link is one-way: ToolAudit.decision_id points at DecisionRecord; "
        "there is no reverse array.",
        enforced=True,
        owner_stage="M2.4",
        anchors=("test_link_direction_is_single_way",),
    ),
    Invariant(
        "DR4",
        "DecisionRecord never grants authority; every action is re-validated at execution time.",
        enforced=True,
        owner_stage="M2.4",
        anchors=("test_decision_never_grants_authority",),
    ),
    Invariant(
        "DR5",
        "DecisionRecord never copies tool input or output; those live in ToolAudit.",
        enforced=True,
        owner_stage="M2.4",
        anchors=("test_decision_record_does_not_copy_tool_payloads",),
    ),
    Invariant(
        "DR6",
        "Decision status can express PARTIALLY_APPLIED; "
        "partial success is never rounded to success or failure.",
        enforced=True,
        owner_stage="M2.4",
        anchors=("test_partial_apply_is_expressed_not_rounded",),
    ),
    Invariant(
        "DR7",
        "Every tool declares its decision semantics (none/optional/required) "
        "and the executor enforces it.",
        enforced=True,
        owner_stage="M2.4",
        anchors=("test_decision_semantics_are_declared_and_enforced",),
    ),
    Invariant(
        "DR8",
        "parent_decision_id expresses the management decision tree; "
        "no separate workflow model is introduced.",
        enforced=True,
        owner_stage="M2.4",
        anchors=("test_parent_decision_forms_a_tree_without_a_workflow_model",),
    ),
    Invariant(
        "DR9",
        "Decision context is a bounded snapshot with stable refs and a hash, "
        "never a copy of the database.",
        enforced=True,
        owner_stage="M2.4",
        anchors=("test_context_is_bounded_and_hashed",),
    ),
    Invariant(
        "DR10",
        "Decision outcome is traceable (decision to outcome); no capability scoring in M2.4.",
        enforced=True,
        owner_stage="M2.4",
        anchors=("test_outcome_is_traceable_without_scoring",),
    ),
    Invariant(
        "R1",
        "System may automatically dispatch only to the already-authorized assignee.",
        enforced=True,
        owner_stage="M2.5",
        anchors=(
            "test_dispatch_only_to_existing_assignee",
            "test_ready_unassigned_is_never_auto_assigned",
        ),
    ),
    Invariant(
        "R2",
        "System never selects an assignee when a task becomes ready.",
        enforced=True,
        owner_stage="M2.5",
        anchors=("test_ready_unassigned_is_never_auto_assigned",),
    ),
    Invariant(
        "R3",
        "Structural readiness and dispatchability are distinct concepts.",
        enforced=True,
        owner_stage="M2.5",
        anchors=("test_readiness_and_dispatchability_are_distinct",),
    ),
    Invariant(
        "R4",
        "A ready unassigned task requires a management decision.",
        enforced=True,
        owner_stage="M2.5",
        anchors=("test_ready_unassigned_escalates_as_management_decision",),
    ),
    Invariant(
        "R5",
        "Runtime/resource failure does not cause automatic reassignment.",
        enforced=True,
        owner_stage="M2.5",
        anchors=("test_runtime_unavailable_does_not_reassign",),
    ),
    Invariant(
        "R6",
        "guided and managed share the same DAG runtime.",
        enforced=True,
        owner_stage="M2.5",
        anchors=("test_guided_and_managed_share_one_dag_runtime",),
    ),
    Invariant(
        "R7",
        "Fixture graphs share the same dispatcher/runtime after graph creation.",
        enforced=True,
        owner_stage="M2.5",
        anchors=("test_fixture_graph_uses_the_same_runtime",),
    ),
    Invariant(
        "R8",
        "task.ready is a fact event, not a management approval request.",
        enforced=True,
        owner_stage="M2.5",
        anchors=("test_task_ready_is_a_fact_event",),
    ),
    Invariant(
        "R9",
        "Normal DAG progress does not require a new DecisionRecord.",
        enforced=True,
        owner_stage="M2.5",
        anchors=("test_normal_dag_progress_creates_no_decision",),
    ),
    Invariant(
        "R10",
        "Any reassignment must originate from an authorized Agent/User decision.",
        enforced=True,
        owner_stage="M2.5",
        anchors=("test_reassignment_requires_a_decision",),
    ),
    Invariant(
        "R11",
        "Manager Agents are invoked for decisions/exceptions, not ordinary scheduling.",
        enforced=True,
        owner_stage="M2.5",
        anchors=("test_decision_needed_events_are_the_only_manager_triggers",),
    ),
    Invariant(
        "R12",
        "No production fallback may silently assign or plan work on behalf of management.",
        enforced=True,
        owner_stage="M2.5",
        anchors=("test_no_silent_auto_planning_or_assignment_fallback",),
    ),
)

#: 允许 `owner_stage` 出现的阶段 id（防止填错阶段名）。
M2_STAGES: frozenset[str] = frozenset(f"M2.{i}" for i in range(1, 11))
