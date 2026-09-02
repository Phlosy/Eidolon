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

export type ProjectPhaseStatus =
  | "pending"
  | "ready"
  | "in_progress"
  | "waiting_review"
  | "changes_requested"
  | "approved"
  | "completed"
  | "blocked";

export type ReviewDecision =
  "approved" | "conditionally_approved" | "changes_requested" | "rejected";

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
  lifecycle_status: LifecycleStatus;
  username: string | null;
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
  planned_start_at?: string | null;
  planned_end_at?: string | null;
  code?: string | null;
  priority?: string;
  customer?: string;
  background?: string;
  objectives?: string[];
  technical_requirements?: string[];
  constraints?: string[];
  deliverables?: string[];
  review_configuration?: Record<string, unknown>;
  participants?: Record<string, unknown>;
  tutorial_accelerated?: boolean;
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
  owner_id?: number | null;
  planned_start_at?: string | null;
  planned_end_at?: string | null;
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
  planned_start_at?: string | null;
  planned_end_at?: string | null;
  actual_start_at?: string | null;
  actual_end_at?: string | null;
  phase_id?: number | null;
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

export interface ProjectTimeline extends Project {
  milestones: Milestone[];
  tasks: Task[];
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
  description?: string;
  goal?: string;
  code?: string;
  priority?: string;
  customer?: string;
  owner_id?: number | null;
  background?: string;
  objectives?: string[];
  requirements?: RequirementInput[];
  technical_requirements?: string[];
  constraints?: string[];
  deliverables?: string[];
  deadline?: string | null;
  milestones?: Record<string, unknown>[];
  review_configuration?: ReviewConfiguration;
  participants?: ProjectParticipants;
  tutorial_accelerated?: boolean;
}

export interface RequirementInput {
  code?: string;
  title: string;
  description: string;
  priority: string;
  acceptance_criteria: string;
}

export interface ReviewConfiguration {
  requirements_review: true;
  design_review: true;
  acceptance_review: true;
  additional_reviews: string[];
}

export interface ProjectParticipants {
  customer_contact: string;
  project_owner_employee_id: number | null;
  presenter_employee_id: number | null;
  reviewer_names: string[];
  approver_names: string[];
}

export interface ProjectRequirement extends Required<RequirementInput> {
  id: number;
  project_id: number;
  sequence: number;
  design_refs: string[];
  implementation_refs: string[];
  test_refs: string[];
  acceptance_refs: string[];
  created_at: string;
  updated_at: string;
}

