"""Shared string enums (frontend/backend contract). See docs/architecture.md §3.3."""

from enum import StrEnum


class EmployeeRole(StrEnum):
    ceo = "ceo"
    product_manager = "product_manager"
    researcher = "researcher"
    engineer = "engineer"
    qa_engineer = "qa_engineer"


class EmployeeStatus(StrEnum):
    offline = "offline"
    idle = "idle"
    working = "working"
    researching = "researching"
    learning = "learning"
    reflecting = "reflecting"
    meeting = "meeting"
    error = "error"


class RuntimeType(StrEnum):
    mock = "mock"
    hermes = "hermes"
    openclaw = "openclaw"
    codex = "codex"
    claude_code = "claude_code"
    opencode = "opencode"
    custom = "custom"


class ProjectStatus(StrEnum):
    requested = "requested"
    planning = "planning"
    in_progress = "in_progress"
    in_review = "in_review"
    completed = "completed"
    cancelled = "cancelled"
    rejected = "rejected"
    #: M2.1（B11）：管理职责空缺/不可用 —— 系统**不替公司规划**，如实停在等待。
    #: 与 `requested` 的区别：requested = 已受理待管理动作；
    #: waiting_for_management = **连负责人都没有**（Work Intake 责任无人承担）。
    waiting_for_management = "waiting_for_management"


class MilestoneStatus(StrEnum):
    pending = "pending"
    in_progress = "in_progress"
    completed = "completed"


class TaskStatus(StrEnum):
    backlog = "backlog"
    todo = "todo"
    in_progress = "in_progress"
    in_review = "in_review"
    done = "done"
    failed = "failed"
    rejected = "rejected"
    # M2.3：管理 Agent 需要能诚实表达两件在此之前**没有状态可表达**的事：
    #   blocked   —— 卡住了（等外部输入 / 等依赖 / 等人），不是"跑失败"
    #   cancelled —— 被管理层取消（计划变了），不是"被评审拒绝"
    # 两者都不是终态的成功，因此不产生证据（EvidencePipeline 只消费 done/failed）。
    # 也不需要迁移：status 是字符串列，加值不改表。
    blocked = "blocked"
    cancelled = "cancelled"


class TaskKind(StrEnum):
    order_review = "order_review"
    planning = "planning"
    research = "research"
    development = "development"
    testing = "testing"
    final_review = "final_review"
    general = "general"


class ArtifactType(StrEnum):
    prd = "prd"
    research_report = "research_report"
    architecture = "architecture"
    source_code = "source_code"
    test_report = "test_report"
    readme = "readme"
    release = "release"
    plan = "plan"
    other = "other"


class ArtifactStatus(StrEnum):
    draft = "draft"
    in_review = "in_review"
    approved = "approved"
    rejected = "rejected"


class KnowledgeScope(StrEnum):
    private = "private"
    department = "department"
    company = "company"


class KnowledgeStatus(StrEnum):
    active = "active"
    proposed = "proposed"
    rejected = "rejected"


class WorkSessionStatus(StrEnum):
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class LearningKind(StrEnum):
    reflection = "reflection"
    research = "research"
    # 人格派生的“未解问题 / 待验证假设”（§11）：solution 永远为空、confidence 永远 0.0。
    question = "question"


class SkillValidationStatus(StrEnum):
    candidate = "candidate"
    validated = "validated"
    deprecated = "deprecated"


# ---------- v0.2 (persistent workforce) ----------


class ProviderType(StrEnum):
    openai = "openai"
    anthropic = "anthropic"
    openrouter = "openrouter"
    deepseek = "deepseek"
    moonshot = "moonshot"  # Kimi
    zhipu = "zhipu"  # 智谱 GLM
    qwen = "qwen"  # 阿里通义（DashScope 兼容模式）
    groq = "groq"
    mistral = "mistral"
    gemini = "gemini"
    ollama = "ollama"
    custom = "custom"


class ProviderScope(StrEnum):
    company = "company"
    employee = "employee"


class DeploymentMode(StrEnum):
    docker = "docker"
    process = "process"
    mock = "mock"


class RuntimeInstanceStatus(StrEnum):
    created = "created"
    starting = "starting"
    running = "running"
    idle = "idle"
    stopping = "stopping"
    stopped = "stopped"
    unhealthy = "unhealthy"
    crashed = "crashed"
    error = "error"
    deleting = "deleting"


class HealthStatus(StrEnum):
    healthy = "healthy"
    unhealthy = "unhealthy"
    starting = "starting"
    unknown = "unknown"


# ---------- v0.3 (workspace / drive) ----------


class DriveNodeKind(StrEnum):
    folder = "folder"
    document = "document"


class DriveZone(StrEnum):
    projects = "projects"
    knowledge = "knowledge"
    skills = "skills"
    handbook = "handbook"


class CollaboratorRole(StrEnum):
    viewer = "viewer"
    editor = "editor"


