/** DTOs of the v2 API, as the routers in apps/server/eidolon/api/routers shape them. */

export interface Membership {
  company_id: string;
  person_id: string;
  role: string;
  title: string | null;
}

export interface Me {
  user_id: string;
  email: string;
  display_name: string;
  person_ids: string[];
  companies: Membership[];
  session_id: string;
}

export interface SessionInfo {
  session_id: string;
  current: boolean;
  created_at: string;
  last_seen_at: string;
  expires_at: string;
  user_agent: string;
}

export interface Company {
  company_id: string;
  name: string;
  slug: string;
  currency: string;
  owner_user_id: string;
  created_at: string;
  institution?: {
    template_id: string;
    version: number;
    name: string;
    fingerprint: string;
    changes: number;
  } | null;
  headcount?: number;
}

export interface Institution {
  fingerprint: string;
  changes: number;
  adopted_fact_id: string;
  template: Record<string, unknown> & {
    id: string;
    version: number;
    name: string;
    description?: string;
    roles: Array<{ code: string; title: string; kind: string; reports_to: string | null }>;
    gates: Record<string, Record<string, unknown>>;
    authority_defaults: Record<string, string[]>;
    budget_policy: Record<string, unknown>;
  };
}

export interface Competency {
  area: string;
  score: number;
  evidence_count: number;
  rated?: boolean;
  positive?: number;
  negative?: number;
  last_evidence_at?: string;
}

export interface Employee {
  employment_id: string;
  person_id: string;
  role: string;
  title: string | null;
  department_id: string | null;
  status: string;
  started_at: string;
  ended_at: string | null;
  name: string;
  kind: "human" | "agent" | null;
  competencies: Competency[];
}

export interface ResumeEntry {
  at: string;
  kind: string;
  company_id: string;
  area: string | null;
  [key: string]: unknown;
}

export interface PersonProfile {
  person_id: string;
  name: string;
  kind: string;
  persona: Record<string, unknown>;
  employments: Array<{
    company_id: string;
    role: string;
    title: string | null;
    status: string;
    started_at: string;
    ended_at: string | null;
  }>;
  competencies: Competency[];
  evidence_count: number;
  spend_micro: number;
  resume?: ResumeEntry[];
}

