import { useQueries, useQuery } from "@tanstack/react-query";
import { listArtifacts } from "../api/artifacts";
import { listEmployees, getEmployeePerformance } from "../api/employees";
import { getProject, listProjects } from "../api/projects";

/**
 * Mock-mode cost estimate per runtime task attempt, in USD.
 * The MVP has no global billing endpoint; in mock runtime mode this is a
 * deliberately simple derivation from aggregate attempt counts.
 */
const MOCK_COST_PER_ATTEMPT = 0.05;

export interface DashboardStats {
  employeesOnline: number;
  employeesTotal: number;
  activeProjects: number;
  tasksInProgress: number;
  artifactsCount: number;
  runtimeCostUsd: number;
  isLoading: boolean;
  isError: boolean;
}

const ACTIVE_PROJECT_STATUSES = new Set(["requested", "planning", "in_progress", "in_review"]);
const IN_PROGRESS_TASK_STATUSES = new Set(["in_progress", "in_review"]);

export function useDashboardStats(): DashboardStats {
  const employeesQuery = useQuery({ queryKey: ["employees"], queryFn: listEmployees });
  const projectsQuery = useQuery({ queryKey: ["projects"], queryFn: listProjects });
  const artifactsQuery = useQuery({
    queryKey: ["artifacts", {}],
    queryFn: () => listArtifacts(),
  });

  const projects = projectsQuery.data ?? [];
  const employees = employeesQuery.data ?? [];

  const projectDetailQueries = useQueries({
    queries: projects.map((project) => ({
      queryKey: ["projects", project.id],
      queryFn: () => getProject(project.id),
    })),
  });

  const performanceQueries = useQueries({
    queries: employees.map((employee) => ({
      queryKey: ["employees", employee.id, "performance"],
      queryFn: () => getEmployeePerformance(employee.id),
    })),
  });

  const tasksInProgress = projectDetailQueries
    .map((q) => q.data)
    .flatMap((detail) => detail?.tasks ?? [])
    .filter((task) => IN_PROGRESS_TASK_STATUSES.has(task.status)).length;

  const totalAttempts = performanceQueries
    .map((q) => q.data)
    .reduce((sum, perf) => sum + (perf?.attempts ?? 0), 0);

  const isLoading =
    employeesQuery.isLoading ||
    projectsQuery.isLoading ||
    artifactsQuery.isLoading ||
    projectDetailQueries.some((q) => q.isLoading) ||
    performanceQueries.some((q) => q.isLoading);

  const isError = employeesQuery.isError || projectsQuery.isError || artifactsQuery.isError;

  return {
    employeesOnline: employees.filter((e) => e.status !== "offline").length,
    employeesTotal: employees.length,
    activeProjects: projects.filter((p) => ACTIVE_PROJECT_STATUSES.has(p.status)).length,
    tasksInProgress,
    artifactsCount: artifactsQuery.data?.length ?? 0,
    runtimeCostUsd: totalAttempts * MOCK_COST_PER_ATTEMPT,
    isLoading,
    isError,
  };
}