class ImageCompatibility(StrEnum):
    verified = "verified"
    unverified = "unverified"
    unknown = "unknown"


class GitPlatformType(StrEnum):
    gitlab = "gitlab"
    gitea = "gitea"
    github = "github"  # GitHub Enterprise (self-hosted)
    custom = "custom"


class GitBuiltinStatus(StrEnum):
    not_installed = "not_installed"
    installing = "installing"
    stopped = "stopped"
    running = "running"
    error = "error"


class ImageUpdateStatus(StrEnum):
    idle = "idle"
    checking = "checking"
    available = "available"
    downloading = "downloading"
    updating = "updating"
    verifying = "verifying"
    completed = "completed"
    failed = "failed"
    rolled_back = "rolled_back"


# ---------- v0.4 (employee lifecycle) ----------


# ------------------------------------------------------------------ 职位/编制/任职
# 领域设计见 docs/position-system.md；拍板记录见 docs/workforce-domain-refactor.md §11。


class TemplateScope(StrEnum):
    """职位模板来源。内置模板被公司采用时**复制**成 company 行，不共享可变行。"""

    system = "system"
    company = "company"


class SlotAdministrativeStatus(StrEnum):
    """编制的**行政态**：只有人/业务决定的四个值可以入库。

    `VACANT` / `OCCUPIED` 故意不在这里 —— 它们是占用态（`OccupancyStatus`），
    由“有无生效 PRIMARY 任职”派生（ADR-2）：写入口不存在，所以
    “库里写 VACANT 而实际有人任职”这个漂移场景结构上不可表达。
    """

    planned = "planned"
    active = "active"
    frozen = "frozen"
    closed = "closed"


class OccupancyStatus(StrEnum):
    """编制的**占用态**：只用于 API/UI 展示，绝不入库。"""

    vacant = "vacant"
    occupied = "occupied"
    frozen = "frozen"
    closed = "closed"


class AssignmentType(StrEnum):
    """任职类型。MVP 只开 `primary`；部分唯一索引只约束 primary，
    所以将来接代理/兼任不需改表。"""

    primary = "primary"
    acting = "acting"
    temporary = "temporary"
    secondary = "secondary"


class AssignmentStatus(StrEnum):
    """复用 `employments.employment_status` 语义（不新增同义列 ⇒ 不会两个真相）。"""

    active = "active"
    closed = "closed"
    superseded = "superseded"


class WorkforceStatus(StrEnum):
    """由 `WorkforceStatusResolver` 读时派生，**不入库**（ADR-4）。

    `available` 是合法常态：已入册、人级资源就绪、暂无主职。
    """

    recruiting = "recruiting"
    onboarding = "onboarding"
    available = "available"
    assigned = "assigned"
    transferring = "transferring"
    suspended = "suspended"
    offboarding = "offboarding"
    offboarded = "offboarded"


class LifecycleStatus(StrEnum):
    """Employee lifecycle (docs/design-v0.4-lifecycle.md §1). Distinct from
    EmployeeStatus, which is the moment-to-moment work state."""

    pending = "pending"
    onboarding = "onboarding"
    active = "active"
    transferring = "transferring"
    suspended = "suspended"
    offboarding = "offboarding"
    offboarded = "offboarded"


class ResourceType(StrEnum):
    workspace = "workspace"
    docs = "docs"
    git = "git"


class ResourceAccountStatus(StrEnum):
    pending = "pending"
    provisioning = "provisioning"
    active = "active"
    suspended = "suspended"
    failed = "failed"
    deprovisioning = "deprovisioning"
    deprovisioned = "deprovisioned"


class EntitlementType(StrEnum):
    role = "role"
    group = "group"
    permission = "permission"
    resource_access = "resource_access"


class PackageSource(StrEnum):
    manual = "manual"
    role = "role"
    # P4d 职位层：随 assignment 生效/结束而 ADD/REMOVE（docs/position-system.md §4）。
    # 刻意与 role 分开：`role` 是**人级**遗留映射，`position` 才跟着编制走。
    position = "position"
    project = "project"


class ProvisioningJobKind(StrEnum):
    onboarding = "onboarding"
    transfer = "transfer"
    permission_change = "permission_change"
    suspension = "suspension"
    resumption = "resumption"
    offboarding = "offboarding"


class ProvisioningJobStatus(StrEnum):
    pending = "pending"
    running = "running"
    done = "done"
    partial = "partial"  # some steps failed; employee keeps previous lifecycle state
    failed = "failed"


class ProvisioningStepStatus(StrEnum):
    pending = "pending"
    running = "running"
    done = "done"
    failed = "failed"
    skipped = "skipped"


# ---------- v0.5 (guided company + project delivery lifecycle) ----------


