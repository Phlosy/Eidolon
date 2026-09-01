import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { cn } from "../../utils/cn";
import { enumLabel } from "../../utils/labels";
import { eid, initials } from "../../utils/format";
import { EMPLOYEE_STATUS_META } from "../../utils/status";
import { StatusDot } from "../common/status-dot";
import { RuntimeBadge } from "../runtime/runtime-badge";
import { RuntimeUpdateBadge } from "../runtime/runtime-update-badge";
import type { Employee, RuntimeImageInfo, RuntimeInstance, Task } from "../../types";

interface EmployeeCardProps {
  employee: Employee;
  /** Resolved current task (when the employee is working on one). */
  currentTask?: Task | null;
  /** Live runtime instance, when the employee has one (drives provider/model line). */
  runtime?: RuntimeInstance | null;
  /** Image info for the employee's runtime type (drives the update badge). */
  runtimeImage?: RuntimeImageInfo | null;
  className?: string;
}

/**
 * Office floor card: avatar initials, name, title/role, runtime badge,
 * animated status dot + label, and the current task when working.
 */
export function EmployeeCard({
  employee,
  currentTask,
  runtime,
  runtimeImage,
  className,
}: EmployeeCardProps) {
  const { t } = useTranslation();
  const meta = EMPLOYEE_STATUS_META[employee.status];
  const showTask = employee.status === "working" && currentTask;

  return (
    <Link
      to={`/employees/${employee.id}`}
      className={cn(
        "block rounded-lg border border-border bg-card p-4 transition-colors hover:border-foreground/20",
        className,
      )}
    >
      <div className="flex items-start gap-3">
        <div className="relative shrink-0">
          <div className="flex h-10 w-10 items-center justify-center rounded-full bg-muted text-sm font-semibold text-foreground">
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
        <StatusDot status={employee.status} />
        <span data-testid="status-label" className={cn("text-xs font-medium", meta.textClass)}>
          {enumLabel(t, "employee:status", employee.status)}
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
        <div className="mt-2 rounded-md border border-border bg-muted/50 px-2.5 py-1.5">
          <p className="truncate text-xs text-foreground">{currentTask.title}</p>
          <p className="mt-0.5 font-mono text-[10px] text-muted-foreground">
            {eid(currentTask.id)}
          </p>
        </div>
      ) : null}
    </Link>
  );
}