export interface Mission {
  mission_id: string;
  title: string;
  goal: string;
  success_criteria: string[];
  constraints: string[];
  budget: number | null;
  deadline: string | null;
  status: string;
  decision_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface Project {
  project_id: string;
  company_id: string;
  mission_id: string | null;
  title: string;
  budget: number;
  manager: string | null;
  status: string;
  plan_decision_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface Review {
  reviewer: string;
  verdict: string;
  reason?: string | null;
  decision_id?: string;
  run_id?: string;
}

export interface Task {
  task_id: string;
  project_id: string;
  title: string;
  description: string;
  acceptance_criteria: string[];
  kind: string;
  role: string | null;
  status: string;
  assignee: string | null;
  depends_on: string[];
  inputs: string[];
  rework_count: number;
  current_run_id: string | null;
  reviews: Review[];
  created_at: string;
  updated_at: string;
}

export interface Artifact {
  artifact_id: string;
  project_id: string;
  task_id: string;
  run_id: string | null;
  path: string;
  blob_ref: string;
  size: number;
  media_type: string;
  uses: Array<Record<string, unknown>>;
  created_at: string;
}

/** R7-b: a story folded from the facts; every line names the facts behind it. */
export interface ReportLine {
  text: string;
  facts: string[];
  link: { kind: string; id: string } | null;
}
export interface ReportSection {
  key: string;
  title: string;
  lines: ReportLine[];
}
export interface Report {
  kind: "project" | "mission";
  id: string;
  company_id: string;
  title: string;
  status: string;
  lang: string;
  as_of_fact_id: string | null;
  sections: ReportSection[];
  needs_you: Array<{
    decision_id: string;
    gate: string;
    requested_at: string | null;
    recommendation: string | null;
    summary: string | null;
    advisor: string | null;
  }>;
}

export interface ProjectDetail extends Project {
  tasks: Task[];
  artifacts: Artifact[];
  spend_micro: number;
}

export interface TaskDetail extends Task {
  artifacts: Artifact[];
  spend_micro: number;
}

export interface RunEvent {
  seq: number;
  fact_id: string;
  kind: string;
  at: string;
  actor: { kind: string; id: string | null };
  causation_id: string | null;
  payload: Record<string, unknown>;
}

export interface RunSummary {
  run_id: string;
  person_id: string | null;
  task_id: string | null;
  model: string | null;
  status: string;
  started_at: string;
  cause_fact_id: string | null;
}

export interface RunDetail {
  run_id: string;
  company_id: string;
  person_id: string | null;
  task_id: string | null;
  model: string | null;
  status: string;
  steps_completed: number;
  usage: Record<string, number>;
  error: string | null;
  stop_reason: string | null;
  cause_fact_id: string | null;
  started_at: string;
  ended_at: string | null;
  spend_micro: number;
  events: RunEvent[];
}

export interface Account {
  code: string;
  name: string;
  kind: string;
  currency: string;
  debit_total_micro: number;
  credit_total_micro: number;
  balance_micro: number;
}

export interface Ledger {
  company_id: string;
  spend_micro: number;
  accounts: Account[];
}

export interface LedgerVerify {
  ok: boolean;
  accounts: number;
  entries: number;
  costs: number;
  unposted: number;
  projection_lag: number;
  debit_total: number;
  credit_total: number;
  problems: string[];
}

export interface RunCost {
  run_id: string;
  person_id: string | null;
  micro: number;
}

export interface ProjectCosts {
  project_id: string;
  currency: string | null;
  total_micro: number;
  task_micro: number;
  management_micro: number;
  tasks: Array<{ task_id: string; title: string; status: string; micro: number; runs: RunCost[] }>;
  management: RunCost[];
}

export interface CostTrace {
  entry_fact_id: string | null;
  entry_id: string | null;
  cost_fact_id: string;
  model_call_fact_id: string;
  run_id: string | null;
  person_id: string | null;
  task_id: string | null;
  task_title: string | null;
  project_id: string | null;
  mission_id: string | null;
  decision_id: string | null;
  model: string;
  provider: string;
  mode: string | null;
  usage: Record<string, number>;
  source: { amount_micro: number; currency: string } | null;
  booked: { amount_micro: number; currency: string; fx_rate: string } | null;
  posted: boolean;
  reason: string | null;
  missing: string[];
  chain: Array<{ seq: number; fact_id: string; kind: string; at: string }>;
}

export interface FactView {
  fact_id: string;
  seq: number;
  kind: string;
  actor: { kind: string; id: string | null };
  causation_id: string | null;
  correlation_id: string;
  created_at: string;
  payload: Record<string, unknown>;
}

export interface Trace {
  root: string;
  facts: FactView[];
  edges: Array<{ cause: string; effect: string }>;
  depth_reached: boolean;
  missing: string[];
}

export interface Signature {
  person_id: string;
  role: string | null;
  verdict: string;
  reason: string | null;
}

export interface Decision {
  decision_id: string;
  company_id: string;
  gate: string;
  status: string;
  resolution: {
    decider: string;
    role: string | null;
    quorum: number;
    allowed_roles: string[];
    require_reason: boolean;
    [key: string]: unknown;
  };
  subject: Record<string, unknown> & { action?: Record<string, unknown> };
  proposer: string | null;
  author: string | null;
  amount: number | null;
  requested_at: string;
  signatures: Signature[];
  escalations: number;
  /** R7-a: the company's own opinion, written by the advising agent. */
  brief?: Brief | null;
  applied?: string | null;
}

export interface Brief {
  recommendation: "approve" | "reject" | "rework" | string;
  summary: string;
  rationale: string;
  risks: string[];
  alternatives: string[];
  advisor: string | null;
  advisor_role: string | null;
  cost: string | null;
}

export interface Passkey {
  credential_id: string;
  name: string;
  transports: string[];
  device_type: string;
  created_at: string;
  last_used_at: string | null;
}

export interface WebAuthnOptions {
  challenge: string;
  public_key: Record<string, unknown>;
}

export interface ProviderCredential {
  configured: boolean;
  company_id?: string;
  kind?: string;
  base_url?: string | null;
  model?: string | null;
  key_tail?: string;
  updated_at?: string;
  updated_by?: string;
}
