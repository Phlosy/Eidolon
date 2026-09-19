import { useQuery } from "@tanstack/react-query";
import { getDashboardSummary, type DashboardProjectSummary } from "../api/dashboard";
import { useAuth } from "../features/auth/auth-context";
import { deriveCompanyProgress } from "../utils/company-metrics";

/** Mock-mode estimate per skill attempt in USD; this is not a billing total. */
const MOCK_COST_PER_ATTEMPT = 0.05;

export interface DashboardStats {
  employeesOnline: number;
  employeesTotal: number;
  activeProjects: number;
  tasksInProgress: number;
  artifactsCount: number;
  runtimeCostUsd: number;
  tasksTotal: number;
  tasksCompleted: number;
  projectSummaries: DashboardProjectSummary[];
  companyProgress: ReturnType<typeof deriveCompanyProgress>;
  isLoading: boolean;
  isError: boolean;
}

export function useDashboardStats(): DashboardStats {
  const { auth } = useAuth();
  const query = useQuery({
    queryKey: ["dashboard", "summary", auth?.user.id ?? null, auth?.company.id ?? null],
    queryFn: getDashboardSummary,
  });
  const summary = query.data;
  return {
    employeesOnline: summary?.employees_online ?? 0,
    employeesTotal: summary?.employees_total ?? 0,
    activeProjects: summary?.active_projects ?? 0,
    tasksInProgress: summary?.tasks_in_progress ?? 0,
    artifactsCount: summary?.documents_count ?? 0,
    runtimeCostUsd: (summary?.total_attempts ?? 0) * MOCK_COST_PER_ATTEMPT,
    tasksTotal: summary?.tasks_total ?? 0,
    tasksCompleted: summary?.tasks_completed ?? 0,
    projectSummaries: summary?.project_summaries ?? [],
    companyProgress: deriveCompanyProgress({
      employees: summary?.employees_total ?? 0,
      completedProjects: summary?.completed_projects ?? 0,
      documents: summary?.documents_count ?? 0,
      completedTasks: summary?.tasks_completed ?? 0,
    }),
    isLoading: query.isLoading,
    isError: query.isError,
  };
}