class ProjectPhaseType(StrEnum):
    initiation = "initiation"
    requirements_analysis = "requirements_analysis"
    requirements_review = "requirements_review"
    system_design = "system_design"
    system_design_review = "system_design_review"
    development = "development"
    internal_testing = "internal_testing"
    user_acceptance_testing = "user_acceptance_testing"
    acceptance_review = "acceptance_review"
    delivery = "delivery"
    project_archive = "project_archive"


class ProjectPhaseStatus(StrEnum):
    pending = "pending"
    ready = "ready"
    in_progress = "in_progress"
    waiting_review = "waiting_review"
    changes_requested = "changes_requested"
    approved = "approved"
    completed = "completed"
    blocked = "blocked"


class ReviewType(StrEnum):
    requirements_review = "requirements_review"
    design_review = "design_review"
    acceptance_review = "acceptance_review"
    custom_review = "custom_review"


class ReviewStatus(StrEnum):
    preparing = "preparing"
    waiting_for_customer = "waiting_for_customer"
    completed = "completed"


class ReviewDecision(StrEnum):
    approved = "approved"
    conditionally_approved = "conditionally_approved"
    changes_requested = "changes_requested"
    rejected = "rejected"


class DocumentCategory(StrEnum):
    internal = "internal"
    formal = "formal"
    review = "review"
    delivery = "delivery"
    source = "source"
    build = "build"
    test = "test"


class BaselineType(StrEnum):
    requirements = "requirements"
    design = "design"
    acceptance = "acceptance"


class ChangeRequestStatus(StrEnum):
    draft = "draft"
    impact_analysis = "impact_analysis"
    waiting_approval = "waiting_approval"
    approved = "approved"
    implementing = "implementing"
    regression_test = "regression_test"
    closed = "closed"
    rejected = "rejected"
    cancelled = "cancelled"


class TutorialStatus(StrEnum):
    not_started = "not_started"
    active = "active"
    paused = "paused"
    skipped = "skipped"
    completed = "completed"


# ---------- P5: Talent Profile & Competency Foundation ----------


class CompetencyKind(StrEnum):
    """能力目录类型：通用能力（人人适用的 10 维）与专业能力（按领域扩展）。"""

    general = "general"
    professional = "professional"


class CompetencyStatus(StrEnum):
    """员工能力行状态。`unrated` 不落行 —— 无证据的维度由查询/序列化呈现为 unrated
    （score=null），行内只可能出现 provisional / assessed / stale。"""

    unrated = "unrated"
    provisional = "provisional"
    assessed = "assessed"
    stale = "stale"


class EvidenceSourceKind(StrEnum):
    """能力证据来源类型（docs/competency-system.md §6）。值全小写，与仓库枚举风格一致。

    P6 集中维护：任何新来源必须在这里登记，不允许业务模块散落字符串。
    对应 spec（docs/evidence-pipeline.md）的 TASK/PROJECT/TEST/REVIEW/ARTIFACT/
    USER_FEEDBACK/PEER_REVIEW/ASSESSMENT/LEARNING/SKILL_USAGE。
    """

    task = "task"
    project = "project"
    test = "test"
    review = "review"
    artifact = "artifact"
    user_feedback = "user_feedback"
    peer_review = "peer_review"
    assessment = "assessment"
    learning = "learning"
    skill_usage = "skill_usage"
    # T1.1（cultivation-system-design §2 D4）：教育证据分级来源。
    # 只由培养引擎直接写（upsert_evidence），**不进** pipeline collectors 的
    # 采集清单 —— 公司员工的正式考核链在结构上采不到它们（隔离守卫钉死）。
    edu_course = "edu_course"
    edu_exam = "edu_exam"
    edu_project = "edu_project"
    edu_internship = "edu_internship"
    edu_competition = "edu_competition"


class ExpectationRole(StrEnum):
    """工作项对能力的期望角色（docs/evidence-pipeline.md §八）：

    - PRIMARY：该项主要验证的能力，Evidence 权重更高；
    - SUPPORTING：辅助佐证；
    - OPTIONAL：只在真实 Evidence 出现时才采纳（不会凭空造证据）。
    """

    primary = "primary"
    supporting = "supporting"
    optional = "optional"


class AssessmentTriggerType(StrEnum):
    """Assessment 触发类型。第一版实现 automatic / project_end；其余仅预留。"""

    automatic = "automatic"
    project_end = "project_end"
    manual = "manual"
    periodic = "periodic"
    promotion = "promotion"
    position_change = "position_change"
    position_fit = "position_fit"


# ---------- P10: Career & Talent Development ----------


class CareerEventType(StrEnum):
    """职业履历审计事件（CareerEvent=发生过什么的记录；当前任职仍来自 PositionAssignment）。"""

    joined = "joined"
    position_assigned = "position_assigned"
    position_released = "position_released"
    transferred = "transferred"
    promoted = "promoted"
    demoted = "demoted"
    acting_assigned = "acting_assigned"
    acting_ended = "acting_ended"
    suspended = "suspended"
    resumed = "resumed"
    assessment_completed = "assessment_completed"
    development_plan_created = "development_plan_created"
    development_plan_completed = "development_plan_completed"


