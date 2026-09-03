import { useTranslation } from "react-i18next";
import type { ReactNode } from "react";
import { BriefcaseBusiness, Cpu, Target } from "lucide-react";
import type { Department, Employee, EmployeePerformance, RuntimeInstance, Task } from "../../types";
import { EMPLOYEE_STATUS_META } from "../../utils/status";
import { enumLabel } from "../../utils/labels";
import { initials } from "../../utils/format";
import { cn } from "../../utils/cn";
import { LevelBadge, ProgressTrack } from "../game/progress-track";
import { LifecycleStatusBadge } from "../lifecycle/lifecycle-status-badge";
import { Panel } from "../shared/panel";

export function EmployeeHero({
  employee,
  department,
  runtime,
  task,
  performance,
  actions,
}: {
  employee: Employee;
  department?: Department;
  runtime?: RuntimeInstance;
  task?: Task;
  performance?: EmployeePerformance;
  actions: ReactNode;
}) {
  const { t } = useTranslation();
  const meta = EMPLOYEE_STATUS_META[employee.status];
  const xp =
    (performance?.attempts ?? 0) * 12 +
    (performance?.success_count ?? 0) * 18 +
    (performance?.learning_records_count ?? 0) * 8;
  const level = Math.floor(xp / 180) + 2;
  const progress = Math.round(((xp % 180) / 180) * 100);
  return (
    <Panel className="border-primary/20 p-6 md:p-8">
      <div className="absolute right-0 top-0 h-full w-1/2 bg-[radial-gradient(circle_at_top_right,var(--status-working-glow),transparent_62%)]" />
      <div className="relative flex flex-col gap-6 xl:flex-row xl:items-start xl:justify-between">
        <div className="flex min-w-0 flex-col gap-5 sm:flex-row">
          <span
            className={cn(
              "relative flex h-24 w-24 shrink-0 items-center justify-center rounded-[1.75rem] border bg-surface text-2xl font-semibold shadow-[var(--shadow-floating)]",
              meta.auraClass,
              meta.pulse && "pulse-ring",
            )}
          >
            {initials(employee.name)}
            <span
              className={cn(
                "absolute -bottom-1 -right-1 h-4 w-4 rounded-full border-[3px] border-surface",
                meta.dotClass,
                meta.pulse && "status-pulse",
              )}
            />
          </span>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <LevelBadge level={level} label={t("employee:hero.level")} />
              <LifecycleStatusBadge status={employee.lifecycle_status} />
              <span className={cn("type-kicker rounded-lg border px-2 py-1", meta.chipClass)}>
                {enumLabel(t, "employee:status", employee.status)}
              </span>
            </div>
            <h1 className="mt-4 truncate text-3xl font-semibold tracking-[-0.04em] md:text-4xl">
              {employee.name}
            </h1>
            <p className="mt-2 text-sm text-muted-foreground">
              {employee.title ?? enumLabel(t, "employee:role", employee.role)}
              {department ? ` · ${department.name}` : ""}
            </p>
            <div className="mt-5 flex flex-wrap gap-2 text-[10px] text-muted-foreground">
              <span className="flex items-center gap-1.5 rounded-lg border border-border bg-background/45 px-2.5 py-1.5">
                <BriefcaseBusiness className="h-3 w-3 text-primary" />
                {department?.name ?? "—"}
              </span>
              <span className="flex items-center gap-1.5 rounded-lg border border-border bg-background/45 px-2.5 py-1.5">
                <Cpu className="h-3 w-3 text-secondary" />
                {runtime?.provider_name ?? enumLabel(t, "runtime:type", employee.runtime_type)}
                {runtime?.model ? ` · ${runtime.model}` : ""}
              </span>
            </div>
          </div>
        </div>
        <div className="shrink-0">{actions}</div>
      </div>
      <div className="relative mt-7 grid gap-3 lg:grid-cols-[1.4fr_.6fr]">
        <div className="rounded-2xl border border-border bg-background/45 p-4">
          <div className="flex items-center gap-2">
            <Target className="h-4 w-4 text-primary" />
            <p className="type-kicker text-muted-foreground">{t("employee:hero.currentMission")}</p>
          </div>
          <p className="mt-3 text-sm font-semibold">
            {task?.title ?? t("employee:hero.awaitingMission")}
          </p>
          {task ? (
            <p className="mt-1 font-mono text-[10px] text-muted-foreground">#EID-{task.id}</p>
          ) : null}
        </div>
        <div className="rounded-2xl border border-border bg-background/45 p-4">
          <div className="flex justify-between text-[10px] text-muted-foreground">
            <span>{t("employee:hero.experience")}</span>
            <span className="type-telemetry">{xp % 180}/180 XP</span>
          </div>
          <ProgressTrack value={progress} className="mt-3" />
          <p className="mt-3 text-[9px] text-muted-foreground">{t("employee:hero.derived")}</p>
        </div>
      </div>
    </Panel>
  );
}
