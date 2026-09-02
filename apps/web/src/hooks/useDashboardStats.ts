import { useQueries, useQuery } from "@tanstack/react-query";
import { listDriveTree } from "../api/drive";
import { listEmployees, getEmployeePerformance } from "../api/employees";
import { getProject, listProjects } from "../api/projects";
import { deriveCompanyProgress } from "../utils/company-metrics";
import type { ProjectDetail } from "../types";

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
  tasksTotal: number;
  tasksCompleted: number;
  projectDetails: ProjectDetail[];
  companyProgress: ReturnType<typeof deriveCompanyProgress>;
  isLoading: boolean;
  isError: boolean;
}

const ACTIVE_PROJECT_STATUSES = new Set(["requested", "planning", "in_progress", "in_review"]);
const IN_PROGRESS_TASK_STATUSES = new Set(["in_progress", "in_review"]);

export function useDashboardStats(): DashboardStats {
  const employeesQuery = useQuery({ queryKey: ["employees"], queryFn: listEmployees });
  const projectsQuery = useQuery({ queryKey: ["projects"], queryFn: listProjects });
  // v0.3: artifacts live in the Drive as documents; count drive documents.
  const driveQuery = useQuery({
    queryKey: ["drive", "tree", { zone: null }],
    queryFn: () => listDriveTree(),
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

  const projectDetails = projectDetailQueries.flatMap((query) => query.data ? [query.data] : []);
  const allTasks = projectDetails.flatMap((detail) => detail.tasks);
  const tasksInProgress = allTasks.filter((task) => IN_PROGRESS_TASK_STATUSES.has(task.status)).length;
  const tasksCompleted = allTasks.filter((task) => task.status === "done").length;
  const documents = (driveQuery.data ?? []).filter((node) => node.kind === "document").length;
  const completedProjects = projects.filter((project) => project.status === "completed").length;

  const totalAttempts = performanceQueries
    .map((q) => q.data)
    .reduce((sum, perf) => sum + (perf?.attempts ?? 0), 0);

  const isLoading =
    employeesQuery.isLoading ||
    projectsQuery.isLoading ||
    driveQuery.isLoading ||
    projectDetailQueries.some((q) => q.isLoading) ||
    performanceQueries.some((q) => q.isLoading);

  const isError = employeesQuery.isError || projectsQuery.isError || driveQuery.isError;

  return {
    employeesOnline: employees.filter((e) => e.status !== "offline").length,
    employeesTotal: employees.length,
    activeProjects: projects.filter((p) => ACTIVE_PROJECT_STATUSES.has(p.status)).length,
    tasksInProgress,
    artifactsCount: documents,
    runtimeCostUsd: totalAttempts * MOCK_COST_PER_ATTEMPT,
    tasksTotal: allTasks.length,
    tasksCompleted,
    projectDetails,
    companyProgress: deriveCompanyProgress({ employees: employees.length, completedProjects, documents, completedTasks: tasksCompleted }),
    isLoading,
    isError,
  };
}
