"""M3 生产运行与成长域契约（M3.0 Production Agent Contract Freeze）。

`docs/m3-agent-production-growth-design.md` 的代码化。**与 `app/work/contracts.py` 同款纪律：
本模块是纯契约层** —— 不建表、不发事件、不碰 Session、不改任何业务行为。

最高原则（M2 冻结，M3 不重新谈判）：

    System provides facts.   Agent makes decisions.
    System validates.        System executes.        System records.

本模块把 M3 的边界变成**可执行、可测试**的事实：

| 契约 | 内容 | owner 阶段 |
| --- | --- | --- |
| 生产阶段 | `ProductionStage`（employment / provisioning / runtime / execution） | M3.0 |
| 就绪 → 阶段 | `READINESS_ITEM_STAGE`（与 `contracts.READINESS_FACT_SOURCES` 双向一致） | M3.0 |
| 编排步骤 | `PROVISIONING_STEP_ORDER`（与就绪必答项逐字一致） | M3.0 |
| 失败原因 | `ProvisioningFailureReason`（8 个可解释原因） | M3.0 |
| 幂等 | `IDEMPOTENT_ENSURE_OPERATIONS` + 覆盖场景表 | M3.1 |
| 对账 | `RECONCILIATION_*`（detect / mark / repair；**禁止静默删除**） | M3.1 |
| 真实 / 模拟 | `execution_mode()` + `EVIDENCE_ENVIRONMENT_*` | M3.0 |
| Provenance | `PROVENANCE_*`（现存 vs 欠账显式登记） | M3.1 / M3.5 |
| 能力缺口动作 | `CAPABILITY_GAP_ACTIONS`（7 条，每条有工具或归属） | M3.3 |
| 重试语义 | `TECHNICAL_RETRY_*` / `MANAGEMENT_REPLAN_*`（两套语义不共用计数器） | M3.4 |
| 遥测词表 | `RUNTIME_TELEMETRY_KINDS`（∩ 领域事件 = ∅） | M3.4 |
| 履历契约 | `WORK_EXPERIENCE_FIELDS` + `WORK_EXPERIENCE_SOURCES` | M3.5 |
| 资产读模型 | `AGENT_PROFILE_LONG_TERM` / `AGENT_PROFILE_CURRENT_STATE` | M3.7 |
| 可靠性指标 | `RELIABILITY_METRICS`（全部可归因，禁止总战力） | M3.6 |
| Agent SoT 禁区 | `FORBIDDEN_AGENT_SOT_TABLES` | M3.0 |
| 不变量 | `PRODUCTION_INVARIANTS`（P1–P36，**全部 enforced**） | M3.0 |

三条纪律：

1. **不允许"冻结待锚点"的双态**（M2.10 的教训）：M3.0 的每条 P 都必须有**现存**测试锚点；
   尚未实现的语义用"契约 + 欠账登记（owner_stage）"表达，而不是假装不变量已落地。
2. **不允许"幽灵词条"**：词表里的每一项，要么有真实实现，要么有 owner 阶段。
3. 改动本模块 = 改 M3 契约：必须走设计文档评审，并同步 `tests/test_m3_contract.py`。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.models.enums import RuntimeType
from app.work import contracts as C

__all__ = [
    "M3_STAGES",
    "ProductionStage",
    "READINESS_ITEM_STAGE",
    "READINESS_VIEWS",
    "READINESS_EXECUTION_GATE",
    "PROVISIONING_STEP_ORDER",
    "ProvisioningFailureReason",
    "IDEMPOTENT_ENSURE_OPERATIONS",
    "IDEMPOTENCY_SCENARIOS",
    "RECONCILIATION_ACTIONS",
    "RECONCILIATION_FORBIDDEN_ACTIONS",
    "RECONCILIATION_CODE_PATHS",
    "MOCK_RUNTIME_TYPE",
    "REAL_RUNTIME_TYPES",
    "PLACEHOLDER_RUNTIME_TYPES",
    "execution_mode",
    "EVIDENCE_ENVIRONMENT_REAL",
    "EVIDENCE_ENVIRONMENT_MOCK",
    "WorkSessionPurpose",
    "RuntimeProvenance",
    "PROVENANCE_SNAPSHOT_FIELDS",
    "PROVENANCE_PRESENT_FIELDS",
    "PROVENANCE_OWED_FIELDS",
    "PROVENANCE_WRITE_ONCE_TRIGGER",
    "RuntimeTelemetryKind",
    "RUNTIME_TELEMETRY_KINDS",
    "TELEMETRY_ADAPTER_EVENT",
    "TELEMETRY_NOT_YET_MAPPED",
    "CapabilityGapAction",
    "CAPABILITY_GAP_ACTIONS",
    "CAPABILITY_GAP_ACTION_TOOLS",
    "CAPABILITY_GAP_ACTION_OWNER",
    "AUTO_CAPABILITY_ACTIONS",
    "TECHNICAL_RETRY_OWNER",
    "MANAGEMENT_REPLAN_OWNER",
    "RETRY_SEMANTICS",
    "WORK_EXPERIENCE_FIELDS",
    "WORK_EXPERIENCE_SOURCES",
    "WORK_EXPERIENCE_OWED_FIELDS",
    "AGENT_PROFILE_LONG_TERM",
    "AGENT_PROFILE_CURRENT_STATE",
    "AGENT_PROFILE_SOURCES",
    "RELIABILITY_METRICS",
    "FORBIDDEN_AGENT_SOT_TABLES",
    "FORBIDDEN_AGENT_REFERENCE_COLUMNS",
    "CAPABILITY_KEYS_THAT_MUST_BE_FORBIDDEN",
    "PERSON_SCOPED_COLUMN_OWED",
    "PRODUCTION_INVARIANTS",
]

#: M3 的合法阶段名（用于 owner_stage 校验；M3.0 起）。
M3_STAGES: frozenset[str] = frozenset(f"M3.{i}" for i in range(0, 11))


# ---------------------------------------------------------------------------
# § 1. 生产阶段：四层就绪（派生，不是断言）
# ---------------------------------------------------------------------------


class ProductionStage(StrEnum):
    """Agent 从"在册"到"能开工"的四个**可解释**层次（设计 §4）。

    每层都派生自现有事实。四层不是状态机 —— 它们是**检查面**：
    少了哪一层，Manager 就看到哪一层的缺口。
    """

    employment = "employment"  # 有效在册 + 有职责面
    provisioning = "provisioning"  # 环境已开通（工作区 / 实例 / 供应商）
    runtime = "runtime"  # 实例健康 + 供应商/模型可用
    execution = "execution"  # 现在能不能真的开工（前三层 + 必答项）


#: 就绪项 → 生产阶段（与 `contracts.READINESS_FACT_SOURCES` **双向一致**）。
#:
#: 为什么钉死：如果就绪项加了而阶段没加（或反过来），就会长出"配了但不检"或
#: "检了但没配"的漂移口 —— 那正是 M2.10 复审计在冻结面上抓到的同类问题。
READINESS_ITEM_STAGE: dict[str, ProductionStage] = {
    "position": ProductionStage.employment,
    "workspace": ProductionStage.provisioning,
    "runtime": ProductionStage.runtime,
    "provider": ProductionStage.runtime,
}

#: 两个**不同**的就绪口径（M2.8 冻结，M3 不合并；守卫钉住"严格口径 ⊇ 执行门禁"）。
#:
#: - 严格口径：`ready_to_work` = **四项全就绪** —— 面向人（告诉你"差什么"）；
#: - 执行门禁：`blocking_gaps` = `READINESS_REQUIRED_FOR_EXECUTION` 的必答项 —— 面向派发。
#:   刻意更窄：`position` 是软契约（W5），缺职位不应该把一个人彻底挡在工作之外。
READINESS_VIEWS: dict[str, str] = {
    "strict": "readiness_report.ready_to_work（四项全就绪：面向人）",
    "execution_gate": "readiness_report.blocking_gaps（必答项：面向派发）",
}
READINESS_EXECUTION_GATE = "blocking_gaps"


# ---------------------------------------------------------------------------
# § 2. Provisioning 契约（形状在 M3.0 冻结，真实执行在 M3.1）
# ---------------------------------------------------------------------------

#: 编排步骤顺序（设计 §6.1）。
#:
#: 必须与 `contracts.READINESS_REQUIRED_FOR_EXECUTION["real"]` **逐字一致**：
#: 编排配置的环境事实 == 就绪检查的事实（P31）。
PROVISIONING_STEP_ORDER: tuple[str, ...] = ("workspace", "runtime", "provider")


class ProvisioningFailureReason(StrEnum):
    """Provisioning 的失败原因（**可解释**优先于"未知错误"）。

    纪律：不要巨大状态机。只要求每个失败都落在一个能解释的原因上，
    并写进 `provisioning_jobs/steps`（现有载体）+ 员工就绪回报（`partial`）。
    """

    provisioning_failed = "PROVISIONING_FAILED"
    workspace_unavailable = "WORKSPACE_UNAVAILABLE"
    start_failed = "START_FAILED"
    unhealthy = "UNHEALTHY"
    provider_unavailable = "PROVIDER_UNAVAILABLE"
    model_unavailable = "MODEL_UNAVAILABLE"
    capability_mismatch = "CAPABILITY_MISMATCH"
    runtime_unavailable = "RUNTIME_UNAVAILABLE"


#: 必须幂等的 ensure 操作（P6）。名字必须与 `app/work/readiness.py` 的真实函数对应
#: —— 守卫会解析（"声明了幂等但它不存在"会转红）。
IDEMPOTENT_ENSURE_OPERATIONS: tuple[str, ...] = (
    "ensure_workspace",
    "ensure_runtime_instance",
    "ensure_provider_binding",
)

#: 幂等必须覆盖的现实场景（设计 §6.3）。**值 = 期望行为**，不是状态机。
IDEMPOTENCY_SCENARIOS: dict[str, str] = {
    "row_and_container_ok": "no-op（不重建、不重启）",
    "row_present_container_missing": "按 DB 行重建容器（repair）",
    "container_present_row_missing": "登记孤儿 + 上报（**不删除**）",
    "container_unhealthy": "尝试恢复；失败则记原因，不反复重启风暴",
    "crash_mid_orchestration": "下次 ensure 从事实重新收敛，不依赖内存状态",
}

#: 对账动作词表（P7）：detect / mark / repair —— **没有删除**。
#:
#: 为什么：删除外部资源是不可逆动作，必须由人/管理层显式发起（设计 §6.4）。
RECONCILIATION_ACTIONS: dict[str, tuple[str, ...]] = {
    "orphan_detected": ("mark", "report"),
    "runtime_missing": ("repair", "mark"),
    "status_stale": ("refresh", "report"),
    "unhealthy": ("repair", "mark", "report"),
}

#: 对账路径**禁止**出现的动作（不可逆 / 静默）。
RECONCILIATION_FORBIDDEN_ACTIONS: frozenset[str] = frozenset(
    {"delete", "remove", "prune", "purge", "destroy", "rm"}
)

#: 守卫扫描的**编排层**代码路径（低层 transport 例如 `runtimes/docker/service.py`
#: 提供显式删除 API 是合法的；纪律落在编排层：对账不得静默删除）。
RECONCILIATION_CODE_PATHS: tuple[str, ...] = ("work/readiness.py",)


# ---------------------------------------------------------------------------
# § 3. 真实 vs 模拟（P24 / P25）
# ---------------------------------------------------------------------------

MOCK_RUNTIME_TYPE = RuntimeType.mock.value

#: 有真实实现、M3.1 要真正拉起的运行时（设计：**不要顺手扩类型**）。
REAL_RUNTIME_TYPES: tuple[str, ...] = (RuntimeType.hermes.value, RuntimeType.openclaw.value)

#: 仍是 placeholder 的运行时（M3 不扩）。列出来是为了"诚实"：
#: 它们出现在枚举里，但 M3 不承诺它们能跑。
PLACEHOLDER_RUNTIME_TYPES: tuple[str, ...] = (
    RuntimeType.codex.value,
    RuntimeType.claude_code.value,
    RuntimeType.opencode.value,
    RuntimeType.custom.value,
)


def execution_mode(runtime_type: str) -> str:
    """执行模式判定（**唯一口径**）：`"mock"` 或 `"real"`。

    这是纯函数：契约层就能判定，不依赖数据库、不依赖实例存在与否 ——
    "看起来像真的"必须不可伪造（P24）。
    """
    return "mock" if str(runtime_type) == MOCK_RUNTIME_TYPE else "real"


#: 证据环境标识（与 `app/evidence/policy.py` 逐字一致；守卫对拍）。
EVIDENCE_ENVIRONMENT_REAL = ""  # 空串 = 真实（既有约定，见 evidence/candidate.py）
EVIDENCE_ENVIRONMENT_MOCK = "mock"


# ---------------------------------------------------------------------------
# § 4. Runtime Provenance（P14 / P34）
# ---------------------------------------------------------------------------


class WorkSessionPurpose(StrEnum):
    """KTD4: worker targets Task; management targets Project; review targets ReviewRequest.

    Purpose and target are creation-time provenance. Ending a non-worker session
    records execution only; domain decisions remain the business writers.
    """

    worker = "worker"
    management = "management"
    review = "review"


@dataclass(frozen=True)
class RuntimeProvenance:
    """一次执行"是谁在什么环境用什么模型干的"——**不可改写**的快照。

    历史不由当前状态反推（P23 的同源纪律）：换绑、换模型、换实例只影响**未来**会话。
    """

    runtime_type: str
    runtime_instance_id: int | None
    provider_id: int | None
    model: str
    runtime_session_ref: str
    deployment_mode: str = ""

    def as_dict(self) -> dict:
        return {
            "runtime_type": self.runtime_type,
            "runtime_instance_id": self.runtime_instance_id,
            "provider_id": self.provider_id,
            "model": self.model,
            "runtime_session_ref": self.runtime_session_ref,
            "deployment_mode": self.deployment_mode,
        }


#: Provenance 必须覆盖的字段全集（= 现存 + 欠账）。
PROVENANCE_SNAPSHOT_FIELDS: tuple[str, ...] = (
    "runtime_type",
    "runtime_instance_id",
    "provider_id",
    "model",
    "runtime_session_ref",
    "deployment_mode",
)

#: **现存**承载：`work_sessions` 已有列（守卫对拍 metadata）。
PROVENANCE_PRESENT_FIELDS: tuple[str, ...] = (
    "runtime_type",
    "runtime_instance_id",
    "provider_id",
    "model",
    "runtime_session_ref",
    "deployment_mode",
)

#: The execution provenance debt is repaid. Empty is valid only with the field,
#: creation writer, historical reader and immutability behavior all implemented.
PROVENANCE_OWED_FIELDS: dict[str, str] = {}

#: 写一次纪律的触发点：会话结束后 provenance 不可改写。
PROVENANCE_WRITE_ONCE_TRIGGER = "work_sessions.ended_at"


# ---------------------------------------------------------------------------
# § 5. Execution Telemetry vs Domain Event（P16 / P35）
# ---------------------------------------------------------------------------


class RuntimeTelemetryKind(StrEnum):
    """**执行环境**发生了什么（结构化事件；不是 token 流）。

    这些是遥测（Telemetry），**不是**领域事件（Domain Event）。
    token 级流只进日志/短期缓冲，不永久落库（M3.4 决策）。
    """

    session_started = "session.started"
    session_completed = "session.completed"
    session_failed = "session.failed"
    message_completed = "message.completed"
    tool_called = "tool.called"
    tool_completed = "tool.completed"
    tool_failed = "tool.failed"
    artifact_created = "artifact.created"
    runtime_warning = "runtime.warning"
    runtime_error = "runtime.error"


RUNTIME_TELEMETRY_KINDS: tuple[str, ...] = tuple(kind.value for kind in RuntimeTelemetryKind)

#: 遥测 → 现有 adapter 事件（`app/runtimes/base.RuntimeEventKind`）的映射。
#: **不新造第二套 adapter 词表**：能对上的就直接对上。
TELEMETRY_ADAPTER_EVENT: dict[str, str] = {
    RuntimeTelemetryKind.session_started.value: "status",
    RuntimeTelemetryKind.session_completed.value: "completed",
    RuntimeTelemetryKind.session_failed.value: "error",
    RuntimeTelemetryKind.message_completed.value: "message",
    RuntimeTelemetryKind.tool_called.value: "tool_call",
    RuntimeTelemetryKind.tool_completed.value: "tool_call",
    RuntimeTelemetryKind.tool_failed.value: "tool_call",
    RuntimeTelemetryKind.artifact_created.value: "artifact",
    RuntimeTelemetryKind.runtime_error.value: "error",
    RuntimeTelemetryKind.runtime_warning.value: "status",
}

#: 尚无 adapter 对应物、必须在 M3.4 落地的遥测事件（诚实登记，不假装已有）。
TELEMETRY_NOT_YET_MAPPED: frozenset[str] = frozenset(
    set(RUNTIME_TELEMETRY_KINDS) - set(TELEMETRY_ADAPTER_EVENT)
)


# ---------------------------------------------------------------------------
# § 6. Capability Gap Action Model（P9 / P10 / P33）
# ---------------------------------------------------------------------------


class CapabilityGapAction(StrEnum):
    """Manager 面对能力缺口时可选的**动作**（系统只提供事实与执行）。

    系统永不替 Manager 选（P9/P10）：契约层不得出现按阈值自动选动作的函数。
    """

    learn = "learn"
    use_internal_employee = "use_internal_employee"
    reassign = "reassign"
    recruit = "recruit"
    revise_task = "revise_task"
    split_task = "split_task"
    delegate = "delegate"


CAPABILITY_GAP_ACTIONS: tuple[CapabilityGapAction, ...] = tuple(CapabilityGapAction)

#: 动作 → 真实工具；U12 全部落地，Runtime 目录复用此映射。
CAPABILITY_GAP_ACTION_TOOLS: dict[CapabilityGapAction, tuple[str, ...]] = {
    CapabilityGapAction.learn: ("create_learning_priority", "request_training"),
    CapabilityGapAction.use_internal_employee: ("assign_task",),
    CapabilityGapAction.reassign: ("assign_task", "update_task"),
    CapabilityGapAction.recruit: ("recruit_from_market",),
    CapabilityGapAction.revise_task: ("update_task", "replan_project"),
    CapabilityGapAction.split_task: ("create_task", "create_dependency"),
    CapabilityGapAction.delegate: ("delegate_project",),
}

#: 动作 → 没工具时的 owner 阶段（空字符串 = 已有工具）。
CAPABILITY_GAP_ACTION_OWNER: dict[CapabilityGapAction, str] = {
    CapabilityGapAction.learn: "",
    CapabilityGapAction.use_internal_employee: "",
    CapabilityGapAction.reassign: "",
    CapabilityGapAction.recruit: "",
    CapabilityGapAction.revise_task: "",
    CapabilityGapAction.split_task: "",
    CapabilityGapAction.delegate: "",
}

#: **自动动作必须是空的**（P9）：缺口的响应只能来自 Manager/User 的决策。
AUTO_CAPABILITY_ACTIONS: frozenset[CapabilityGapAction] = frozenset()


# ---------------------------------------------------------------------------
# § 7. Technical Retry vs Management Replan（P15）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RetrySemantics:
    """一套重试语义：谁拥有、事实落在哪、计数器叫什么。"""

    owner: str
    fact_host: str
    counter: str
    auto: bool
    note: str = ""


TECHNICAL_RETRY_OWNER = "system"
MANAGEMENT_REPLAN_OWNER = "manager_agent"

RETRY_SEMANTICS: dict[str, RetrySemantics] = {
    "technical_retry": RetrySemantics(
        owner=TECHNICAL_RETRY_OWNER,
        fact_host="runtime_attempts + work_sessions.technical_retry_count",
        counter="technical_retry_count",
        auto=True,
        note="U13 已保存请求/尝试事实；U15 待实现有限次 + 退避策略；不自动改方案",
    ),
    "management_replan": RetrySemantics(
        owner=MANAGEMENT_REPLAN_OWNER,
        fact_host="decision_records（M2 决策信封）+ review_requests.verdict",
        counter="tasks.rework_count",
        auto=False,
        note="Agent 做错/方案不可行/反复失败：必须 Manager 决策（replan_project）",
    ),
}


# ---------------------------------------------------------------------------
# § 8. Work Experience（M3.5 落地的载体，M3.0 先冻契约）
# ---------------------------------------------------------------------------

WORK_EXPERIENCE_FIELDS: tuple[str, ...] = (
    "person_id",
    "employee_id",
    "company_id",
    "position_code",
    "project_id",
    "project_title",
    "task_id",
    "task_title",
    "task_kind",
    "role_in_work",
    "contribution",
    "requirement_snapshot",
    "result",
    "review_verdict",
    "rework_count",
    "started_at",
    "completed_at",
    # 需要但当前没有承载的字段（欠账，见 WORK_EXPERIENCE_OWED_FIELDS）
    "difficulty_snapshot",
    "management_decision_ref",
)

#: 字段 → 真实来源（必须能解析到**现存**表列；守卫按 M2 A4 同款纪律解析）。
#:
#: 为什么用派生读模型而不是新表：`career_events` 是**职位履历审计**
#: （只有 position 口径，没有 task/project/artifact/review），
#: 硬塞只能进 `metadata_json`（不可查询、不可守卫、会变成垃圾抽屉）。
WORK_EXPERIENCE_SOURCES: dict[str, str] = {
    "person_id": "work_sessions.person_id",
    "employee_id": "work_sessions.employee_id",
    "company_id": "work_sessions.company_id",
    "position_code": "work_sessions.provenance_snapshot_json（当时任职 code）",
    "project_id": "work_sessions.provenance_snapshot_json（当时项目 id）",
    "project_title": "work_sessions.provenance_snapshot_json（当时项目名称）",
    "task_id": "work_sessions.task_id",
    "task_title": "work_sessions.provenance_snapshot_json（当时任务名称）",
    "task_kind": "work_sessions.provenance_snapshot_json（当时任务 kind）",
    "role_in_work": "tasks.assignee_id（执行）+ review_requests.reviewer_employee_id（评审）",
    "contribution": "drive_nodes.task_id（产出归属，M2 H2）",
    "requirement_snapshot": "work_sessions.provenance_snapshot_json（当时任务要求）",
    "result": "tasks.status",
    "review_verdict": "review_requests.verdict",
    "rework_count": "tasks.rework_count",
    "started_at": "work_sessions.started_at",
    "completed_at": "work_sessions.ended_at",
}

#: 履历需要但**当前没有承载**的字段 → owner 阶段（P34 同款诚实登记）。
WORK_EXPERIENCE_OWED_FIELDS: dict[str, str] = {
    "difficulty_snapshot": "M3.5",  # 现在只有 tasks.priority 可作代理，不是难度快照
    "management_decision_ref": "M3.5",  # 决策角色与决策 id 的显式关联
}


# ---------------------------------------------------------------------------
# § 9. AgentProfile：长期资产 vs 当前运行态（P26 / P23）
# ---------------------------------------------------------------------------

AGENT_PROFILE_LONG_TERM: tuple[str, ...] = (
    "identity",
    "traits",
    "competencies",
    "validated_skills",
    "knowledge_domains",
    "work_experience",
    "representative_projects",
    "reliability_metrics",
    "specializations",
    "collaboration_facts",
    "management_experience",
    "representative_artifacts",
)

AGENT_PROFILE_CURRENT_STATE: tuple[str, ...] = (
    "company",
    "role",
    "load",
    "runtime",
    "provider",
    "current_task",
    "readiness",
    "active_work_session",
    "blocked_reason",
    "review_status",
)

AGENT_PROFILE_SOURCES: dict[str, str] = {
    "identity": "persons.name",
    "traits": "employee_brains.traits",
    "competencies": "employee_competencies.score + employee_competencies.confidence",
    "validated_skills": "skills.validation_status",
    "knowledge_domains": "knowledge_items.topic",
    "work_experience": "（M3.5 的派生读模型，见 WORK_EXPERIENCE_SOURCES）",
    "representative_projects": "tasks.project_id",
    "reliability_metrics": "（M3.6 的派生指标，见 RELIABILITY_METRICS）",
    "specializations": "（M3.6 从证据/历史收敛）",
    "collaboration_facts": "drive_nodes.task_id + review_requests.reviewer_employee_id",
    "management_experience": "decision_records.actor_person_id",
    "representative_artifacts": "drive_nodes.task_id",
    "company": "employees.company_id",
    "role": "position_definitions.code",
    "load": "tasks.assignee_id",
    "runtime": "runtime_instances.status",
    "provider": "model_bindings.provider_id",
    "current_task": "employees.current_task_id",
    "readiness": "（M2.8 的派生就绪报告，见 work/readiness.py）",
    "active_work_session": "work_sessions.status",
    "blocked_reason": "tasks.status",
    "review_status": "review_requests.status",
}


# ---------------------------------------------------------------------------
# § 10. 可靠性指标：全部可归因，禁止总战力（P19 / P21）
# ---------------------------------------------------------------------------

#: 指标 → 来源/算子（必须能解析到真实表列）。
RELIABILITY_METRICS: dict[str, str] = {
    "tasks_completed": "tasks.status",
    "success_rate": "tasks.status",
    "first_pass_review_rate": "review_requests.verdict + tasks.rework_count",
    "rework_rate": "tasks.rework_count",
    "on_time_rate": "tasks.planned_end_at + tasks.actual_end_at",
    "high_difficulty_tasks": "tasks.priority",
    "runtime_failure_rate": "work_sessions.status + work_sessions.error",
    "review_score": "review_facts.payload_json",
    "delivery_quality": "review_requests.verdict",
    "management_decision_outcomes": "decision_records.status + decision_records.outcome_note",
}


# ---------------------------------------------------------------------------
# § 11. Agent SoT 禁区（P1）+ 能力键禁区（P3）
# ---------------------------------------------------------------------------

#: 禁止成为第二身份真相的表（整表名；M2 的 A7 守卫只覆盖了 `agent` / `agents`）。
FORBIDDEN_AGENT_SOT_TABLES: frozenset[str] = frozenset(
    {
        "agent",
        "agents",
        "agent_profiles",
        "agent_assets",
        "agent_records",
        "agent_identities",
        "agent_skills",
        "agent_competencies",
        "agent_experiences",
    }
)

#: 长期资产里**当前没有 Person 口径列**的载体 → owner 阶段（P2 的欠账登记）。
#:
#: 为什么显式登记："长期资产随人走"要求历史归因能落到 Person；只有 employee_id 的载体
#: 在换公司后会断链。承认欠账，而不是把它从盘点里删掉。
PERSON_SCOPED_COLUMN_OWED: dict[str, str] = {
    # 现在只有 employee_id；换公司后归因需要 person 口径（M3.5 一并处理）
    "career_events": "M3.5",
}

#: 禁止的表级信号：任何表出现这些列名 = 把 Agent 变成了外键主体。
FORBIDDEN_AGENT_REFERENCE_COLUMNS: frozenset[str] = frozenset({"agent_id", "agent_ref"})
#: 运行时策略里**必须**被禁止的能力类键（P3：Runtime 配置不是 Person 能力）。
#: 这些键必须已经在 `contracts.FORBIDDEN_RUNTIME_POLICY_KEYS` 里 —— 守卫对拍。
CAPABILITY_KEYS_THAT_MUST_BE_FORBIDDEN: frozenset[str] = frozenset(
    {"skills", "skill_package", "persona", "personality", "traits", "temperature", "brain"}
)


# ---------------------------------------------------------------------------
# § 12. M3 不变量（P1–P36）
# ---------------------------------------------------------------------------


PRODUCTION_INVARIANTS: tuple[C.Invariant, ...] = (
    C.Invariant(
        "P1",
        "Agent has no new identity source of truth.",
        enforced=True,
        owner_stage="M3.0",
        anchors=(
            "test_no_agent_source_of_truth_table_or_column_exists",
            "test_agent_profile_is_a_read_model_without_a_write_path",
        ),
    ),
    C.Invariant(
        "P2",
        "Person remains the long-term asset owner.",
        enforced=True,
        owner_stage="M3.0",
        anchors=("test_person_owned_assets_keep_a_person_scoped_column",),
    ),
    C.Invariant(
        "P3",
        "Runtime configuration is not Person competency.",
        enforced=True,
        owner_stage="M3.0",
        anchors=(
            "test_runtime_policy_forbids_capability_keys",
            "test_runtime_layer_never_writes_capability_facts",
        ),
    ),
    C.Invariant(
        "P4",
        "Runtime readiness is derived, not manually asserted.",
        enforced=True,
        owner_stage="M3.0",
        anchors=(
            "test_readiness_is_derived_and_not_a_stored_boolean",
            "test_readiness_report_explains_every_item",
            "test_readiness_has_two_views_and_the_gate_is_the_narrow_one",
        ),
    ),
    C.Invariant(
        "P5",
        "A RuntimeInstance DB row alone does not imply READY_TO_WORK.",
        enforced=True,
        owner_stage="M3.0",
        anchors=("test_runtime_row_alone_is_not_ready_to_work",),
    ),
    C.Invariant(
        "P6",
        "Provisioning is idempotent.",
        enforced=True,
        owner_stage="M3.1",
        anchors=(
            "test_idempotent_ensure_operations_exist_and_cover_scenarios",
            "test_orchestrate_twice_does_not_duplicate_provisioning_facts",
        ),
    ),
    C.Invariant(
        "P7",
        "Runtime reconciliation must not silently delete unknown external resources.",
        enforced=True,
        owner_stage="M3.1",
        anchors=(
            "test_reconciliation_contract_has_no_destructive_action",
            "test_orchestration_layer_calls_no_destructive_action",
        ),
    ),
    C.Invariant(
        "P8",
        "System never reassigns work because an Agent is not ready.",
        enforced=True,
        owner_stage="M3.2",
        anchors=("test_not_ready_agent_is_reported_never_auto_reassigned",),
    ),
    C.Invariant(
        "P9",
        "Capability Gap does not trigger automatic recruitment.",
        enforced=True,
        owner_stage="M3.3",
        anchors=(
            "test_capability_gap_has_no_automatic_action",
            "test_work_layer_does_not_import_recruitment_or_market_services",
        ),
    ),
    C.Invariant(
        "P10",
        "Capability Gap actions originate from Manager/User decisions.",
        enforced=True,
        owner_stage="M3.3",
        anchors=(
            "test_every_capability_gap_action_has_a_tool_or_an_owner_stage",
            "test_capability_gap_actions_are_decision_shaped",
        ),
    ),
    C.Invariant(
        "P11",
        "Learning never directly writes competency score.",
        enforced=True,
        owner_stage="M3.3",
        anchors=("test_learning_path_does_not_write_competency_score",),
    ),
    C.Invariant(
        "P12",
        "Recruitment does not copy Person-owned assets.",
        enforced=True,
        owner_stage="M3.3",
        anchors=("test_recruitment_does_not_copy_person_owned_assets",),
    ),
    C.Invariant(
        "P13",
        "Recruitment must eventually pass through provisioning before real execution.",
        enforced=True,
        owner_stage="M3.2",
        anchors=("test_real_execution_requires_provisioning_facts",),
    ),
    C.Invariant(
        "P14",
        "Historical runtime/provider/model facts are immutable snapshots.",
        enforced=True,
        owner_stage="M3.4",
        anchors=(
            "test_provenance_fields_match_work_session_columns",
            "test_provenance_gaps_are_declared_and_owned",
        ),
    ),
    C.Invariant(
        "P15",
        "Technical retry and management replan are separate semantics.",
        enforced=True,
        owner_stage="M3.4",
        anchors=("test_retry_semantics_are_separate_and_non_overlapping",),
    ),
    C.Invariant(
        "P16",
        "Runtime events do not directly become domain truth.",
        enforced=True,
        owner_stage="M3.4",
        anchors=(
            "test_telemetry_kinds_do_not_collide_with_domain_events",
            "test_runtime_layer_does_not_write_domain_state",
        ),
    ),
    C.Invariant(
        "P17",
        "Task success requires domain completion/review, not merely runtime exit=0.",
        enforced=True,
        owner_stage="M3.0",
        anchors=("test_telemetry_never_carries_domain_status_targets",),
    ),
    C.Invariant(
        "P18",
        "Work Experience must originate from real domain facts.",
        enforced=True,
        owner_stage="M3.5",
        anchors=(
            "test_work_experience_fields_match_their_sources",
            "test_work_experience_has_a_stage_for_every_field",
        ),
    ),
    C.Invariant(
        "P19",
        "Reliability metrics are derived.",
        enforced=True,
        owner_stage="M3.6",
        anchors=("test_reliability_metrics_are_derived_and_attributable",),
    ),
    C.Invariant(
        "P20",
        "Specialization is derived from evidence/history.",
        enforced=True,
        owner_stage="M3.6",
        anchors=("test_specialization_has_no_write_path",),
    ),
    C.Invariant(
        "P21",
        "No single opaque power score becomes Agent truth.",
        enforced=True,
        owner_stage="M3.6",
        anchors=("test_no_opaque_power_score_exists",),
    ),
    C.Invariant(
        "P22",
        "Management experience must come from DecisionRecord + outcome facts.",
        enforced=True,
        owner_stage="M3.6",
        anchors=("test_management_experience_comes_from_decision_facts",),
    ),
    C.Invariant(
        "P23",
        "Current employment/runtime state must not overwrite historical career facts.",
        enforced=True,
        owner_stage="M3.5",
        anchors=("test_work_experience_history_is_snapshot_based_not_reverse_inferred",),
    ),
    C.Invariant(
        "P24",
        "Mock execution must remain distinguishable from real execution.",
        enforced=True,
        owner_stage="M3.0",
        anchors=(
            "test_execution_mode_is_total_and_binary",
            "test_runtime_type_partition_is_exhaustive_and_honest",
        ),
    ),
    C.Invariant(
        "P25",
        "Mock evidence must continue respecting existing reliability discount.",
        enforced=True,
        owner_stage="M3.10",
        anchors=("test_mock_evidence_discount_contract_matches_the_evidence_policy",),
    ),
    C.Invariant(
        "P26",
        "AgentProfile is a read model.",
        enforced=True,
        owner_stage="M3.7",
        anchors=(
            "test_agent_profile_is_a_read_model_without_a_write_path",
            "test_agent_profile_axes_are_disjoint_and_sourced",
        ),
    ),
    C.Invariant(
        "P27",
        "T2 Market remains the talent transaction source of truth.",
        enforced=True,
        owner_stage="M3.7",
        anchors=("test_market_transaction_truth_is_not_redefined_in_work_layer",),
    ),
    C.Invariant(
        "P28",
        "M2 Project/Task/Decision/DAG contracts remain frozen.",
        enforced=True,
        owner_stage="M3.10",
        anchors=("test_m2_frozen_contracts_are_untouched_by_m3",),
    ),
    C.Invariant(
        "P29",
        "System detects execution problems; Agents decide business responses.",
        enforced=True,
        owner_stage="M3.4",
        anchors=("test_detection_helpers_are_not_decision_helpers",),
    ),
    C.Invariant(
        "P30",
        "High-impact actions must respect Authority and Autonomy independently.",
        enforced=True,
        owner_stage="M3.9",
        anchors=("test_authority_and_autonomy_are_independent_axes",),
    ),
    C.Invariant(
        "P31",
        "Provisioning steps and readiness items must describe the same environment facts.",
        enforced=True,
        owner_stage="M3.0",
        anchors=("test_provisioning_steps_match_required_execution_facts",),
    ),
    C.Invariant(
        "P32",
        "Every declared readiness item must name a real table/column source.",
        enforced=True,
        owner_stage="M3.0",
        anchors=(
            "test_readiness_item_sources_resolve_to_real_tables",
            "test_readiness_items_map_onto_production_stages",
        ),
    ),
    C.Invariant(
        "P33",
        "A declared capability-gap action must have either a live tool or an owner stage.",
        enforced=True,
        owner_stage="M3.3",
        anchors=("test_every_capability_gap_action_has_a_tool_or_an_owner_stage",),
    ),
    C.Invariant(
        "P34",
        "Provenance gaps are explicitly owned; repaid gaps require behavioral evidence.",
        enforced=True,
        owner_stage="M3.0",
        anchors=(
            "test_provenance_gaps_are_declared_and_owned",
            "test_every_declared_gap_is_owned_by_a_real_stage",
        ),
    ),
    C.Invariant(
        "P35",
        "Telemetry vocabulary must not collide with frozen domain event vocabulary.",
        enforced=True,
        owner_stage="M3.0",
        anchors=("test_telemetry_kinds_do_not_collide_with_domain_events",),
    ),
    C.Invariant(
        "P36",
        "M3 must not introduce a second project/task/DAG container.",
        enforced=True,
        owner_stage="M3.10",
        anchors=("test_no_second_project_or_graph_container_exists",),
    ),
)


# ---------------------------------------------------------------------------
# 模块级一致性断言（契约自检；与 `contracts.py` 同款纪律）
# ---------------------------------------------------------------------------

# P31：编排步骤 == 真实执行必答项（逐字，含顺序）
assert PROVISIONING_STEP_ORDER == C.READINESS_REQUIRED_FOR_EXECUTION["real"], (
    "Provisioning 步骤与就绪必答项不一致：配了但不检（或反过来）就是漂移口（P31）"
)

# P32：就绪项与阶段映射双向一致
assert set(READINESS_ITEM_STAGE) == set(C.READINESS_FACT_SOURCES), (
    "就绪项与生产阶段映射不一致：新增就绪项必须同时声明它属于哪一层（P32）"
)

# P24：运行时类型划分必须穷尽且互斥（mock / real / placeholder）
assert {MOCK_RUNTIME_TYPE} | set(REAL_RUNTIME_TYPES) | set(PLACEHOLDER_RUNTIME_TYPES) == {
    item.value for item in RuntimeType
}, "运行时类型划分既不穷尽也不互斥 —— 新增类型必须显式登记归属（P24）"
assert not ({MOCK_RUNTIME_TYPE} & (set(REAL_RUNTIME_TYPES) | set(PLACEHOLDER_RUNTIME_TYPES))), (
    "mock 不可能同时是真实或 placeholder"
)

# P34：欠账必须显式、owner 合法；已偿还的债务不能作为占位保留。
assert not set(PROVENANCE_PRESENT_FIELDS) & set(PROVENANCE_OWED_FIELDS)
assert set(PROVENANCE_PRESENT_FIELDS) | set(PROVENANCE_OWED_FIELDS) == set(
    PROVENANCE_SNAPSHOT_FIELDS
), "provenance 字段全集与 现存/欠账 两半不一致（P34）"

# P3：能力类键必须已经被运行时策略禁止
assert CAPABILITY_KEYS_THAT_MUST_BE_FORBIDDEN <= C.FORBIDDEN_RUNTIME_POLICY_KEYS, (
    "有能改变能力语义的键没被运行时策略禁止（P3）："
    f"{sorted(CAPABILITY_KEYS_THAT_MUST_BE_FORBIDDEN - C.FORBIDDEN_RUNTIME_POLICY_KEYS)}"
)

# P9：自动动作必须为空
assert not AUTO_CAPABILITY_ACTIONS, "能力缺口的响应不允许自动动作（P9）"

# P33：动作词表不允许幽灵条目
for _action in CAPABILITY_GAP_ACTIONS:
    _tools = CAPABILITY_GAP_ACTION_TOOLS[_action]
    _owner = CAPABILITY_GAP_ACTION_OWNER[_action]
    assert bool(_tools) or (_owner in M3_STAGES), (
        f"能力缺口动作 {_action.value} 既没有工具也没有 owner 阶段（幽灵词条，P33）"
    )
    assert not (_tools and _owner), f"{_action.value} 同时声明了工具与 owner 阶段（P33）"
assert set(CAPABILITY_GAP_ACTION_TOOLS) == set(CAPABILITY_GAP_ACTIONS)
assert set(CAPABILITY_GAP_ACTION_OWNER) == set(CAPABILITY_GAP_ACTIONS)

# P35：遥测词表与领域事件词表不得重叠
assert not (set(RUNTIME_TELEMETRY_KINDS) & (C.FACT_EVENTS | C.DECISION_NEEDED_EVENTS)), (
    "遥测事件与领域事件重名 —— 执行环境的噪音会伪装成业务真相（P16/P35）"
)

# P15：两套重试语义必须是两个不同的 owner
assert TECHNICAL_RETRY_OWNER != MANAGEMENT_REPLAN_OWNER, (
    "技术重试与管理 replan 必须是两个语义（P15）"
)

# P18：履历字段与来源映射必须一一对应（现存 + 欠账）
assert set(WORK_EXPERIENCE_SOURCES) | set(WORK_EXPERIENCE_OWED_FIELDS) == set(
    WORK_EXPERIENCE_FIELDS
), "履历字段与来源映射不一致：新增字段必须登记来源或 owner 阶段（P18）"

# P26：AgentProfile 两轴不得重叠
assert not (set(AGENT_PROFILE_LONG_TERM) & set(AGENT_PROFILE_CURRENT_STATE)), (
    "长期资产与当前运行态混在一起 —— 当前状态会覆盖历史（P23/P26）"
)
assert set(AGENT_PROFILE_SOURCES) == set(AGENT_PROFILE_LONG_TERM) | set(
    AGENT_PROFILE_CURRENT_STATE
), "AgentProfile 字段与来源映射不一致（P26）"
