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
