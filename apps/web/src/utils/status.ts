import type {
  ArtifactStatus,
  EmployeeStatus,
  MilestoneStatus,
  ProjectStatus,
  TaskStatus,
} from "../types";

/** Badge variants used across the app (see components/common/badge). */
export type StatusVariant =
  "default" | "success" | "info" | "violet" | "warning" | "danger" | "muted";

export interface EmployeeStatusMeta {
  /** Tailwind classes for the status dot. */
  dotClass: string;
  /** Tailwind classes for the status label text. */
  textClass: string;
  /** Whether the dot should render the animated pulse. */
  pulse: boolean;
}

/**
 * Status → presentation map (office page spec):
 * working=green pulse, researching=blue, learning=violet, reflecting=amber,
 * idle=gray, error=red, offline=dim. Labels are i18n'd at render time via
 * `employee:status.<status>` (see utils/labels.ts enumLabel).
 */
export const EMPLOYEE_STATUS_META: Record<EmployeeStatus, EmployeeStatusMeta> = {
  working: {
    dotClass: "bg-emerald-500",
    textClass: "text-emerald-600 dark:text-emerald-400",
    pulse: true,
  },
  researching: {
    dotClass: "bg-blue-500",
    textClass: "text-blue-600 dark:text-blue-400",
    pulse: true,
  },
  learning: {
    dotClass: "bg-violet-500",
    textClass: "text-violet-600 dark:text-violet-400",
    pulse: true,
  },
  reflecting: {
    dotClass: "bg-amber-500",
    textClass: "text-amber-600 dark:text-amber-400",
    pulse: true,
  },
  meeting: {
    dotClass: "bg-cyan-500",
    textClass: "text-cyan-600 dark:text-cyan-400",
    pulse: true,
  },
  idle: {
    dotClass: "bg-gray-400 dark:bg-gray-500",
    textClass: "text-gray-500 dark:text-gray-400",
    pulse: false,
  },
  error: {
    dotClass: "bg-red-500",
    textClass: "text-red-600 dark:text-red-400",
    pulse: true,
  },
  offline: {
    dotClass: "bg-gray-300 dark:bg-gray-700",
    textClass: "text-gray-400 dark:text-gray-500",
    pulse: false,
  },
};

export const PROJECT_STATUS_VARIANT: Record<ProjectStatus, StatusVariant> = {
  requested: "info",
  planning: "violet",
  in_progress: "success",
  in_review: "warning",
  completed: "default",
  cancelled: "muted",
  rejected: "danger",
};

export const TASK_STATUS_VARIANT: Record<TaskStatus, StatusVariant> = {
  backlog: "muted",
  todo: "default",
  in_progress: "success",
  in_review: "warning",
  done: "default",
  failed: "danger",
  rejected: "danger",
};

export const MILESTONE_STATUS_VARIANT: Record<MilestoneStatus, StatusVariant> = {
  pending: "muted",
  in_progress: "warning",
  completed: "success",
};

export const ARTIFACT_STATUS_VARIANT: Record<ArtifactStatus, StatusVariant> = {
  draft: "muted",
  in_review: "warning",
  approved: "success",
  rejected: "danger",
};

/** Node accent classes for the project workflow graph, keyed by task status. */
export const TASK_STATUS_NODE_CLASS: Record<TaskStatus, string> = {
  backlog: "border-border bg-card text-muted-foreground",
  todo: "border-border bg-card text-foreground",
  in_progress: "border-emerald-500/60 bg-emerald-500/10 text-foreground",
  in_review: "border-amber-500/60 bg-amber-500/10 text-foreground",
  done: "border-emerald-500/40 bg-card text-muted-foreground",
  failed: "border-red-500/60 bg-red-500/10 text-foreground",
  rejected: "border-red-500/60 bg-red-500/10 text-foreground",
};
