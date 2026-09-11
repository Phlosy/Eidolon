import type {
  AccountStatus,
  ArtifactStatus,
  EmployeeStatus,
  LifecycleStatus,
  MilestoneStatus,
  ProjectStatus,
  ProvisioningJobStatus,
  ProvisioningStepStatus,
  TaskStatus,
} from "../types";

/** Badge variants used across the app (see components/common/badge). */
export type StatusVariant =
  "default" | "success" | "info" | "violet" | "warning" | "danger" | "muted";

export interface EmployeeStatusMeta {
  /** Tailwind classes for the status dot (token-based, theme-aware). */
  dotClass: string;
  /** Tailwind classes for the status label text. */
  textClass: string;
  /** Rounded status chip: border + soft tinted bg + text. */
  chipClass: string;
  /** Text color that auras/shimmers inherit via currentColor (pulse-ring, shimmer). */
  auraClass: string;
  /** Card hover border glow in the status color. */
  hoverClass: string;
  /** Whether the status counts as active (pulse/aura animations allowed). */
  pulse: boolean;
}

/**
 * Status → presentation map (office page spec), driven by the --status-*
 * design tokens in index.css: working=emerald, researching=blue,
 * learning=violet, reflecting=amber, meeting=cyan, idle=gray, error=red,
 * offline=dim. Labels are i18n'd at render time via
 * `employee:status.<status>` (see utils/labels.ts enumLabel).
 */
export const EMPLOYEE_STATUS_META: Record<EmployeeStatus, EmployeeStatusMeta> = {
  working: {
    dotClass: "bg-status-working",
    textClass: "text-status-working",
    chipClass: "border-status-working/30 bg-status-working/10 text-status-working",
    auraClass: "text-status-working",
    hoverClass: "hover:border-status-working/50",
    pulse: true,
  },
  researching: {
    dotClass: "bg-status-researching",
    textClass: "text-status-researching",
    chipClass: "border-status-researching/30 bg-status-researching/10 text-status-researching",
    auraClass: "text-status-researching",
    hoverClass: "hover:border-status-researching/50",
    pulse: true,
  },
  learning: {
    dotClass: "bg-status-learning",
    textClass: "text-status-learning",
    chipClass: "border-status-learning/30 bg-status-learning/10 text-status-learning",
    auraClass: "text-status-learning",
    hoverClass: "hover:border-status-learning/50",
    pulse: true,
  },
  reflecting: {
    dotClass: "bg-status-reflecting",
    textClass: "text-status-reflecting",
    chipClass: "border-status-reflecting/30 bg-status-reflecting/10 text-status-reflecting",
    auraClass: "text-status-reflecting",
    hoverClass: "hover:border-status-reflecting/50",
    pulse: true,
  },
  meeting: {
    dotClass: "bg-status-meeting",
    textClass: "text-status-meeting",
    chipClass: "border-status-meeting/30 bg-status-meeting/10 text-status-meeting",
    auraClass: "text-status-meeting",
    hoverClass: "hover:border-status-meeting/50",
    pulse: true,
  },
  idle: {
    dotClass: "bg-status-idle",
    textClass: "text-status-idle",
    chipClass: "border-status-idle/30 bg-status-idle/10 text-status-idle",
    auraClass: "text-status-idle",
    hoverClass: "hover:border-status-idle/40",
    pulse: false,
  },
  error: {
    dotClass: "bg-status-error",
    textClass: "text-status-error",
    chipClass: "border-status-error/30 bg-status-error/10 text-status-error",
    auraClass: "text-status-error",
    hoverClass: "hover:border-status-error/50",
    pulse: true,
  },
  offline: {
    dotClass: "bg-status-offline",
    textClass: "text-status-offline",
    chipClass: "border-status-offline/40 bg-status-offline/10 text-status-offline",
    auraClass: "text-status-offline",
    hoverClass: "hover:border-foreground/20",
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
  waiting_for_management: "warning",
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

/**
 * Lifecycle status → the equivalent employee runtime status whose --status-*
 * token it borrows (v0.4): active=working(emerald), onboarding=learning(violet),
 * transferring=meeting(cyan), suspended=reflecting(amber), offboarding=error(red),
 * pending=idle(gray), offboarded=offline(dim). Labels come from
 * `lifecycle:status.<status>`.
 */
export const LIFECYCLE_STATUS_SOURCE: Record<LifecycleStatus, EmployeeStatus> = {
  pending: "idle",
  onboarding: "learning",
  active: "working",
  transferring: "meeting",
  suspended: "reflecting",
  offboarding: "error",
  offboarded: "offline",
};

export function lifecycleStatusMeta(status: LifecycleStatus): EmployeeStatusMeta {
  return EMPLOYEE_STATUS_META[LIFECYCLE_STATUS_SOURCE[status]];
}

export const ACCOUNT_STATUS_VARIANT: Record<AccountStatus, StatusVariant> = {
  pending: "muted",
  provisioning: "violet",
  active: "success",
  suspended: "warning",
  failed: "danger",
  deprovisioning: "warning",
  deprovisioned: "muted",
};

export const PROVISIONING_JOB_STATUS_VARIANT: Record<ProvisioningJobStatus, StatusVariant> = {
  pending: "muted",
  running: "info",
  done: "success",
  partial: "warning",
  failed: "danger",
};

export const PROVISIONING_STEP_STATUS_VARIANT: Record<ProvisioningStepStatus, StatusVariant> = {
  pending: "muted",
  running: "info",
  done: "success",
  failed: "danger",
  skipped: "muted",
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
