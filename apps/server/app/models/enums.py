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