class CareerTransitionType(StrEnum):
    """职业路径上 from→to 的发展类型。"""

    promotion = "promotion"
    lateral = "lateral"
    specialization = "specialization"
    management = "management"
    cross_functional = "cross_functional"


class DevelopmentPlanStatus(StrEnum):
    draft = "draft"
    active = "active"
    paused = "paused"
    completed = "completed"
    cancelled = "cancelled"


class DevelopmentItemStatus(StrEnum):
    planned = "planned"
    in_progress = "in_progress"
    waiting_evidence = "waiting_evidence"
    completed = "completed"
    cancelled = "cancelled"


class DevelopmentNeedType(StrEnum):
    competency_gap = "competency_gap"
    target_gap = "target_gap"
    evidence_gap = "evidence_gap"
    skill_gap = "skill_gap"
    experience_gap = "experience_gap"
    assessment_gap = "assessment_gap"


# ---------- P11: Behavioral Intelligence & Autonomous Learning ----------


class LearningSessionStatus(StrEnum):
    planned = "planned"
    waiting_budget = "waiting_budget"
    running = "running"
    paused = "paused"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class LearningSourceType(StrEnum):
    development_plan = "development_plan"
    learning_priority = "learning_priority"
    project_need = "project_need"
    repeated_failure = "repeated_failure"
    skill_candidate = "skill_candidate"
    competency_gap = "competency_gap"
    employee_interest = "employee_interest"
    curiosity = "curiosity"
    manual = "manual"


class LearningMode(StrEnum):
    web_research = "web_research"
    knowledge_review = "knowledge_review"
    practice = "practice"
    document_study = "document_study"


class KnowledgeFreshness(StrEnum):
    fresh = "fresh"
    stale = "stale"


class CultivationState(StrEnum):
    """培养状态轴（T2 设计 §4，唯一载体 = `character_profiles.lifecycle`）。

    **只允许这两个值**：`listed` / `hired` 曾是预留值，T2.0 起废弃 ——
    市场可发现性走 `market_listings`（T2.3），任职走 `employments`，
    同一列不再被三个语义争用（守卫测试：tests/test_market_contract.py）。
    """

    cultivating = "cultivating"  # 培养中（含自由养成）
    ready = "ready"  # 养成完成：模板走完全阶段（自动）/ 自由养成显式结业


class TalentOrigin(StrEnum):
    """角色来源（`character_profiles.origin`）：愿景 §2 的两类来源 + T1 的自由起点。"""

    issued = "issued"  # 官方发行（T2.4 发行方生成器写入）
    trained = "trained"  # 玩家自训（可挂模板）
    blank = "blank"  # 空白起点，自由养成


class EmploymentState(StrEnum):
    """任职轴（T2 设计 §4.1，**派生不落库**）：由 `employments` 是否存在
    `effective_to IS NULL` 的 primary 行推出（不新增同义状态列）。"""

    unemployed = "unemployed"
    employed = "employed"


class MarketListingStatus(StrEnum):
    """挂牌行状态（T2.3 落库；一次"在市"= 一行 active）。

    与 `MarketState`（app/talent/market/contracts.py，派生不落库的三值视图）配对：
    这里只表达挂牌行自身的两值生命周期（挂牌 → 关闭）。
    """

    active = "active"
    closed = "closed"


class MarketParticipantKind(StrEnum):
    """市场参与者类型（T2 设计 D8）：玩家公司 / NPC 公司 / 系统发行方。

    NPC 公司**不写进 `companies`**（避免污染公司作用域读面）；
    player_company 行通过 `company_id` 指向真实公司。
    """

    player_company = "player_company"
    npc_company = "npc_company"
    system_issuer = "system_issuer"


# ---------------------------------------------------------------------------
# M1 经济域（docs/m1-economy-design.md；枚举值 = **冻结契约**，改动走设计评审）
# ---------------------------------------------------------------------------


class Currency(StrEnum):
    """货币。v1 只有 CREDIT（整数最小单位，`minor_unit_scale = 1`）。

    金额一律整数（E21）；货币必须显式（E22）——不做多币种兑换（设计 §13）。
    """

    credit = "CREDIT"


class EconomicActorKind(StrEnum):
    """经济主体类型（设计 §9）。**账户不写死 company_id** —— 用 (kind, ref) 表达。"""

    system = "system"  # 系统账户（发行/财政/销毁/Escrow 托管）
    user = "user"  # 个人钱包（users.id）
    company = "company"  # 公司钱包（companies.id）
    npc_company = "npc_company"  # NPC 公司（market_participants.id；不进 companies）


class SystemAccountKind(StrEnum):
    """系统账户（设计 §8/§23）：唯一 mint 源、财政池、永久销毁、Escrow 托管。"""

    issuance = "issuance"
    treasury = "treasury"
    burn = "burn"
    escrow = "escrow"


