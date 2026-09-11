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
  | "requested"
  | "planning"
  | "in_progress"
  | "in_review"
  | "completed"
  | "cancelled"
  | "rejected"
  // M2.1（B11）：连负责人都没有 —— 系统不替公司规划，如实停在等待
  | "waiting_for_management";

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
  work_mode?: "guided" | "managed" | null;
  planning_fixture?: "none" | "deterministic_template" | null;
  spec_version?: number;
  work_intake_position_code?: string | null;
  management_employee_id?: number | null;
  management_person_id?: number | null;
  management_assigned_at?: string | null;
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
  /** "" | "failure" | "behavior-extension"：失败驱动与人格延伸必须可区分（§13.1） */
  source: string;
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
  /** 晋升提案的目标 scope；仅 status === "proposed" 时有值。 */
  proposed_scope: KnowledgeScope | null;
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
  /** M2.1：不传就用公司默认；传了就快照到项目行，之后公司默认变化不影响它。 */
  work_mode?: "guided" | "managed" | null;
  /** M2.1：确定性规划 fixture —— **基础设施**，生产不传（仅教程/CI/演示）。 */
  planning_fixture?: "none" | "deterministic_template";
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
  user_id: number;
  company_id: number;
  tutorial_id: string;
  status: "not_started" | "active" | "paused" | "skipped" | "completed";
  current_stage: string;
  current_step: string;
  completed_steps: string[];
  skipped_steps: string[];
  context: Record<string, number | string | boolean>;
  started_at: string | null;
  completed_at: string | null;
  paused_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface HumanUser {
  id: number;
  email: string;
  email_verified: boolean;
  display_name: string;
  avatar: string;
  status: string;
  onboarding_status: "not_started" | "in_progress" | "completed";
  locale: string;
  timezone: string;
  last_login_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface CompanyMembership {
  id: number;
  user_id: number;
  company_id: number;
  role: "OWNER" | "ADMIN" | "MEMBER" | "VIEWER";
}

export interface AuthState {
  user: HumanUser;
  company: Company & { stage: "FOUNDING" | "OPERATING" };
  membership: CompanyMembership;
}

export interface RegisterInput {
  email: string;
  password: string;
  display_name?: string;
  locale?: string;
  timezone?: string;
}

export interface RegistrationResult {
  email: string;
  verification_required: boolean;
  expires_at: string;
  development_verification_token: string | null;
}

export interface UserSession {
  id: number;
  current: boolean;
  created_at: string;
  expires_at: string;
  last_seen_at: string;
  ip_address: string;
  user_agent: string;
}

export interface Passkey {
  id: number;
  name: string;
  transports: string[];
  device_type: string;
  backed_up: boolean;
  created_at: string;
  updated_at: string;
  last_used_at: string | null;
}

export interface WebAuthnOptions {
  challenge_id: number;
  public_key: Record<string, unknown>;
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
  | "openai"
  | "anthropic"
  | "openrouter"
  | "deepseek"
  | "moonshot"
  | "zhipu"
  | "qwen"
  | "groq"
  | "mistral"
  | "gemini"
  | "ollama"
  | "custom";

/** 内置厂商预设（GET /providers/presets）：表单据此自动填 base_url 与推荐模型。 */
export interface ProviderPreset {
  provider_type: ProviderType;
  default_base_url: string | null;
  requires_api_key: boolean;
  /** 常见示例：权威清单以实时探测（POST /providers/probe）为准。 */
  recommended_models: string[];
  /** 官方模型文档，供用户核对 */
  docs_url: string | null;
}

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
  /** 公司级模型目录（员工私有账号的模型在 bindings 上） */
  available_models: Array<{ model: string; alias: string }>;
  default_model: string | null;
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
  /** 公司级模型目录（可选） */
  models?: Array<{ model: string; alias?: string }>;
  primary_model?: string;
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
  /** 该 runtime 是否真的把行为投影送进 agent 上下文（诚实能力位，§8） */
  brain_projection: boolean;
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
  deployment_mode: "docker" | "mock";
  /** Mock 运行时不需要 provider/model；docker 运行时必填（后端会校验）。 */
  provider_id?: number;
  model?: string;
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
  /** legacy 镜像列（权威值在 traits 里，两者由后端同步写） */
  curiosity: number;
  traits?: Record<string, number> | null;
  /** 服务端解析出的 BehaviorPolicy 摘要：只含工作方式，永不含 confidence / 结果判定 */
  behavior?: BehaviorPolicySummary | null;
}

/** `app.brain.BehaviorPolicy.as_dict()` 的镜像。阈值一律在后端，前端只读。 */
export interface BehaviorPolicySummary {
  policy_version: string;
  profile_revision: number;
  band: "low" | "moderate" | "high" | string;
  traits: Record<string, number>;
  work_directives: string[];
  retrieval: {
    knowledge_limit: number;
    include_candidate_skills: boolean;
    candidate_min_success_rate: number;
    novel_topic_ratio: number;
    max_context_items: number;
  };
  reflection: {
    open_question_count: number;
    alternative_hypotheses: number;
    note_style: string;
  };
  learning: {
    followup_topics_per_task: number;
    followup_priority_score: number;
    priority_score_cap: number;
    topic_source: string;
  };
}

export interface BehaviorProjection {
  employee_id: number;
  policy_version: string;
  revision: number;
  band: string;
  projection_markdown: string;
  paths: string[];
  mirrored_revision: number | null;
  mirror_current: boolean;
}

/** 候选技能基准（§10）。`success` 是事实，`outcome` 是人的判断，两者互不推导。 */
export interface SkillUsage {
  id: number;
  employee_id: number;
  skill_id: number;
  task_id: number | null;
  work_session_id: number | null;
  skill_validation_status: string;
  selection_reason: string;
  policy_version: string;
  profile_revision: number;
  success: boolean;
  outcome: "useful" | "not_useful" | null;
  outcome_source: string | null;
  created_at: string;
  updated_at: string;
}

export interface SkillUsageBenchmarks {
  total_usages: number;
  candidate_usages: number;
  candidate_skills_tried: number;
  rated_usages: number;
  pending_ratings: number;
  /** 分母为 0 时后端返回 null（没数据 ≠ 0%） */
  trial_rate: number | null;
  conversion_rate: number | null;
  useful_rate: number | null;
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
  /** 多模型条目：每个建一条绑定；primary_model 为默认启动模型（缺省取第一个）。 */
  models?: Array<{ model: string; alias?: string }>;
  primary_model?: string;
}

/** 条目式模型编辑器里的一行：显示名 + 真实模型名 + 是否选用。 */
export interface ModelEntry {
  /** 显示名（可改，默认等于真实模型名） */
  alias: string;
  /** 发给厂商的真实模型名 */
  model: string;
  /** 是否选用（不选用的不会建绑定） */
  enabled: boolean;
}

/** 员工 ↔ Provider ↔ 模型的绑定（GET /employees/{id}/bindings）。 */
export interface ModelBinding {
  id: number;
  employee_id: number;
  provider_id: number;
  provider_name: string;
  model: string;
  /** 显示名（空 = 与真实模型名相同） */
  alias: string;
  is_primary: boolean;
  position: number;
}

/** POST /providers/probe：不保存配置的试连结果。 */
export interface ProviderProbeResult {
  ok: boolean;
  error: string | null;
  models: string[];
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

/** 任职完整性折叠视图（只读诊断）。与后端 AssignmentIntegrityOut 一致。 */
export interface AssignmentIntegrity {
  /** valid | invalid —— issues 为空 = 真的没问题，不是“没算”。 */
  status: "valid" | "invalid";
  issues: string[];
  read_only: boolean;
}

/** 当前任职派生视图（CurrentPositionOut）。为 null ⇒ 这个人 AVAILABLE。 */
export interface CurrentPositionView {
  definition_id: number;
  code: string;
  name: string;
  level: number;
  job_family: string;
  legacy_role: string | null;
  department_id: number | null;
  department_name: string | null;
  slot_id: number;
  slot_code: string;
  since: string;
  assignment_type: string;
  position_is_custom: boolean;
}

/**
 * 员工详情主接口契约（/employees/{id} 最终形态，P6 WIP 落地后接线）。
 * 当前由 GET /talent-roster/{id} 承载同一形状（后端 EmployeeDetailOut）。
 * 派生三区只读、无 PATCH 入口、统一来源于 WorkforceStatusResolver / position_compat。
 */
export interface EmployeeDetail extends Employee {
  workforce_status: WorkforceStatus;
  has_primary_assignment: boolean;
  occupies_establishment: boolean;
  current_position: CurrentPositionView | null;
  assignment_integrity: AssignmentIntegrity;
}

// ---------- P5: Talent profile & competency foundation (docs/competency-system.md) ----------

export type CompetencyRowStatus = "unrated" | "provisional" | "assessed" | "stale";
export type TrendDirection = "up" | "stable" | "down" | "unknown";

/** competency_domains（company_id null = 全局内置目录）。 */
export interface CompetencyDomain {
  id: number;
  company_id: number | null;
  code: string;
  name: string;
  kind: "general" | "professional";
  description: string;
  order_index: number;
  built_in: boolean;
}

export interface CompetencyDefinition {
  id: number;
  domain_id: number;
  code: string;
  name: string;
  description: string;
  order_index: number;
  built_in: boolean;
}

/** 8 维人格的 UI 数据契约（只描述倾向；affects_execution=false ⇒ 当前不影响执行）。 */
export interface TraitView {
  code: string;
  label: string;
  description: string;
  value: number; // 0..1（存储口径）
  display: number; // 派生 round(value*100)
  affects_execution: boolean;
}

/** 员工能力维度（score/confidence 为 null = 未评估 —— 绝不显示 0 分）。 */
export interface EmployeeCompetencyView {
  competency_definition_id: number;
  domain_id: number;
  domain_code: string;
  domain_name: string;
  code: string;
  name: string;
  description: string;
  kind: "general" | "professional";
  score: number | null;
  confidence: number | null;
  evidence_count: number;
  status: CompetencyRowStatus;
  trend: number | null;
  trend_direction: TrendDirection;
  last_assessed_at: string | null;
}

export interface EmployeeCapabilities {
  general: EmployeeCompetencyView[];
  professional: EmployeeCompetencyView[];
}

export interface CompetencyEvidenceView {
  id: number;
  employee_id: number;
  competency_definition_id: number;
  competency_code: string;
  competency_name: string;
  source_kind: string;
  source_id: number | null;
  source_ref: string;
  assessment_run_id: number | null;
  signal: number | null;
  quality: number | null;
  occurred_at: string;
}

// ---------- P6: Assessment & evidence pipeline (docs/evidence-pipeline.md) ----------

export interface CriterionCompetencyView {
  competency_definition_id: number;
  code: string;
  name: string;
  domain_code: string;
  domain_name: string;
  contribution_weight: number;
  evidence_type: string;
}

export interface CriterionView {
  id: number;
  code: string;
  name: string;
  description: string;
  weight: number;
  order_index: number;
  evidence_kinds: string[];
  competencies: CriterionCompetencyView[];
}

export interface AssessmentProfileView {
  id: number;
  code: string;
  version: number;
  name: string;
  description: string;
  applies_to_kind: string;
  position_definition_id: number | null;
  min_evidence_count: number;
  half_life_days: number;
  algorithm_version: string;
  built_in: boolean;
}

export interface AssessmentRunSummary {
  id: number;
  employee_id: number;
  profile_id: number | null;
  profile_code: string | null;
  profile_version: number | null;
  assessment_type: string;
  triggered_by: string;
  status: string;
  window_from: string | null;
  window_to: string | null;
  evidence_count: number;
  inputs_hash: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface AssessmentResultView {
  kind: "criterion" | "contribution";
  criterion_id: number | null;
  criterion_code: string | null;
  competency_definition_id: number | null;
  competency_code: string | null;
  observed_score: number | null;
  confidence: number | null;
  evidence_count: number;
  contribution: number | null;
  rationale: string;
}

export interface AssessmentRunDetail extends AssessmentRunSummary {
  outputs: Record<string, unknown>;
  results: AssessmentResultView[];
}

/** 能力解释（为什么是这个分）。score/confidence 为 null = 未评估（不是 0）。 */
export interface CompetencyExplanation {
  competency_definition_id: number;
  code: string;
  name: string;
  domain_code: string;
  domain_name: string;
  score: number | null;
  confidence: number | null;
  evidence_count: number;
  status: string;
  trend: number | null;
  trend_direction: "up" | "stable" | "down" | "unknown";
  last_assessed_at: string | null;
  assessment_history: Array<{
    run_id: number;
    profile_code: string | null;
    profile_version: number | null;
    assessment_type: string;
    triggered_by: string;
    window_from: string | null;
    window_to: string | null;
    score: number | null;
    previous_score: number | null;
    trend: number | null;
    status: string | null;
    evidence_count: number | null;
    created_at: string;
  }>;
  recent_evidence: Array<{
    id: number;
    source_kind: string;
    source_id: number | null;
    source_ref: string;
    signal: number | null;
    strength: number | null;
    reliability: number | null;
    occurred_at: string;
  }>;
  source_distribution: Array<{ source_kind: string; count: number }>;
  recent_criterion_results: Array<{
    criterion_code: string | null;
    criterion_name: string | null;
    observed: number | null;
    confidence: number | null;
    contribution: number | null;
    evidence_count: number;
  }>;
  relevant_skills: Array<{
    id: number;
    name: string;
    attempts: number;
    success_count: number;
    validation_status: string;
  }>;
}

// ---------- P7: Position competency profile (docs/position-competency-profile.md) ----------

/** 岗位对能力的要求类型 —— 与后端 REQUIREMENT_TYPES 一致（契约测试钉死）。 */
export const POSITION_REQUIREMENT_TYPES = ["required", "preferred"] as const;
export type PositionRequirementType = (typeof POSITION_REQUIREMENT_TYPES)[number];

/** 画像版本状态。 */
export const POSITION_PROFILE_STATUSES = ["draft", "active", "retired"] as const;
export type PositionProfileStatus = (typeof POSITION_PROFILE_STATUSES)[number];

export interface ProfileRequirement {
  id: number;
  competency_definition_id: number;
  code: string;
  name: string;
  domain_id: number | null;
  domain_code: string;
  domain_name: string;
  kind: "general" | "professional";
  requirement_type: PositionRequirementType;
  minimum_score: number | null;
  target_score: number | null;
  minimum_confidence: number | null;
  critical: boolean;
  priority: number;
  weight: number;
  notes: string;
}

export interface ProfileIntegrationCoverage {
  required_count: number;
  covered_count: number;
  uncovered_competency_ids: number[];
}

export interface ProfileIntegrity {
  status: string;
  codes: string[];
  coverage: ProfileIntegrationCoverage;
  read_only: boolean;
}

export interface ProfileAssessmentInfo {
  id: number;
  code: string;
  version: number;
  name: string;
  algorithm_version: string;
}

export interface ProfileVersionItem {
  id: number;
  version: number;
  status: PositionProfileStatus;
  effective_from: string | null;
  effective_to: string | null;
  published_at: string | null;
  published_note: string;
  requirement_count: number;
}

export interface PositionCompetencyProfile {
  position_definition_id: number;
  position_code: string;
  configured: boolean;
  profile_version: number | null;
  profile_status: PositionProfileStatus | null;
  effective_from: string | null;
  effective_to: string | null;
  assessment_profile: ProfileAssessmentInfo | null;
  general: ProfileRequirement[];
  professional: ProfileRequirement[];
  integrity: ProfileIntegrity;
  versions: ProfileVersionItem[];
}

export interface PositionProfileSummary {
  position_definition_id: number;
  code: string;
  name: string;
  active_version: number | null;
  profile_status: PositionProfileStatus | null;
  requirement_count: number;
  assessment_profile_code: string | null;
}

export interface ProfileTemplate {
  template_version_id: number;
  position_code: string;
  position_name: string;
  version: number;
  requirement_count: number;
}

// ---------- P11: Autonomous learning ----------

export interface LearningPolicyView {
  enabled: boolean;
  daily_token_budget: number;
  daily_cost_budget: number;
  max_session_minutes: number;
  max_sessions_per_day: number;
  allow_web_research: boolean;
  allow_practice: boolean;
  idle_delay_minutes: number;
  cooldown_minutes: number;
}

export interface EmployeeLearningPolicyView {
  inherit_company_policy: boolean;
  enabled: boolean;
  personal_daily_budget_override: number | null;
  idle_delay_override: number | null;
  autonomous_learning_warning: boolean;
}

export interface LearningSessionView {
  id: number;
  employee_id: number;
  topic: string;
  reason: string;
  source_type: string;
  status: string;
  learning_mode: string;
  budget_tokens: number;
  tokens_used: number;
  cost_used: number;
  runtime_type: string;
  provider_name: string;
  started_at: string | null;
  completed_at: string | null;
  summary: string;
  outputs: Record<string, unknown>;
}

// ---------- P10: Career & talent development ----------

export type CareerReadinessStatus =
  "READY" | "NEAR_READY" | "DEVELOPMENT_NEEDED" | "NEEDS_EVIDENCE" | "CRITICAL_GAPS";

export interface CareerNextPosition {
  target_position: { id: number; code: string; name: string };
  transition_type: string;
  readiness_status: CareerReadinessStatus;
  position_fit: {
    known_fit_score: number | null;
    fit_confidence: number | null;
    required_coverage: number;
    qualification_status: string;
    fit_status: string;
  };
  required_gaps: string[];
  uncertainties: string[];
}

export interface CareerPlanItem {
  id: number;
  plan_id: number;
  competency_definition_id: number;
  code: string;
  name: string;
  need_type: string;
  objective: string;
  target_score: number | null;
  target_confidence: number | null;
  priority: number;
  status: string;
  recommended_actions: string[];
  progress_metadata: Record<string, unknown>;
}

export interface CareerPlan {
  id: number;
  employee_id: number;
  target_position_definition_id: number | null;
  target_position: { id: number; code: string; name: string } | null;
  status: string;
  title: string;
  description: string;
  source_fit_hash: string;
  created_at: string | null;
  items: CareerPlanItem[];
}

export interface CareerTimelineEvent {
  type: string;
  at: string | null;
  title: string;
  reason: string;
  source: string;
}

export interface CareerOverview {
  employee_id: number;
  current_position: { definition_id: number; code: string; name: string; since: string } | null;
  next_positions: CareerNextPosition[];
  plans: Array<{
    id: number;
    title: string;
    status: string;
    target_position_definition_id: number | null;
    item_count: number;
    completed_count: number;
    created_at: string | null;
  }>;
  timeline: CareerTimelineEvent[];
}

export interface CareerReadiness {
  employee_id: number;
  target_position: { id: number; code: string; name: string };
  position_fit: {
    known_fit_score: number | null;
    fit_confidence: number | null;
    required_coverage: number;
    qualification_status: string;
    fit_status: string;
  };
  critical_gaps: string[];
  uncertainties: string[];
  required_gaps: string[];
  experience: {
    tasks_completed: number;
    projects_completed: number;
    reviews_presented: number;
    assessments_count: number;
    active_plans: number;
  };
  experience_readiness: { tenure_days: number | null; minimum_tenure_days: number; met: boolean };
  readiness_status: CareerReadinessStatus;
  reasons: string[];
  inputs_hash: string;
}

// ---------- P9: Talent roster & candidate analysis ----------

export const CANDIDATE_BANDS = [
  "RECOMMENDED",
  "VIABLE",
  "DEVELOPMENTAL",
  "NEEDS_EVIDENCE",
  "CRITICAL_GAP",
] as const;
export type CandidateBand = (typeof CANDIDATE_BANDS)[number];

export interface RosterTraitSummary {
  code: string;
  value: number;
}

export interface RosterTopCompetency {
  code: string;
  score: number;
  confidence: number | null;
}

export interface TalentRosterItem {
  employee_id: number;
  name: string;
  slug: string;
  avatar: string;
  department_id: number | null;
  department_name: string | null;
  lifecycle_status: string;
  workforce_status: string;
  has_primary_assignment: boolean;
  occupies_establishment: boolean;
  current_position: {
    definition_id: number;
    code: string;
    name: string;
    slot_code: string;
    department_id: number | null;
    department_name: string | null;
  } | null;
  integrity: string[];
  runtime: { type: string; status: string } | null;
  provider: { provider_id: number; name: string | null; model: string | null } | null;
  traits_summary: RosterTraitSummary[];
  top_general_competencies: RosterTopCompetency[];
  top_professional_competencies: RosterTopCompetency[];
  assessment_summary: {
    assessed_general_count: number;
    general_total: number;
    evidence_coverage: "none" | "low" | "medium" | "high";
  } | null;
  recent_activity: { type: string; at: string } | null;
}

export interface CandidateEmployee {
  employee_id: number;
  name: string;
  slug: string;
  avatar: string;
  workforce_status: string;
  department_id: number | null;
  department_name: string | null;
  current_position: {
    definition_id: number;
    code: string;
    name: string;
    slot_code: string;
  } | null;
}

export interface CandidateFit {
  known_fit_score: number | null;
  fit_confidence: number | null;
  required_coverage: number;
  required_required_coverage: number;
  qualification_status: QualificationStatus;
  fit_status: FitStatus;
  critical_gap_count: number;
  required_gap_count: number;
  uncertainty_count: number;
  strengths: string[];
  gaps: string[];
  uncertainties: string[];
  development_opportunities: string[];
}

export interface CandidateItem {
  employee: CandidateEmployee;
  fit: CandidateFit;
}

export interface CandidateBandGroup {
  band: CandidateBand;
  count: number;
  candidates: CandidateItem[];
}

export interface CandidateAnalysisResult {
  position: { id: number; code: string; name: string };
  profile: { version_id: number; version: number; status: string } | null;
  evaluable: boolean;
  bands: CandidateBandGroup[];
  meta: {
    engine_version: string;
    policy_version: string;
    candidate_analysis_version: string;
    inputs_hash: string;
    include_assigned: boolean;
    calculated_at: string | null;
  };
}

// ---------- P8: Position fit (docs/position-fit.md) ----------

export const FIT_STATUSES = [
  "NOT_EVALUABLE",
  "INSUFFICIENT_DATA",
  "EVALUABLE",
  "STRONG_MATCH",
  "PARTIAL_MATCH",
  "WEAK_MATCH",
  "CRITICAL_GAP",
] as const;
export type FitStatus = (typeof FIT_STATUSES)[number];

export const QUALIFICATION_STATUSES = [
  "QUALIFIED",
  "QUALIFIED_WITH_GAPS",
  "NOT_QUALIFIED",
  "INSUFFICIENT_DATA",
] as const;
export type QualificationStatus = (typeof QUALIFICATION_STATUSES)[number];

export interface FitRequirementEvaluation {
  requirement_id: number;
  competency_definition_id: number;
  code: string;
  name: string;
  domain_code: string;
  domain_name: string;
  kind: "general" | "professional";
  requirement_type: "required" | "preferred";
  critical: boolean;
  minimum_score: number | null;
  target_score: number | null;
  minimum_confidence: number | null;
  weight: number;
  employee_score: number | null;
  employee_confidence: number | null;
  evaluation_status: string;
  reason_code: string;
  gap_type: string | null;
  is_strength: boolean;
  is_development_opportunity: boolean;
  is_unknown: boolean;
  normalized_fit: number | null;
  margin_to_minimum: number | null;
  margin_to_target: number | null;
}

export interface PositionFitResult {
  employee_id: number;
  position_definition_id: number;
  position_code: string;
  configured: boolean;
  profile_version_id: number | null;
  profile_version: number | null;
  profile_status: string | null;
  assessment_profile_code: string | null;
  fit_status: FitStatus;
  qualification_status: QualificationStatus;
  known_fit_score: number | null;
  overall_fit_score: number | null;
  fit_confidence: number | null;
  requirement_coverage: number;
  required_coverage: number;
  preferred_coverage: number;
  known_count: number;
  total_count: number;
  general_fit: number | null;
  professional_fit: number | null;
  strengths: FitRequirementEvaluation[];
  gaps: FitRequirementEvaluation[];
  uncertainties: FitRequirementEvaluation[];
  development_opportunities: FitRequirementEvaluation[];
  requirement_evaluations: FitRequirementEvaluation[];
  engine_version: string;
  policy_version: string;
  serializer_version: string;
  inputs_hash: string;
  calculated_at: string | null;
}

// ---------- v0.4: Employee lifecycle (docs/design-v0.4-lifecycle.md §10) ----------

export type LifecycleStatus =
  "pending" | "onboarding" | "active" | "transferring" | "suspended" | "offboarding" | "offboarded";

/** WorkforceStatus —— WorkforceStatusResolver 读时派生（不入库）。 */
export type WorkforceStatus =
  | "recruiting"
  | "onboarding"
  | "available"
  | "assigned"
  | "transferring"
  | "suspended"
  | "offboarding"
  | "offboarded";

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

/** 权限来源层级 —— 与后端 `EntitlementSourceOut.layer` 逐字一致（契约测试钉死）。
 * 单一来源派生：改层级只能改 ACCESS_SOURCE_LAYERS，类型会自动跟着变。 */
export const ACCESS_SOURCE_LAYERS = ["person", "position"] as const;

export type AccessSourceLayer = (typeof ACCESS_SOURCE_LAYERS)[number];

/** GET /employees/{id}/access 与 /entitlements 的 `sources[].layer` 载体。 */
export interface EntitlementSource {
  package_id: number;
  package_name: string;
  /** 人级（随人走，离职才回收）| 职位级（随编制走，卸任即 REMOVE）。 */
  layer: AccessSourceLayer;
}

/** Effective entitlement with the packages it comes from (GET /employees/{id}/entitlements). */
export interface EmployeeEntitlement {
  entitlement: Entitlement;
  sources: EntitlementSource[];
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

/* -------------------------------------------------------------------------
 * M2.1 · Canonical Executable Project（GET /projects/{id}/spec）
 *
 * 这份读模型回答产品要求的那 8 个问题；它只陈述事实，不给建议 ——
 * 「接不接受 / 怎么拆 / 选谁」仍然由管理 Agent 或 Owner 回答。
 * ---------------------------------------------------------------------- */

export interface ProjectSpecFields {
  background: string;
  goal: string;
  requirements: Array<{
    code: string;
    title: string;
    priority: string;
    acceptance_criteria: string;
  }>;
  constraints: string[];
  deliverables: string[];
  acceptance_criteria: string[];
  priority: string;
  deadline: string | null;
  context: string;
}

export interface ProjectSpecCompleteness {
  is_complete: boolean;
  missing: string[];
  optional_fields: string[];
}

export interface ProjectSpecWorkIntake {
  responsibility: string;
  status: "routed" | "no_position" | "no_incumbent" | "incumbent_unavailable";
  position_code: string;
  default_position_code: string;
  is_configured: boolean;
  position_definition_id: number | null;
  assignment: {
    employee_id: number;
    person_id: number | null;
    slot_id: number | null;
    since: string | null;
  } | null;
  candidate_employee_ids: number[];
  owner_user_id: number | null;
  reason: string;
}

export interface ProjectSpecManagement {
  employee_id: number | null;
  person_id: number | null;
  assigned_at: string | null;
  position_code: string | null;
  position_definition_id: number | null;
  current_responsible_employee_id: number | null;
  stale: boolean;
}

export interface ProjectSpecExecution {
  entered: boolean;
  task_count: number;
  task_status_counts: Record<string, number>;
  phase_count: number;
  artifact_count: number;
  planning_fixture: string | null;
}

export interface ProjectSpec {
  project_id: number;
  spec_version: number;
  spec: ProjectSpecFields;
  completeness: ProjectSpecCompleteness;
  work_mode: "guided" | "managed" | null;
  planning_fixture: "none" | "deterministic_template" | null;
  work_intake: ProjectSpecWorkIntake;
  management: ProjectSpecManagement;
  execution: ProjectSpecExecution;
  questions: Record<string, string>;
}

/* -------------------------------------------------------------------------
 * M2.2 · Role Context（GET /employees/{id}/role-context）
 *
 * 履职上下文是**派生读模型**：只陈述事实（职责 / 生效授权 / 期望引用 / 资源指针），
 * 既不含已获得的能力数值，也不含"你应该先做什么"的系统指令。
 * ---------------------------------------------------------------------- */

export interface RoleAuthorityGrant {
  kind: string;
  scope_kind: "company" | "department" | "direct_reports";
  scope_ref: number;
  max_amount: number | null;
  grant_id: number | null;
}

export interface RoleExpectationRef {
  competency_code: string;
  requirement_type: "required" | "preferred" | string;
  critical: boolean;
}

export interface RoleResourceView {
  kind: "knowledge_topic" | "playbook" | "policy" | "handbook" | "skill_hint" | string;
  ref: string;
  note: string;
  required: boolean;
  /** resolved = 指向既有内容；advisory = 按设计不指向内容；missing = 目标尚不存在 */
  resolution: "resolved" | "advisory" | "missing" | string;
  pointer: string;
}

export interface EmployeeRoleContext {
  person_id: number | null;
  employee_id: number;
  position_definition_id: number | null;
  position_code: string | null;
  department_id: number | null;
  responsibilities: string[];
  authority: RoleAuthorityGrant[];
  expectations: RoleExpectationRef[];
  advisory_scope: string[];
  resource_index: RoleResourceView[];
  direct_reports: number[];
  company_policy_keys: string[];
  current_project_ids: number[];
  knowledge_scopes: string[];
  context_version: number;
}

export interface RoleProjectBrief {
  project_id: number;
  name: string;
  status: string;
  work_mode: string | null;
  requirement_count: number;
}

export interface EmployeeRoleContextPage {
  context: EmployeeRoleContext;
  resources: RoleResourceView[];
  live_projects: RoleProjectBrief[];
  has_management_authority: boolean;
  authority_grant_count: number;
}
