import { get } from "./client";

export interface DashboardProjectSummary {
  project_id: number;
  tasks_total: number;
  tasks_completed: number;
  blockers: number;
}

export interface DashboardSummary {
  company_id: number;
  employees_online: number;
  employees_total: number;
  active_projects: number;
  completed_projects: number;
  tasks_in_progress: number;
  tasks_total: number;
  tasks_completed: number;
  documents_count: number;
  total_attempts: number;
  /** The four newest nonterminal projects displayed by the dashboard board. */
  project_summaries: DashboardProjectSummary[];
}

export function getDashboardSummary(): Promise<DashboardSummary> {
  return get<DashboardSummary>("/dashboard/summary");
}