class LedgerAccountKind(StrEnum):
    """账户类型（设计 §10）。normal_side 由类型派生（见 economy/contracts.py）。"""

    actor = "actor"
    issuance = "issuance"
    treasury = "treasury"
    burn = "burn"
    escrow = "escrow"


class LedgerEntryDirection(StrEnum):
    """复式记账方向（E3：Σdebit = Σcredit）。"""

    debit = "debit"
    credit = "credit"


class LedgerAccountStatus(StrEnum):
    """账户状态（设计 §10，M1.1）：frozen 不可过账；closed 是终态。**账户不可删除**。"""

    active = "active"
    frozen = "frozen"
    closed = "closed"


class LedgerTransactionStatus(StrEnum):
    """交易状态（设计 §12）：v1 只有 posted。

    reversed 由未来 reversal 流程标记 —— E17：金额与 entries 永不修改。
    """

    posted = "posted"
    reversed = "reversed"


class TransactionKind(StrEnum):
    """交易类型（M1.0 冻结的腿组合见 economy/contracts.py::LEG_BLUEPRINTS）。"""

    mint = "mint"  # 发行：Debit 收款人 / Credit ISSUANCE
    transfer = "transfer"  # 转移：Debit 收款人 / Credit 付款人
    burn = "burn"  # 销毁：Debit BURN / Credit 付款人
    treasury_transfer = "treasury_transfer"  # 财政：Debit TREASURY / Credit 付款人
    escrow_fund = "escrow_fund"  # 锁资：Debit ESCROW / Credit 出资人
    escrow_release = "escrow_release"  # 释放：Debit 收款人 / Credit ESCROW
    escrow_refund = "escrow_refund"  # 退款：Debit 出资人 / Credit ESCROW


class RewardType(StrEnum):
    """首批奖励类型（设计 §5/§14；政策金额见 Settings）。"""

    starter_grant = "STARTER_GRANT"
    profile_completion = "PROFILE_COMPLETION"
    company_profile_completion = "COMPANY_PROFILE_COMPLETION"
    tutorial_completion = "TUTORIAL_COMPLETION"
    daily_login = "DAILY_LOGIN"
    weekly_activity = "WEEKLY_ACTIVITY"
    achievement = "ACHIEVEMENT"
    milestone_reward = "MILESTONE_REWARD"
    official_bounty = "OFFICIAL_BOUNTY"
    official_contract = "OFFICIAL_CONTRACT"
    research_grant = "RESEARCH_GRANT"
    system_procurement = "SYSTEM_PROCUREMENT"
    event_reward = "EVENT_REWARD"
    recovery_grant = "RECOVERY_GRANT"


class RewardStatus(StrEnum):
    """奖励状态机（设计 §37）：ELIGIBLE → CLAIMED → POSTED（可 VOID）。"""

    eligible = "ELIGIBLE"
    claimed = "CLAIMED"
    posted = "POSTED"
    void = "VOID"


class WorkOrderKind(StrEnum):
    """统一工作市场的订单类型（设计 §17/§18）。"""

    official_bounty = "OFFICIAL_BOUNTY"
    official_contract = "OFFICIAL_CONTRACT"
    player_bounty = "PLAYER_BOUNTY"
    player_contract = "PLAYER_CONTRACT"
    npc_contract = "NPC_CONTRACT"
    research_grant = "RESEARCH_GRANT"
    system_procurement = "SYSTEM_PROCUREMENT"


class FundingMode(StrEnum):
    """资金模式（设计 §17）：系统发行 / 玩家锁资 / NPC 财政。"""

    system_mint = "system_mint"
    player_escrow = "player_escrow"
    npc_treasury = "npc_treasury"


class WorkOrderStatus(StrEnum):
    """工作订单状态机（设计 §37）。"""

    draft = "DRAFT"
    open = "OPEN"
    accepted = "ACCEPTED"
    in_progress = "IN_PROGRESS"
    submitted = "SUBMITTED"
    reviewing = "REVIEWING"
    approved = "APPROVED"
    rejected = "REJECTED"
    settled = "SETTLED"
    cancelled = "CANCELLED"
    expired = "EXPIRED"
    disputed = "DISPUTED"


class EvaluationMode(StrEnum):
    """验收模式（设计 §20）：自动 / 人工 / 无需验收。"""

    auto = "auto"
    manual = "manual"
    none = "none"


class EvaluationVerdict(StrEnum):
    approved = "approved"
    rejected = "rejected"
    revise = "revise"


class OfferStatus(StrEnum):
    """Offer 状态机（设计 §22，M1.6 冻结）：被接受后**生成合同**，Offer 本身不产生资金流。"""

    open = "OPEN"
    accepted = "ACCEPTED"
    rejected = "REJECTED"
    withdrawn = "WITHDRAWN"
    expired = "EXPIRED"