export interface ProjectPhase {
  id: number;
  project_id: number;
  phase_type: string;
  name: string;
  order: number;
  status: ProjectPhaseStatus;
  started_at: string | null;
  completed_at: string | null;
  gate_required: boolean;
  review_id: number | null;
  baseline_id: number | null;
  owner_employee_id: number | null;
  metadata_json: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface DocumentArtifact {
  id: number;
  project_id: number;
  phase_id: number | null;
  review_id: number | null;
  change_request_id: number | null;
  category: "internal" | "formal" | "review" | "delivery" | "source" | "build" | "test";
  document_type: string;
  title: string;
  format: "markdown" | "docx" | "pptx" | "pdf" | "zip";
  version_label: string;
  drive_node_id: number;
  drive_revision_id: number | null;
  author_employee_id: number | null;
  review_status: string;
  baseline_status: string;
  source_document_id: number | null;
  metadata_json: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface ReviewPackage {
  id: number;
  review_id: number;
  title: string;
  documents: DocumentArtifact[];
  version: number;
  content_hash: string;
}

export interface ReviewMeeting {
  id: number;
  project_id: number;
  phase_id: number;
  source_phase_id: number;
  review_type: string;
  subtype: string | null;
  title: string;
  status: "preparing" | "waiting_for_customer" | "completed";
  scheduled_at: string | null;
  presenter_employee_id: number | null;
  participants: ProjectParticipants | Record<string, unknown>;
  decision: ReviewDecision | null;
  comments: string;
  action_items: string[];
  completed_at: string | null;
  decision_version: number;
  package: ReviewPackage | null;
  created_at: string;
  updated_at: string;
}

export interface Baseline {
  id: number;
  project_id: number;
  phase_id: number;
  review_id: number;
  baseline_type: string;
  name: string;
  version: string;
  document_artifact_ids: number[];
  supersedes_baseline_id: number | null;
  active: boolean;
  created_at: string;
  updated_at: string;
}

export interface ChangeRequest {
  id: number;
  project_id: number;
  code: string;
  title: string;
  reason: string;
  requested_by: string;
  priority: string;
  status: string;
  decision: string | null;
  affected_requirements: string[];
  affected_design: string[];
  affected_tasks: string[];
  affected_tests: string[];
  impact_analysis: Record<string, unknown>;
  decided_at: string | null;
  closed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface DeliveryPackage {
  id: number;
  project_id: number;
  version: string;
  status: string;
  manifest: Record<string, unknown>;
  drive_node_id: number | null;
  content_hash: string;
  created_at: string;
  updated_at: string;
}

export interface ProjectLifecycle {
  project: Project;
  requirements: ProjectRequirement[];
  phases: ProjectPhase[];
  reviews: ReviewMeeting[];
  documents: DocumentArtifact[];
  baselines: Baseline[];
  change_requests: ChangeRequest[];
  delivery_packages: DeliveryPackage[];
  pending_user_action: {
    kind: string;
    title: string;
    review_id: number;
    phase_id: number;
  } | null;
  coverage: {
    requirements: number;
    design: number;
    implementation: number;
    tests: number;
    acceptance: number;
  };
  role_coverage_warning: string | null;
}

export interface ReviewDecisionInput {
  decision: ReviewDecision;
  comments: string;
  conditions?: string[];
  action_items?: string[];
  expected_version?: number;
}

export interface TutorialProgress {
  id: number;
  company_id: number;
  status: "not_started" | "active" | "skipped" | "completed";
  current_step: string;
  completed_steps: string[];
  context: Record<string, number | string | boolean>;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
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

// ---------- v0.3: Drive (unified file hierarchy, docs/design-v0.3-workspace.md §2) ----------

export type DriveZone = "projects" | "knowledge" | "skills" | "handbook";

export type DriveNodeKind = "folder" | "document";

/** Document type tags within the Drive (project-zone types keep the old artifact semantics). */
export type DriveDocType =
  | "prd"
  | "research_report"
  | "architecture"
  | "source_file"
  | "test_report"
  | "readme"
  | "release"
  | "note"
  | "knowledge"
  | "skill_doc"
  | "handbook"
  | "docx"
  | "pptx"
  | "pdf";

export interface DriveNode {
  id: number;
  parent_id: number | null;
  kind: DriveNodeKind;
  name: string;
  path: string;
  zone: DriveZone;
  project_id: number | null;
  doc_type: DriveDocType | null;
  owner_employee_id: number | null;
  current_version: number;
  created_at: string;
  updated_at: string;
}

export interface DriveCollaborator {
  employee_id: number;
  role: "viewer" | "editor";
}

export interface DriveNodeDetail extends DriveNode {
  content: string | null;
  collaborators: DriveCollaborator[];
}

export interface DriveRevision {
  version: number;
  sha256: string;
  author_employee_id: number | null;
  message: string | null;
  created_at: string;
}

export interface CreateDriveFolderInput {
  zone: DriveZone;
  parent_id?: number | null;
  name: string;
  project_id?: number | null;
}

export interface UploadDriveFileInput {
  zone: DriveZone;
  file: File;
  parent_id?: number | null;
  project_id?: number | null;
}

/** PATCH /drive/nodes/{id} — documents only; creates a new revision. */
export interface UpdateDriveNodeInput {
  content: string;
  message?: string;
}

/** POST /employees/{id}/providers — the employee's own provider account (v0.3 ownership). */
export interface CreateEmployeeProviderInput {
  name: string;
  provider_type: ProviderType;
  base_url?: string | null;
  /** Write-only: stored server-side in the SecretStore, never returned. */
  api_key?: string;
  model?: string;
}

// ---------- v0.3: Git integration ----------

export type GitPlatformType = "gitlab" | "gitea" | "github" | "custom";

export type GitBuiltinStatus = "not_installed" | "installing" | "stopped" | "running" | "error";

export interface GitConnection {
  id: number;
  name: string;
  platform_type: GitPlatformType;
  base_url: string;
  /** True when a token is stored server-side. The token itself is never returned. */
  has_credential: boolean;
  /** Masked credential, e.g. `glpat-••••abcd`. The only token material the UI may show. */
  credential_mask: string | null;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface GitOverview {
  builtin: {
    docker_available: boolean;
    status: GitBuiltinStatus;
    url: string | null;
    version: string | null;
  };
  connections: GitConnection[];
}

export interface GitTestResult {
  ok: boolean;
  latency_ms: number | null;
  version: string | null;
  error: string | null;
}

export interface CreateGitConnectionInput {
  name: string;
  platform_type: GitPlatformType;
  base_url: string;
  /** Write-only: stored server-side, never returned. */
  token?: string;
  enabled?: boolean;
}

export interface UpdateGitConnectionInput {
  name?: string;
  platform_type?: GitPlatformType;
  base_url?: string;
  /** Write-only: sending a value replaces the stored token. Omit to keep. */
  token?: string;
  enabled?: boolean;
}

/** WS /ws/events message shape (§7). */
export interface StreamEvent {
  type: string;
  data: Record<string, unknown>;
  ts: string;
}

// ---------- v0.4: Employee lifecycle (docs/design-v0.4-lifecycle.md §10) ----------

export type LifecycleStatus =
  "pending" | "onboarding" | "active" | "transferring" | "suspended" | "offboarding" | "offboarded";

export type AccountStatus =
  | "pending"
  | "provisioning"
  | "active"
  | "suspended"
  | "failed"
  | "deprovisioning"
  | "deprovisioned";

export type EntitlementType = "role" | "group" | "permission" | "resource_access";

export interface Position {
  id: number;
  department_id: number;
  title: string;
  level: string | null;
}

export interface Employment {
  id: number;
  department_id: number;
  position_id: number | null;
  manager_employee_id: number | null;
  employment_status: string;
  joined_at: string;
  effective_from: string;
  effective_to: string | null;
}

export interface EmploymentInfo {
  current: Employment | null;
  history: Employment[];
}

export interface Entitlement {
  id: number;
  key: string;
  name: string;
  type: EntitlementType;
  resource_type: string;
  description: string | null;
}

/** Effective entitlement with the packages it comes from (GET /employees/{id}/entitlements). */
export interface EmployeeEntitlement {
  entitlement: Entitlement;
  sources: { package_id: number; package_name: string }[];
}

export interface AccessPackage {
  id: number;
  slug: string;
  name: string;
  description: string | null;
  built_in: boolean;
  entitlements: Entitlement[];
}

export interface ResourceAccount {
  id: number;
  resource_type: string;
  provider_id: number;
  username: string;
  display_name: string | null;
  status: AccountStatus;
  provisioning_state: string | null;
  last_synced_at: string | null;
  metadata: Record<string, unknown>;
}

export interface ResourceAsset {
  id: number;
  resource_type: string;
  external_id: string;
  owner_employee_id: number | null;
  project_id: number | null;
  provider_key: string | null;
  metadata: Record<string, unknown>;
}

export type ProvisioningJobKind =
  "onboarding" | "transfer" | "permission_change" | "suspension" | "resumption" | "offboarding";

export type ProvisioningJobStatus = "pending" | "running" | "done" | "partial" | "failed";

export type ProvisioningStepStatus = "pending" | "running" | "done" | "failed" | "skipped";

export interface ProvisioningStep {
  id: number;
  seq: number;
  resource_type: string;
  provider_key: string;
  action: string;
  description: string | null;
  status: ProvisioningStepStatus;
  attempts: number;
  error: string | null;
  started_at: string | null;
  completed_at: string | null;
}

export interface ProvisioningJob {
  id: number;
  employee_id: number;
  kind: ProvisioningJobKind;
  status: ProvisioningJobStatus;
  total_steps: number;
  done_steps: number;
  reason: string | null;
  created_at: string;
  completed_at: string | null;
  /** Present on GET /provisioning-jobs/{id}; the list endpoint may omit it. */
  steps?: ProvisioningStep[];
}

export interface ProvisioningPreviewStep {
  resource_type: string;
  provider_key: string;
  action: string;
  description: string | null;
  available: boolean;
}

export interface ReconcileDrift {
  account_id: number;
  resource_type: string;
  kind: string;
  detail: string;
}

// ---------- v0.4: lifecycle API payloads ----------

export interface OnboardEmployeeInput {
  name: string;
  slug?: string;
  title: string;
  role: EmployeeRole;
  department_id: number;
  position_id?: number;
  manager_employee_id?: number;
  runtime_type: RuntimeType;
  provider_id?: number;
  provider_name?: string;
  provider_type?: ProviderType;
  provider_base_url?: string;
  /** Write-only; the server stores this in the encrypted secret store. */
  provider_api_key?: string;
  model?: string;
  personality?: string;
  goals?: string;
  learning_enabled?: boolean;
  curiosity?: number;
  access_package_ids?: number[];
}

export interface OnboardEmployeeResult {
  employee: Employee;
  job: ProvisioningJob;
}

export interface TransferEmployeeInput {
  department_id: number;
  position_id?: number;
  manager_employee_id?: number;
  access_package_ids?: number[];
  reason?: string;
}

export interface OffboardEmployeeInput {
  /** "department" | "company" | "archive" | a specific employee id. */
  transfer_to: "department" | "company" | "archive" | number;
  reason?: string;
}

export interface ProvisioningPreviewInput {
  department_id: number;
  position_id?: number;
  access_package_ids?: number[];
}
