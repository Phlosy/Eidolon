import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { CheckCircle2, Gauge, Sparkles } from "lucide-react";
import { cn } from "../../utils/cn";
import { enumLabel } from "../../utils/labels";
import { eid, formatPercent, initials } from "../../utils/format";
import { EMPLOYEE_STATUS_META } from "../../utils/status";
import { useElapsedSeconds } from "../../hooks/useElapsedSeconds";
import { StatusDot } from "../common/status-dot";
import { RuntimeBadge } from "../runtime/runtime-badge";
import { RuntimeUpdateBadge } from "../runtime/runtime-update-badge";
import type {
  Employee,
  EmployeePerformance,
  EmployeeStatus,
  RuntimeImageInfo,
  RuntimeInstance,
  Task,
} from "../../types";

/** Statuses whose avatar shows the pulsing aura (actively at work). */
const AURA_STATUSES: ReadonlySet<EmployeeStatus> = new Set(["working", "researching", "learning"]);

interface EmployeeCardProps {
  employee: Employee;
  /** Resolved current task (when the employee is working on one). */
  currentTask?: Task | null;
  /** Live runtime instance, when the employee has one (drives provider/model line). */
  runtime?: RuntimeInstance | null;
  /** Image info for the employee's runtime type (drives the update badge). */
  runtimeImage?: RuntimeImageInfo | null;
  /** Aggregate performance (footer stat strip); omitted → strip hidden. */
  performance?: EmployeePerformance | null;
  /** Count of validated skills (footer stat strip). */
  skillsValidated?: number | null;
  className?: string;
}

/**
 * Office floor card: avatar with a status aura, name, title/role, runtime
 * badge, animated status chip, live elapsed timer on the current task, and a
 * game-lite footer stat strip (tasks done / success rate / validated skills).
 */
export function EmployeeCard({
  employee,
  currentTask,
  runtime,
  runtimeImage,
  performance,
  skillsValidated,
  className,
}: EmployeeCardProps) {
  const { t } = useTranslation();
  const meta = EMPLOYEE_STATUS_META[employee.status];
  const showTask = employee.status === "working" && currentTask;
  const elapsed = useElapsedSeconds(showTask ? currentTask.created_at : null);
  const hasAura = AURA_STATUSES.has(employee.status);

  return (
    <Link
      to={`/employees/${employee.id}`}
      className={cn(
        "group block rounded-xl border border-border bg-card p-4 shadow-card transition-all duration-200 hover:-translate-y-0.5 hover:shadow-lift",
        meta.hoverClass,
        className,
      )}
    >
      <div className="flex items-start gap-3">
        <div
          className={cn(
            "relative shrink-0 rounded-full transition-colors duration-300",
            meta.auraClass,
            hasAura && "pulse-ring",
          )}
        >
          <div
            className={cn(
              "flex h-10 w-10 items-center justify-center rounded-full bg-muted text-sm font-semibold text-foreground ring-2 ring-current/30 transition-shadow duration-300",
              hasAura && "[box-shadow:0_0_16px_-4px_currentColor]",
            )}
          >
            {initials(employee.name)}
          </div>
          <StatusDot
            status={employee.status}
            className="absolute -right-0.5 -bottom-0.5 ring-2 ring-card"
          />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-2">
            <p className="truncate text-sm font-semibold">{employee.name}</p>
            <RuntimeBadge type={employee.runtime_type} />
          </div>
          <p className="truncate text-xs text-muted-foreground">
            {employee.title ?? enumLabel(t, "employee:role", employee.role)}
          </p>
        </div>
      </div>

      <div className="mt-3 flex items-center gap-1.5">
        <span
          className={cn(
            "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-medium transition-colors duration-300",
            meta.chipClass,
          )}
        >
          <StatusDot status={employee.status} />
          <span data-testid="status-label">{enumLabel(t, "employee:status", employee.status)}</span>
        </span>
        {runtimeImage?.update_available ? (
          <RuntimeUpdateBadge
            updateAvailable={runtimeImage.update_available}
            compatibilityStatus={runtimeImage.compatibility_status}
          />
        ) : null}
      </div>

      {runtime?.provider_name ? (
        <p className="mt-1.5 truncate text-[11px] text-muted-foreground">
          {runtime.provider_name}
          {runtime.model ? ` · ${runtime.model}` : ""}
        </p>
      ) : null}

      {showTask ? (
        <div
          className={cn(
            "shimmer mt-2 rounded-md border border-border bg-muted/50 px-2.5 py-1.5",
            meta.auraClass,
          )}
        >
          <p className="truncate text-xs text-foreground">{currentTask.title}</p>
          <p className="mt-0.5 flex items-center justify-between font-mono text-[10px] text-muted-foreground">
            <span>{eid(currentTask.id)}</span>
            {elapsed ? (
              <span data-testid="elapsed-timer" className="tabular-nums">
                {elapsed}
              </span>
            ) : null}
          </p>
        </div>
      ) : null}

      {performance ? (
        <div className="mt-3 border-t border-border/60 pt-2.5">
          <div className="flex items-center gap-3 text-[10px] text-muted-foreground">
            <span
              className="inline-flex items-center gap-1"
              title={t("employee:performance.successes")}
            >
              <CheckCircle2 className="h-3 w-3 text-status-working" />
              <span className="font-mono font-medium tabular-nums">{performance.success_count}</span>
            </span>
            <span
              className="inline-flex items-center gap-1"
              title={t("employee:performance.successRate")}
            >
              <Gauge className="h-3 w-3 text-status-researching" />
              <span className="font-mono font-medium tabular-nums">
                {formatPercent(performance.success_rate)}
              </span>
            </span>
            {skillsValidated != null ? (
              <span className="inline-flex items-center gap-1" title={t("employee:tabs.skills")}>
                <Sparkles className="h-3 w-3 text-status-learning" />
                <span className="font-mono font-medium tabular-nums">{skillsValidated}</span>
              </span>
            ) : null}
            <span className="ml-auto h-1 w-14 overflow-hidden rounded-full bg-muted">
              <span
                className="block h-full rounded-full bg-status-working transition-[width] duration-500"
                style={{ width: `${Math.round(Math.max(0, Math.min(1, performance.success_rate)) * 100)}%` }}
              />
            </span>
          </div>
        </div>
      ) : null}
    </Link>
  );
}