class TalentSaleMode(StrEnum):
    """人才出售模式（M1.7，设计 §27）：**表达"能不能还价"**，不重复 `negotiable` 字段。

    - `buyout`：一口价 —— 买方按标价出价即**立即成交**（卖方无需再操作）；
    - `negotiation`：可议价 —— 买方出价后等卖方接受/拒绝。
    """

    buyout = "buyout"
    negotiation = "negotiation"


class ContractType(StrEnum):
    """通用合同类型（设计 §21）：工作/人才/服务/采购/科研共用一个核心。"""

    work = "work"
    talent = "talent"
    service = "service"
    procurement = "procurement"
    research = "research"


class ContractStatus(StrEnum):
    """合同状态机（设计 §37）。"""

    draft = "DRAFT"
    pending_acceptance = "PENDING_ACCEPTANCE"
    active = "ACTIVE"
    funded = "FUNDED"
    fulfilled = "FULFILLED"
    settling = "SETTLING"
    settled = "SETTLED"
    cancelled = "CANCELLED"
    expired = "EXPIRED"
    failed = "FAILED"
    disputed = "DISPUTED"


class EscrowStatus(StrEnum):
    """Escrow 状态机（设计 §23/§37）：资金既不属于付款人也不属于收款人（E7）。"""

    unfunded = "UNFUNDED"
    funded = "FUNDED"
    released = "RELEASED"
    refunded = "REFUNDED"
    expired = "EXPIRED"


class SettlementStatus(StrEnum):
    """结算状态机（设计 §37）：PENDING → PROCESSING → COMPLETED（可 FAILED 重试）。"""

    pending = "PENDING"
    processing = "PROCESSING"
    completed = "COMPLETED"
    failed = "FAILED"


class EconomicCategory(StrEnum):
    """经营分类（设计 §25/§31）：报表与观测用；落在账本 reference/reason 语义上。"""

    starter = "STARTER"
    reward = "REWARD"
    official_income = "OFFICIAL_INCOME"
    player_income = "PLAYER_INCOME"
    talent_sale = "TALENT_SALE"
    service_sale = "SERVICE_SALE"
    hiring = "HIRING"
    talent_purchase = "TALENT_PURCHASE"
    training = "TRAINING"
    compute = "COMPUTE"
    market_fee = "MARKET_FEE"
    contract_fee = "CONTRACT_FEE"
    treasury = "TREASURY"
    burn = "BURN"
    recovery = "RECOVERY"
    npc_budget = "NPC_BUDGET"


# ---------------------------------------------------------------------------
# M2 工作与组织运行域（docs/m2-agent-work-runtime-design.md；不变量 W1–W31）
#
# 这一节的枚举**不代表任何新表**：它们是 M2 全部阶段共享的词汇表（决策边界、
# 职责面、评审结论、工作根形态、资源索引、记忆平面）。加在这里的理由与 T2
# 契约层注释一致 —— 模型与迁移要 import 它们，枚举必须有唯一家。
# ---------------------------------------------------------------------------


class ResponsibilityArea(StrEnum):
    """职责面（设计 §3.1）—— 只用于**路由建议**与默认授权归组。

    这是 **soft** 语义：它描述"公司通常把这类决策交给哪一面"，
    **不是**"只有这一面的人才能做这类决策"（W5 / W12）。
    """

    strategic = "strategic"  # 战略与经营（CEO）
    delivery = "delivery"  # 交付与技术组织（CTO / PM / Team Lead）
    quality = "quality"  # 质量与验收（QA / Reviewer）
    people = "people"  # 人员与培养（HR / 管理层）
    research = "research"  # 研究与探索
    execution = "execution"  # 一线执行


class DecisionKind(StrEnum):
    """只能由**已授权 Agent/User actor** 做出的管理决策（W3 / W15）。

    系统**不产生**这些值，只校验、落账（DecisionRecord）、执行结果。
    `SYSTEM_FACTS`（contracts）与本节值集必须互斥 —— 由契约测试钉住。
    """

    accept_project = "accept_project"
    decline_project = "decline_project"
    decompose_project = "decompose_project"
    delegate_management = "delegate_management"
    assign_task = "assign_task"
    reassign_task = "reassign_task"
    create_dependency = "create_dependency"
    request_review = "request_review"
    request_rework = "request_rework"
    mark_blocked = "mark_blocked"
    replan = "replan"
    accept_delivery = "accept_delivery"
    recruit = "recruit"
    purchase_agent = "purchase_agent"
    assign_position = "assign_position"
    release_position = "release_position"
    enroll_learning = "enroll_learning"
    offboard = "offboard"


