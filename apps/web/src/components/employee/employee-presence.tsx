import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { ArrowUpRight } from "lucide-react";
import type { Department, Employee } from "../../types";
import { EMPLOYEE_STATUS_META } from "../../utils/status";
import { enumLabel } from "../../utils/labels";
import { initials } from "../../utils/format";
import { cn } from "../../utils/cn";

export function EmployeePresence({ employee, department, compact = false }: { employee: Employee; department?: Department; compact?: boolean }) {
  const { t } = useTranslation();
  const status = EMPLOYEE_STATUS_META[employee.status];
  const level = 2 + ((employee.id * 7 + employee.name.length) % 18);
  return <Link to={`/employees/${employee.id}`} className={cn("group relative flex min-w-0 items-center gap-3 rounded-2xl border border-border bg-background/55 transition duration-200 hover:-translate-y-0.5 hover:bg-surface-elevated", status.hoverClass, compact ? "p-2.5" : "p-3.5")}>
    <span className={cn("relative flex shrink-0 items-center justify-center rounded-xl border bg-surface font-semibold", status.auraClass, status.pulse && "pulse-ring", compact ? "h-9 w-9 text-xs" : "h-11 w-11 text-sm")}>{initials(employee.name)}<span className={cn("absolute -bottom-0.5 -right-0.5 h-2.5 w-2.5 rounded-full border-2 border-surface", status.dotClass, status.pulse && "status-pulse")} /></span>
    <span className="min-w-0 flex-1"><span className="flex items-center justify-between gap-2"><span className="truncate text-sm font-semibold">{employee.name}</span><span className="type-telemetry text-[9px] text-muted-foreground">LV {level}</span></span><span className="mt-0.5 block truncate text-[11px] text-muted-foreground">{employee.title ?? enumLabel(t, "employee:role", employee.role)}{department ? ` · ${department.name}` : ""}</span>{!compact ? <span className="mt-2 flex items-center justify-between gap-2"><span className={cn("type-kicker truncate", status.textClass)}>{enumLabel(t, "employee:status", employee.status)}</span><span className="truncate font-mono text-[9px] text-muted-foreground">{employee.current_task_id ? `#EID-${employee.current_task_id}` : t("dashboard:workforce.available")}</span></span> : null}</span>
    {!compact ? <ArrowUpRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground opacity-0 transition group-hover:opacity-100" /> : null}
  </Link>;
}
