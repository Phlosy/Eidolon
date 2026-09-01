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


class ImageCompatibility(StrEnum):
    verified = "verified"
    unverified = "unverified"
    unknown = "unknown"


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