class DecisionOutcome(StrEnum):
    """决策的**结果**（事后回填，不是事前判定；W18 / W28）。

    `pending` 是默认态：决策作出时还不知道结果，这是常态而非缺陷。
    系统**不定义**"好/坏结果"的标准 —— 它只记录业务事实的终态。
    """

    pending = "pending"
    succeeded = "succeeded"
    failed = "failed"
    superseded = "superseded"  # 被后续决策取代（例如 replan）
    withdrawn = "withdrawn"  # 决策者自己撤回


class ReviewVerdict(StrEnum):
    """**任务级技术评审**结论（M2.7，W17 / W29）。

    与另外三个面**不得互相替代**（设计 §12.1）：
      - `ReviewDecision`（阶段门，人类）
      - `EvaluationVerdict`（商业验收，管理面/确定性规则）
      - `AssessmentResult`（能力考核，统计聚合）
    四个面各有自己的对象与判定者；跨面引用必须经显式映射。
    """

    passed = "PASS"
    rework = "REWORK"
    rejected = "REJECT"
    escalated = "ESCALATE"


class ProjectWorkMode(StrEnum):
    """**产品**工作模式（设计 §11.3，D2/M2-ADR-11，W22 / W30）。

    两种模式**共用同一 `projects` 行、同一 Task 表、同一评审底座**；
    它们的差别只有一个：

        **human involvement level，不是 decision ownership。**

    两种模式里「接不接 / 怎么拆 / 选谁 / 是否返工 / 是否交付」都来自
    Manager Agent 或 Human Owner；系统都不代管。

    它**只属于产品行为**。确定性的模板执行图（`GRAPH_TEMPLATE`）不在本枚举里 ——
    它是测试/教程基础设施，见 `PlanningFixture`（D3/M2-ADR-12）。
    """

    #: 目标形态：Manager Agent 自主规划，执行前不经人类确认（W2 / W16）
    managed = "managed"
    #: 教学/协助形态：Manager 仍然自主决策，但**关键动作需要人类确认与讲解**
    guided = "guided"


class PlanningFixture(StrEnum):
    """确定性规划 fixture —— **基础设施轴，不是产品模式**（D3/M2-ADR-12，W33）。

    它不是"第三种玩法"，而是给 CI / 教程 / golden path / 开发演示用的**确定性替身**：
    让「Project → Task Graph → 执行 → Artifact → Review → Completed」这条链在
    **不依赖 LLM Manager Agent** 的前提下可重复、可断言、零成本。

    两条硬纪律（由契约测试钉死）：

    1. **生产项目绝不能隐式落到它头上**（没有"Manager 没反应 → 偷偷用模板"）；
    2. 只能**显式**请求，且受 `settings.allow_planning_fixtures` 门控
       （默认 False；测试/CI/开发环境显式打开）。
    """

    #: 生产：Task DAG 只能由 Manager Agent / Human 创建
    none = "none"
    #: 基础设施替身：用固定模板生成确定性执行图（仅教程/CI/测试/演示）
    deterministic_template = "deterministic_template"


class ResponsibilityKind(StrEnum):
    """组织责任类型（设计 §4 的职责路由，D1/M2-ADR-11，W32）。

    Position 表达"公司希望你负责什么"——系统按**责任**路由，而不是写死"CEO 特权"。

    M2.1 只开 `work_intake`（新公司的最终工作入口）。后续责任（交付管理、质量门……）
    在 M2.2/M2.4 按同一机制追加：新增一个值 + 一条 `RESPONSIBILITY_DEFAULTS` 默认，
    **不需要改路由代码**。
    """

    #: 谁负责接收工作、做高层判断与委派（默认=CEO，公司可配）
    work_intake = "work_intake"


class RoleResourceKind(StrEnum):
    """Role Resource Index 的条目类型（设计 §6）。

    全部是**建议读取/学习的引用**，不携带分值、不授予能力（W27）。
    """

    knowledge_topic = "knowledge_topic"
    playbook = "playbook"
    policy = "policy"
    handbook = "handbook"
    skill_hint = "skill_hint"


class MemoryPlane(StrEnum):
    """记忆平面（设计 §7，W9 / W10）。

    - `institutional`：随**公司**存续；换人不迁移、不丢失、不复制；
    - `personal`：随 **Person** 存续；换职位不迁移、不重置。
    """

    institutional = "institutional"
    personal = "personal"


class FactKind(StrEnum):
    """系统拥有的**事实**类别（设计 §1.1）。

    这些是系统必须能回答的问题；它们**不是决策**，也不含"应该怎么做"。
    `SYSTEM_FACTS`（contracts）是它的显式清单，与 `DecisionKind` 互斥。
    """

    position_definition = "position_definition"
    position_assignment = "position_assignment"
    person_competency = "person_competency"
    person_evidence = "person_evidence"
    person_experience = "person_experience"
    fit_result = "fit_result"
    current_load = "current_load"
    runtime_status = "runtime_status"
    provider_status = "provider_status"
    workspace_status = "workspace_status"
    budget_snapshot = "budget_snapshot"
    artifact_index = "artifact_index"
    knowledge_index = "knowledge_index"
    task_graph_state = "task_graph_state"
    task_readiness = "task_readiness"
    review_facts = "review_facts"
    workorder_state = "workorder_state"
    validation_result = "validation_result"
    decision_history = "decision_history"


