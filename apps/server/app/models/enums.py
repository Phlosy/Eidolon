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
