/**
 * TS types aligned with the backend schemas (docs/architecture.md §3).
 * Maintained by hand against the backend OpenAPI contract.
 * Enum string contracts are from §3.3 — do not diverge.
 */

// ---------- Enums (§3.3, shared string contracts) ----------

export type EmployeeRole = "ceo" | "product_manager" | "researcher" | "engineer" | "qa_engineer";

export type EmployeeStatus =
  "offline" | "idle" | "working" | "researching" | "learning" | "reflecting" | "meeting" | "error";

export type RuntimeType =
  "mock" | "hermes" | "openclaw" | "codex" | "claude_code" | "opencode" | "custom";

export type ProjectStatus =
  "requested" | "planning" | "in_progress" | "in_review" | "completed" | "cancelled" | "rejected";

export type MilestoneStatus = "pending" | "in_progress" | "completed";

export type TaskStatus =
  "backlog" | "todo" | "in_progress" | "in_review" | "done" | "failed" | "rejected";

export type TaskKind =
  "order_review" | "planning" | "research" | "development" | "testing" | "final_review" | "general";

export type ArtifactType =
  | "prd"
  | "research_report"
  | "architecture"
  | "source_code"
  | "test_report"
  | "readme"
  | "release"
  | "plan"
  | "other";

export type ArtifactStatus = "draft" | "in_review" | "approved" | "rejected";

export type KnowledgeScope = "private" | "department" | "company";

export type KnowledgeStatus = "active" | "proposed" | "rejected";

export type WorkSessionStatus = "running" | "completed" | "failed" | "cancelled";

export type LearningKind = "reflection" | "research";

export type SkillValidationStatus = "candidate" | "validated" | "deprecated";

export type MemoryKind = "note" | "observation" | "summary";

// ---------- Entities (§3.2) ----------

export interface Company {
  id: number;
  name: string;
  slug: string;
  description: string | null;
  industry: string | null;
  settings: Record<string, unknown>;
  departments: Department[];
  created_at: string;
  updated_at: string;
}

export interface Department {
  id: number;
  company_id: number;
  name: string;
  slug: string;
  description: string | null;
  created_at: string;
  updated_at: string;
}

export interface Employee {
  id: number;
  company_id: number;
  department_id: number;
  name: string;
  slug: string;
  role: EmployeeRole;
  title: string | null;
  avatar: string | null;
  status: EmployeeStatus;
  runtime_type: RuntimeType;
  runtime_config: Record<string, unknown>;
  workspace_path: string;
  memory_namespace: string;
  current_task_id: number | null;
  created_at: string;
  updated_at: string;
}

export interface Project {
  id: number;
  company_id: number;
  name: string;
  description: string | null;
  status: ProjectStatus;
  goal: string | null;
  owner_id: number | null;
  source_order_text: string | null;
  created_at: string;
  updated_at: string;
}

export interface Milestone {
  id: number;
  project_id: number;
  name: string;
  description: string | null;
  status: MilestoneStatus;
  order: number;
  created_at: string;
  updated_at: string;
}

export interface Task {
  id: number;
  project_id: number;
  milestone_id: number | null;
  title: string;
  description: string | null;
  kind: TaskKind;
  status: TaskStatus;
  priority: number;
  assignee_id: number | null;
  acceptance_criteria: string | null;
  sequence: number;
  // IDs of tasks that must complete before this one (n-n via task_dependencies).
  dependencies: number[];
  created_at: string;
  updated_at: string;
}

export interface Artifact {
  id: number;
  company_id: number;
  project_id: number | null;
  task_id: number | null;
  type: ArtifactType;
  title: string;
  content: string;
  path: string | null;
  version: number;
  status: ArtifactStatus;
  author_id: number | null;
  created_at: string;
  updated_at: string;
}

export interface Skill {
  id: number;
  employee_id: number;
  name: string;
  description: string | null;
  version: string;
  attempts: number;
  success_count: number;
  success_rate: number | null;
  avg_duration_sec: number | null;
  avg_cost: number | null;
  last_used_at: string | null;
  validation_status: SkillValidationStatus;
  created_at: string;
  updated_at: string;
}

export interface LearningRecord {
  id: number;
  employee_id: number;
  project_id: number | null;
  task_id: number | null;
  kind: LearningKind;
  topic: string | null;
  problem: string | null;
  observation: string | null;
  lesson: string | null;
  solution: string | null;
  confidence: number | null;
  sources: string[];
  created_at: string;
  updated_at: string;
}

export interface LearningPriority {
  id: number;
  employee_id: number;
  topic: string;
  score: number;
  reason: string | null;
  created_at: string;
  updated_at: string;
}

export interface MemoryEntry {
  id: number;
  employee_id: number;
  kind: MemoryKind;
  content: string;
  source_ref: string | null;
  created_at: string;
  updated_at: string;
}

export interface KnowledgeItem {
  id: number;
  scope: KnowledgeScope;
  owner_employee_id: number | null;
  department_id: number | null;
  title: string;
  content: string;
  topic: string | null;
  status: KnowledgeStatus;
  confidence: number | null;
  sources: string[];
  created_at: string;
  updated_at: string;
}