# ---------------------------------------------------------------------------
# M2.2 · 管理授权（Authority Projection，设计 §4.1/§4.2，W37–W42）
# ---------------------------------------------------------------------------


class AuthorityScopeKind(StrEnum):
    """管理授权的**作用域**（M2.2）。

    刻意只有三个值 —— 用户拍板：「Authority 支持有限 scope/constraint，例如
    company、department、direct_reports 及 spend max_amount，但 M2.2 **不建设
    通用 ABAC 引擎**」。

    作用域之外的一切（时间、地点、属性表达式、策略语言）**不在 M2.2 范围**。
    """

    #: 作用域 = 自己公司内的一切（最常见；`scope_ref` 恒为 0）
    company = "company"
    #: 作用域 = 某个部门（`scope_ref` = departments.id）
    department = "department"
    #: 作用域 = 自己的汇报子树（`scope_ref` 恒为 0；子树由 position_slots.manager_slot_id 派生）
    direct_reports = "direct_reports"


# ---------------------------------------------------------------------------
# M2.3 · 管理工具面（docs/m2-agent-work-runtime-design.md §19，T1–T12）
# ---------------------------------------------------------------------------


class AuthorityKind(StrEnum):
    """**Authority**（硬边界）—— 职位被授权做什么（设计 §4.1）。

    M2.2 起它有了落库载体：`position_authority_grants.authority_kind`。
    按仓库纪律（"有宿主列的枚举住在 models/enums.py"）M2.3 把它从
    `app/work/contracts.py` 移到这里；契约层继续 re-export，导入路径不变。

    与 `ResponsibilityArea` 的区别：Authority 是**硬**的（没有它系统拒绝，W6），
    Responsibility 是**软**的（只影响路由与展示，W5）。
    """

    create_project = "create_project"
    delegate_management = "delegate_management"
    assign_task = "assign_task"
    request_rework = "request_rework"
    accept_delivery = "accept_delivery"
    approve_hiring = "approve_hiring"
    spend_credits = "spend_credits"
    assign_position = "assign_position"
    release_position = "release_position"
    offboard = "offboard"
    # M2.3：组织项目内的工作图（建任务 / 改任务 / 连依赖 / 标记阻塞 / 取消 / 请评审）。
    # 与 `assign_task` 分开是刻意的：**能不能改工作图**与**能不能把活派给某人**
    # 是两件事，公司可以只给其一（例如 PM 能建任务但不能替别人改派）。
    plan_project_work = "plan_project_work"


class ToolSideEffect(StrEnum):
    """工具副作用等级（M2.3 用户拍板 §10）。

    - `read`        —— 事实查询（可同时服务 Agent / UI / CLI，复用同一查询服务）
    - `write`       —— 普通组织工作动作（只走内部 Agent 工具执行面）
    - `high_impact` —— 经济 / 招聘 / 解雇 / 合同 / 高风险资源；**M2.3 不实现任何此类工具**，
                       但等级先冻结，配 `AutonomyLevel.requires_confirmation` 作为门禁
    """

    read = "read"
    write = "write"
    high_impact = "high_impact"


class ToolTransport(StrEnum):
    """工具调用的到达方式（M2.3 用户拍板 §12）。

    **Transport 不代表信任**（T4）：内部面照样做完整 Authority 校验。
    """

    #: Agent Runtime 内部执行面（生产路径；没有对应的玩家 HTTP 路由）
    internal = "internal"
    #: 开发者/运维调试口（默认关；`EIDOLON_AGENT_TOOL_CLI_ENABLED`）
    debug_cli = "debug_cli"


class AutonomyLevel(StrEnum):
    """**Autonomy Policy** —— AI 是否允许在无人确认下执行该动作（用户拍板 §11）。

    必须与 Authority 分开记录：

    ```text
    Authority      = 这个职位**有没有**组织权力执行该动作
    AutonomyPolicy = **AI** 是否允许在无人确认下执行该动作
    ```

    「CEO 有 `spend_credits` 权限」**不等于**「CEO AI 可以无限额度自主花钱」。
    M2.3 只**冻结这个边界**（一张副作用 → 自主等级的当前行为表），
    不实现策略引擎；未来的 `auto_allowed` / `requires_confirmation` /
    `max_auto_amount` 都挂在这一层，而不是混进 Authority。
    """

    #: 授权内即可自主执行
    auto_allowed = "auto_allowed"
    #: 必须先取得人类（Owner / 管理层）确认 —— M2.3 没有确认通道，因此**拒绝执行**
    requires_confirmation = "requires_confirmation"
    #: 任何情况下都不允许由 AI 自主执行
    forbidden = "forbidden"
