import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { ArrowRight, CircleDotDashed, FolderKanban, Target } from "lucide-react";
import type { Employee, Project, ProjectDetail } from "../../types";
import { deriveProjectProgress } from "../../utils/company-metrics";
import { enumLabel } from "../../utils/labels";
import { ProgressTrack } from "../game/progress-track";
import { Panel, SectionHeader } from "../shared/panel";

/**
 * 活跃项目 → 任务卡（quest cards）。
 *
 * 两张一排，卡片只讲一件事：这个项目推进到哪了、卡在哪、下一步点哪。
 */
export function ActiveProjectBoard({
  projects,
  details,
  employees,
}: {
  projects: Project[];
  details: ProjectDetail[];
  employees: Employee[];
}) {
  const { t } = useTranslation();
  const detailById = new Map(details.map((detail) => [detail.id, detail]));
  const employeeById = new Map(employees.map((employee) => [employee.id, employee]));
  const active = projects
    .filter((project) => !["completed", "cancelled", "rejected"].includes(project.status))
    .slice(0, 4);
  return (
    <Panel className="p-5 md:p-6">
      <SectionHeader
        kicker={t("dashboard:projects.kicker")}
        title={t("dashboard:projects.title")}
        description={t("dashboard:projects.description")}
        icon={FolderKanban}
        action={
          <Link
            to="/projects"
            className="flex items-center gap-1.5 text-xs font-medium text-primary hover:underline"
          >
            {t("dashboard:viewBoard")}
            <ArrowRight className="h-3 w-3" />
          </Link>
        }
      />
      {active.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-border p-8 text-center text-sm text-muted-foreground">
          {t("dashboard:projects.empty")}
        </div>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2">
          {active.map((project) => {
            const detail = detailById.get(project.id);
            const progress = deriveProjectProgress(detail?.tasks ?? []);
            const blockers =
              detail?.tasks.filter((task) => task.status === "failed" || task.status === "rejected")
                .length ?? 0;
            const owner = employeeById.get(project.owner_id ?? -1)?.name;
            return (
              <Link
                key={project.id}
                to={`/projects/${project.id}`}
                className="group relative flex flex-col justify-between rounded-2xl border border-border bg-background/45 p-4 transition hover:-translate-y-0.5 hover:border-border-active hover:bg-surface-elevated"
              >
                <div>
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-semibold">{project.name}</p>
                      <p className="mt-1 truncate text-[11px] text-muted-foreground">
                        {owner ?? t("dashboard:projects.unassigned")} ·{" "}
                        {enumLabel(t, "project:status", project.status)}
                      </p>
                    </div>
                    <span className="type-telemetry shrink-0 text-lg font-semibold text-primary">
                      {progress.percent}%
                    </span>
                  </div>
                  <ProgressTrack value={progress.percent} className="mt-4" />
                </div>
                <div className="mt-4 flex items-center justify-between gap-3">
                  <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[10px] text-muted-foreground">
                    <span className="flex items-center gap-1">
                      <Target className="h-3 w-3" />
                      {t("dashboard:projects.tasks", {
                        done: progress.completed,
                        total: progress.total,
                      })}
                    </span>
                    <span
                      className={
                        blockers > 0
                          ? "flex items-center gap-1 text-danger"
                          : "flex items-center gap-1"
                      }
                    >
                      <CircleDotDashed className="h-3 w-3" />
                      {t("dashboard:projects.blockers", { count: blockers })}
                    </span>
                  </div>
                  <ArrowRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground transition group-hover:translate-x-0.5 group-hover:text-primary" />
                </div>
              </Link>
            );
          })}
        </div>
      )}
    </Panel>
  );
}