export interface WorkSession {
  id: number;
  task_id: number;
  employee_id: number;
  runtime_type: RuntimeType;
  runtime_session_ref: string | null;
  status: WorkSessionStatus;
  summary: string | null;
  started_at: string | null;
  ended_at: string | null;
  cost: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

export interface CompanyEvent {
  id: number;
  type: string;
  company_id: number;
  actor_employee_id: number | null;
  project_id: number | null;
  task_id: number | null;
  payload: Record<string, unknown>;
  created_at: string;
}

// ---------- API payloads (§8) ----------

export interface ProjectDetail extends Project {
  milestones: Milestone[];
  // Each task carries its own `dependencies: number[]` (see Task).
  tasks: Task[];
  artifacts: Artifact[];
}

export interface ProjectGraphNode {
  id: string;
  type: string | null;
  label: string;
  status: TaskStatus;
}

export interface ProjectGraphEdge {
  source: string;
  target: string;
}

export interface ProjectGraph {
  nodes: ProjectGraphNode[];
  edges: ProjectGraphEdge[];
}

export interface CreateProjectInput {
  name: string;
  description: string;
  goal?: string;
}

export interface EmployeePerformance {
  employee_id: number;
  attempts: number;
  success_count: number;
  success_rate: number;
  artifacts_count: number;
  learning_records_count: number;
}

// ---------- v0.2: Providers ----------

export type ProviderType =
  "openai" | "anthropic" | "openrouter" | "deepseek" | "gemini" | "ollama" | "custom";

export type ProviderScope = "company" | "employee";

export interface Provider {
  id: number;
  name: string;
  provider_type: ProviderType;
  base_url: string | null;
  scope: ProviderScope;
  owner_employee_id: number | null;
  enabled: boolean;
  /** True when a credential is stored server-side. The key itself is never returned. */
  has_credential: boolean;
  /** Masked credential, e.g. `sk-••••abcd`. The only key material the UI may show. */
  credential_mask: string | null;
  metadata: Record<string, unknown>;
  in_use_by: number;
  created_at: string;
  updated_at: string;
}

export interface ProviderTestResult {
  ok: boolean;
  latency_ms: number | null;
  error: string | null;
  models_count: number | null;
}

export interface ProviderModels {
  models: string[];
  source: "api" | "manual";
}

export interface CreateProviderInput {
  name: string;
  provider_type: ProviderType;
  base_url?: string | null;
  scope: ProviderScope;
  owner_employee_id?: number | null;
  api_key?: string;
  metadata?: Record<string, unknown>;
}

export interface UpdateProviderInput {
  name?: string;
  provider_type?: ProviderType;
  base_url?: string | null;
  scope?: ProviderScope;
  owner_employee_id?: number | null;
  /** Write-only: sending a value replaces the stored credential. Omit to keep. */
  api_key?: string;
  enabled?: boolean;
  metadata?: Record<string, unknown>;
}

// ---------- v0.2: Runtimes ----------

export interface RuntimeCapabilities {
  chat: boolean;
  task: boolean;
  filesystem: boolean;
  terminal: boolean;
  web: boolean;
  memory: boolean;
  skills: boolean;
  scheduler: boolean;
  streaming: boolean;
  artifacts: boolean;
}

export interface RuntimeTypeInfo {
  type: RuntimeType;
  implemented: boolean;
  docker_available: boolean;
  deployment_modes: string[];
  capabilities: RuntimeCapabilities;
  supported_providers: ProviderType[];
}

export type RuntimeInstanceStatus =
  | "created"
  | "starting"
  | "running"
  | "idle"
  | "stopping"
  | "stopped"
  | "unhealthy"
  | "crashed"
  | "error"
  | "deleting";

export interface RuntimeInstance {
  id: number;
  employee_id: number;
  runtime_type: RuntimeType;
  deployment_mode: string;
  container_name: string | null;
  image: string | null;
  image_tag: string | null;
  runtime_version: string | null;
  status: RuntimeInstanceStatus;
  health_status: string;
  internal_host: string | null;
  internal_port: number | null;
  workspace_path: string;
  data_path: string;
  model_binding_id: number | null;
  cpu_limit: number;
  memory_limit_mb: number;
  last_healthcheck_at: string | null;
  started_at: string | null;
  created_at: string;
  updated_at: string;
  provider_name?: string | null;
  model?: string | null;
}

export interface RuntimeLogs {
  lines: string[];
}

export type RuntimeImageCompatibility = "verified" | "unverified" | "unknown";

export type RuntimeImageUpdateStatus =
  | "idle"
  | "checking"
  | "available"
  | "downloading"
  | "updating"
  | "verifying"
  | "completed"
  | "failed"
  | "rolled_back";

export interface RuntimeImageInfo {
  runtime_type: RuntimeType;
  repository: string;
  tag: string;
  installed_version: string | null;
  latest_version: string | null;
  update_available: boolean;
  compatibility_status: RuntimeImageCompatibility;
  update_status: RuntimeImageUpdateStatus;
  last_checked_at: string | null;
  used_by: number;
}

export interface CreateEmployeeRuntimeInput {
  runtime_type: RuntimeType;
  deployment_mode: "docker";
  provider_id: number;
  model: string;
  cpu_limit?: number;
  memory_limit_mb?: number;
}

export interface UpdateRuntimeProviderInput {
  provider_id: number;
  model: string;
}

export interface EmployeeBrain {
  employee_id: number;
  personality: string | null;
  goals: string | null;
  interests: string[];
  learning_policy: Record<string, unknown>;
  memory_policy: Record<string, unknown>;
  curiosity: number;
}

export interface SettingsResponse {
  runtime_mode: string;
  workspace_root: string;
  api_port: number;
  web_port: number;
  company_name: string;
}

/** WS /ws/events message shape (§7). */
export interface StreamEvent {
  type: string;
  data: Record<string, unknown>;
  ts: string;
}
