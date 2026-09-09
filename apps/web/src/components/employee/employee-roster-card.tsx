import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { ArrowUpRight, BriefcaseBusiness, Cpu, Gauge } from "lucide-react";
import type { Department, Employee, RuntimeInstance } from "../../types";
import { EMPLOYEE_STATUS_META } from "../../utils/status";
import { enumLabel } from "../../utils/labels";
import { initials } from "../../utils/format";
import { cn } from "../../utils/cn";
import { LifecycleStatusBadge } from "../lifecycle/lifecycle-status-badge";
import { ProgressTrack } from "../game/progress-track";

export function EmployeeRosterCard({
  employee,
  department,
  runtime,
  view,
}: {
  employee: Employee;
  department?: Department;
  runtime?: RuntimeInstance;
  view: "grid" | "list";
}) {
  const { t } = useTranslation();
  const meta = EMPLOYEE_STATUS_META[employee.status];
  const level = 2 + ((employee.id * 7 + employee.name.length) % 18);
  const workload = employee.current_task_id ? 72 : employee.status === "offline" ? 0 : 24;
  if (view === "list")
    return (
      <Link
        to={`/employees/${employee.id}`}
        className="group grid min-h-[76px] items-center gap-3 border-b border-border/60 px-4 py-3 transition last:border-0 hover:bg-surface-interactive md:grid-cols-[minmax(180px,1.4fr)_minmax(120px,1fr)_120px_110px_120px]"
      >
        <span className="flex min-w-0 items-center gap-3">
          <span
            className={cn(
              "relative flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border bg-surface type-caption font-semibold",
              meta.auraClass,
            )}
          >
            {initials(employee.name)}
            <span
              className={cn(
                "absolute -bottom-0.5 -right-0.5 h-2.5 w-2.5 rounded-full border-2 border-surface",
                meta.dotClass,
              )}
            />
          </span>
          <span className="min-w-0">
            <span className="type-h4 block truncate">{employee.name}</span>
            <span className="type-caption block truncate text-muted-foreground">
              {employee.title ?? enumLabel(t, "employee:role", employee.role)}
            </span>
          </span>
        </span>
        <span className="type-caption hidden truncate text-muted-foreground md:block">
          {department?.name ?? "—"}
        </span>
        <span className={cn("type-kicker hidden md:block", meta.textClass)}>
          {enumLabel(t, "employee:status", employee.status)}
        </span>
        <span className="type-telemetry hidden md:block">LV {level}</span>
        <span className="hidden items-center justify-end gap-2 md:flex">
          <ProgressTrack value={workload} className="w-16" />
          <ArrowUpRight className="h-3.5 w-3.5 text-muted-foreground" />
        </span>
      </Link>
    );
  return (
    <Link
      to={`/employees/${employee.id}`}
      className={cn(
        "group command-panel relative block overflow-hidden p-5 transition duration-200 hover:-translate-y-1 hover:border-border-active",
        meta.hoverClass,
      )}
    >
      <div className="relative flex items-start justify-between gap-4">
        <span
          className={cn(
            "relative flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl border bg-surface type-h4",
            meta.auraClass,
            meta.pulse && "pulse-ring",
          )}
        >
          {initials(employee.name)}
          <span
            className={cn(
              "absolute -bottom-0.5 -right-0.5 h-3 w-3 rounded-full border-2 border-surface",
              meta.dotClass,
              meta.pulse && "status-pulse",
            )}
          />
        </span>
        <LifecycleStatusBadge status={employee.lifecycle_status} />
      </div>
      <div className="relative mt-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="type-h4 truncate">{employee.name}</h2>
            <p className="type-caption mt-0.5 truncate text-muted-foreground">
              {employee.title ?? enumLabel(t, "employee:role", employee.role)}
            </p>
          </div>
          <span className="type-telemetry shrink-0 rounded-lg border border-primary/20 bg-primary/8 px-2 py-1 text-primary">
            LV {level}
          </span>
        </div>
        <div className="type-caption mt-4 grid grid-cols-2 gap-2">
          <span className="flex items-center gap-2 rounded-xl border border-border bg-background/45 p-2.5 text-muted-foreground">
            <BriefcaseBusiness className="h-3.5 w-3.5 text-primary" />
            <span className="truncate">{department?.name ?? t("employee:roster.unassigned")}</span>
          </span>
          <span className="flex items-center gap-2 rounded-xl border border-border bg-background/45 p-2.5 text-muted-foreground">
            <Cpu className="h-3.5 w-3.5 text-secondary" />
            <span className="truncate">
              {runtime?.provider_name ?? enumLabel(t, "runtime:type", employee.runtime_type)}
            </span>
          </span>
        </div>
        <div className="mt-4 rounded-xl border border-border bg-background/45 p-3">
          <div className="mb-2 flex items-center justify-between">
            <span className="type-caption flex items-center gap-1.5 text-muted-foreground">
              <Gauge className="h-3 w-3" />
              {t("employee:roster.workload")}
            </span>
            <span className="type-telemetry">{workload}%</span>
          </div>
          <ProgressTrack value={workload} tone={workload > 85 ? "warning" : "primary"} />
          <p className="type-code mt-3 truncate text-muted-foreground">
            {employee.current_task_id
              ? t("employee:roster.currentTask", { id: employee.current_task_id })
              : t("employee:roster.available")}
          </p>
        </div>
        <div className="mt-4 flex items-center justify-between">
          <span className={cn("type-kicker", meta.textClass)}>
            {enumLabel(t, "employee:status", employee.status)}
          </span>
          <span className="type-caption flex items-center gap-1 font-medium text-primary opacity-0 transition group-hover:opacity-100">
            {t("employee:roster.manage")}
            <ArrowUpRight className="h-3 w-3" />
          </span>
        </div>
      </div>
    </Link>
  );
}
