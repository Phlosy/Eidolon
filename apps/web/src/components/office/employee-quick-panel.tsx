import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { ArrowUpRight, BriefcaseBusiness, Cpu, Sparkles, Target, X } from "lucide-react";
import { useEmployeePerformance, useEmployeeSkills, useTask } from "../../hooks/useEmployees";
import type { Department, Employee, RuntimeInstance } from "../../types";
import { EMPLOYEE_STATUS_META } from "../../utils/status";
import { enumLabel } from "../../utils/labels";
import { initials } from "../../utils/format";
import { cn } from "../../utils/cn";

export function EmployeeQuickPanel({ employee, department, runtime, onClose }: { employee: Employee; department?: Department; runtime?: RuntimeInstance; onClose: () => void }) {
  const { t } = useTranslation();
  const task = useTask(employee.current_task_id).data;
  const performance = useEmployeePerformance(employee.id).data;
  const skills = useEmployeeSkills(employee.id).data ?? [];
  const meta = EMPLOYEE_STATUS_META[employee.status];
  const validated = skills.filter((skill) => skill.validation_status === "validated").length;
  const level = 2 + Math.floor((performance?.attempts ?? employee.id) / 4);
  return <aside className="command-panel relative overflow-hidden p-5 panel-enter xl:sticky xl:top-24"><button type="button" onClick={onClose} className="absolute right-3 top-3 flex h-10 w-10 items-center justify-center rounded-xl text-muted-foreground hover:bg-muted" aria-label={t("common:close")}><X className="h-4 w-4" /></button><div className="flex items-center gap-4 pr-10"><span className={cn("relative flex h-14 w-14 items-center justify-center rounded-2xl border bg-surface text-lg font-semibold", meta.auraClass, meta.pulse && "pulse-ring")}>{initials(employee.name)}</span><div className="min-w-0"><p className="type-kicker text-primary">{t("office:quickPanel")}</p><h2 className="mt-1 truncate text-xl font-semibold">{employee.name}</h2><p className="truncate text-xs text-muted-foreground">{employee.title ?? enumLabel(t, "employee:role", employee.role)}</p></div></div><div className={cn("mt-5 flex items-center gap-2 rounded-xl border px-3 py-2", meta.chipClass)}><span className={cn("h-2 w-2 rounded-full", meta.dotClass, meta.pulse && "status-pulse")} /><span className="type-kicker">{enumLabel(t, "employee:status", employee.status)}</span></div><dl className="mt-5 grid grid-cols-2 gap-2">{[[BriefcaseBusiness, t("office:department"), department?.name ?? "—"], [Cpu, t("office:runtime"), runtime?.runtime_type ?? employee.runtime_type], [Sparkles, t("office:level"), String(level)], [Target, t("office:validatedSkills"), String(validated)]].map(([Icon, label, value]) => { const ItemIcon = Icon as typeof Cpu; return <div key={String(label)} className="rounded-xl border border-border bg-background/45 p-3"><ItemIcon className="h-3.5 w-3.5 text-primary" /><dt className="mt-3 text-[9px] text-muted-foreground">{String(label)}</dt><dd className="mt-1 truncate text-xs font-medium">{String(value)}</dd></div>; })}</dl><div className="mt-5 rounded-xl border border-border bg-background/45 p-3"><p className="type-kicker text-muted-foreground">{t("office:currentMission")}</p><p className="mt-2 text-sm font-medium">{task?.title ?? t("office:awaitingMission")}</p>{task ? <p className="mt-1 font-mono text-[10px] text-muted-foreground">#EID-{task.id}</p> : null}</div><Link to={`/employees/${employee.id}`} className="mt-5 flex h-11 w-full items-center justify-center gap-2 rounded-xl bg-primary text-xs font-semibold text-primary-foreground shadow-[var(--glow-primary)]">{t("office:openWorkbench")}<ArrowUpRight className="h-3.5 w-3.5" /></Link></aside>;
}
