"""M2.2 工作与组织运行域的**读模型**（RoleContext / Authority / Role Resources）。

只做序列化：不查库、不判断、不排序建议。所有字段都是
`app/work/role_context.py` 与 `app/work/authority.py` 的事实投影。

**C5 纪律**：RoleContext 响应不含任何 score / level / rank ——
期望只给引用（competency code + required/preferred + critical），分值留在岗位画像里
按需另读。`tests/test_m2_role_context.py` 会对序列化结果做递归键名扫描。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class AuthorityGrantOut(BaseModel):
    """一条生效授权（与 `position_authority_grants` 字段一一对应）。"""

    kind: str
    scope_kind: str
    scope_ref: int = 0
    max_amount: int | None = None
    grant_id: int | None = None


class PositionExpectationRefOut(BaseModel):
    """期望的**引用**（不含分值；分值请读岗位画像）。"""

    competency_code: str
    requirement_type: str = "required"
    critical: bool = False


class RoleResourceOut(BaseModel):
    """Role Resource Index 的一项 + 解析结果。

    `resolution`：
      - `resolved` —— `pointer` 指向既有内容/配置（`knowledge_item:` / `drive_node:` /
        `company_setting:`）；
      - `advisory` —— 按设计不指向内容（`skill_hint`）；
      - `missing` —— 指针目标尚不存在（公司还没发布对应知识/手册）。
    """

    kind: str
    ref: str
    note: str = ""
    required: bool = False
    resolution: str
    pointer: str = ""


class RoleContextOut(BaseModel):
    """履职上下文（派生读模型，不落表 —— M2-ADR-4）。"""

    person_id: int | None = None
    employee_id: int
    position_definition_id: int | None = None
    position_code: str | None = None
    department_id: int | None = None
    responsibilities: list[str] = []
    authority: list[AuthorityGrantOut] = []
    expectations: list[PositionExpectationRefOut] = []
    advisory_scope: list[str] = []
    resource_index: list[RoleResourceOut] = []
    direct_reports: list[int] = []
    company_policy_keys: list[str] = []
    current_project_ids: list[int] = []
    knowledge_scopes: list[str] = []
    #: 契约版本（`app/work/contracts.AUTHORITY_SNAPSHOT_VERSION` 同族）；
    #: 变更字段含义时 bump，读面才知道自己拿到的语义
    context_version: int = Field(default=1)


class AuthorityDecisionOut(BaseModel):
    """授权校验结果（只回答"在不在授权内"）。"""

    allowed: bool
    kind: str
    reason: str
    actor_employee_id: int
    position_definition_id: int | None = None
    grant_ids: list[int] = []
    grants_hash: str = ""
    evaluated_at: datetime | None = None


class RoleProjectBriefOut(BaseModel):
    """ "这个职位现在该关心哪些项目"的事实摘要（细节去项目读面）。"""

    project_id: int
    name: str
    status: str
    work_mode: str | None = None
    requirement_count: int = 0


class RoleContextPageOut(BaseModel):
    """`GET /employees/{id}/role-context` 的完整响应。"""

    context: RoleContextOut
    resources: list[RoleResourceOut] = []
    live_projects: list[RoleProjectBriefOut] = []
    #: 当前这个人的职位是否持有管理授权（事实；**不**表示"能不能做某件事"）
    has_management_authority: bool = False
    authority_grant_count: int = 0


# ---------------------------------------------------------------------------
# M2.4 · 管理决策读面（DecisionRecord + ToolAudit）
#
# 三层不混（DR1）：这里第一层给**管理语义**（为什么），
# `actions[]` 只给执行事实的**定位信息**（tool/outcome/AuditId），
# 完整入参出参走 `/decisions/{id}/tool-audits` —— 决策读面不该变成审计转储（DR5）。
# ---------------------------------------------------------------------------


class DecisionActionRefOut(BaseModel):
    """一条执行事实的定位信息（不含入参出参）。"""

    audit_id: int
    tool_name: str
    outcome: str
    decision_semantics: str
    authority_allowed: bool | None = None
    created_at: datetime | None = None


class DecisionActionSummaryOut(BaseModel):
    """派生计数（不落列 —— 存下来就是第二份真相，DR5）。"""

    total: int = 0
    applied: int = 0
    by_outcome: dict[str, int] = {}


class DecisionOut(BaseModel):
    decision_id: int
    company_id: int
    actor_person_id: int | None = None
    actor_employee_id: int
    acting_position_assignment_id: int | None = None
    acting_position_definition_id: int | None = None
    acting_position_code: str | None = None
    decision_type: str
    scope: str
    project_id: int | None = None
    task_id: int | None = None
    reason: str = ""
    intended_outcome: str = ""
    status: str
    outcome_note: str = ""
    parent_decision_id: int | None = None
    superseded_by_id: int | None = None
    context_version: int = 1
    context_hash: str = ""
    context: dict = {}
    #: 决策**当时**的授权快照（凭据，不是通行证 —— DR4）
    authority_at_decision: dict = {}
    action_summary: DecisionActionSummaryOut = DecisionActionSummaryOut()
    actions: list[DecisionActionRefOut] = []
    child_decision_ids: list[int] = []
    created_at: datetime | None = None
    resolved_at: datetime | None = None


class ToolAuditOut(BaseModel):
    """一次工具调用的**执行事实**（完整留档）。"""

    audit_id: int
    tool_name: str
    decision_id: int | None = None
    outcome: str
    side_effect: str
    decision_semantics: str
    autonomy: str
    transport: str
    origin: str
    actor_employee_id: int | None = None
    actor_person_id: int | None = None
    actor_company_id: int | None = None
    work_session_id: int | None = None
    task_id: int | None = None
    project_id: int | None = None
    arguments: dict = {}
    arguments_digest: str = ""
    authority_allowed: bool | None = None
    authority_reason: str = ""
    authority_grant_ids: list[int] = []
    authority_grants_hash: str = ""
    result: dict | None = None
    error: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None


class DecisionStatsOut(BaseModel):
    """决策观测（只读）。"""

    decisions_by_status: dict[str, int] = {}
    tool_audits_by_outcome: dict[str, int] = {}
    tool_audits_by_decision_semantics: dict[str, int] = {}


# ---------------------------------------------------------------------------
# M2.6 Artifact Handoff（设计 §14c，H1–H8）
#
# 事实形状与 `app/work/handoff.py` 的数据类一一对应 —— 那份 dataclass 是唯一口径，
# 这里只是它的 HTTP 读面（T2/T11：读面不自己写查询）。
# ---------------------------------------------------------------------------


class ArtifactRefOut(BaseModel):
    """一个产物的引用 + 归属（内容按需读）。"""

    artifact_id: int
    title: str
    doc_type: str
    task_id: int | None = None
    task_title: str | None = None
    work_session_id: int | None = None
    version: int
    sha256: str


class InputArtifactOut(ArtifactRefOut):
    """交给下游的输入：多一段**有界**内容摘要（H6）。"""

    source_task_id: int = 0
    source_task_title: str = ""
    excerpt: str = ""


class ConsumedArtifactOut(BaseModel):
    """使用事实：谁在哪次会话用掉了它（H3/G5）。"""

    artifact_id: int
    title: str
    doc_type: str
    task_id: int
    task_title: str | None = None
    work_session_id: int | None = None
    actor_employee_id: int | None = None
    reason: str
    created_at: str


class DeclaredInputOut(BaseModel):
    """输入声明 + 它当前的事实状态（不做判断）。"""

    source_task_id: int
    source_task_title: str
    source_task_status: str
    artifact_count: int
    ready: bool


class LineageHopOut(BaseModel):
    """上游链上的一跳（`depth` = 离查询目标的距离）。"""

    depth: int
    task_id: int
    task_title: str
    task_status: str
    artifact_id: int
    artifact_title: str
    doc_type: str


class TaskArtifactReportOut(BaseModel):
    """`GET /tasks/{id}/artifacts`：产出 / 使用 / 声明 / 上游链。"""

    task_id: int
    project_id: int | None = None
    produces: list[str] = []
    produced: list[ArtifactRefOut] = []
    consumed: list[ConsumedArtifactOut] = []
    declared_inputs: list[DeclaredInputOut] = []
    inputs: list[InputArtifactOut] = []
    upstream: list[LineageHopOut] = []
    missing_input_sources: list[int] = []
    self_artifacts_are_consumable: bool = False


class TaskInputsIn(BaseModel):
    """声明输入（人类管理动作；与 Agent 工具 `create_task.consumes` 同一服务）。"""

    source_task_ids: list[int]


class TaskInputsOut(BaseModel):
    task_id: int
    declared_inputs: list[DeclaredInputOut] = []
