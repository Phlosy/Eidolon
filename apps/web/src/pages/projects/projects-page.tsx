import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ChartNoAxesGantt, FolderKanban, LayoutGrid, Plus, Radio, Target } from "lucide-react";
import { useProjectPortfolio } from "../../hooks/useProjects";
import { useEmployees } from "../../hooks/useEmployees";
import { ProjectCommandCard } from "../../components/project/project-command-card";
import { ProjectGanttChart } from "../../components/project/project-gantt-chart";
import { NewProjectDialog } from "../../components/project/new-project-dialog";
import { Button } from "../../components/common/button";
import { EmptyState, ErrorState, PageHeader } from "../../components/common/states";
import { Skeleton } from "../../components/common/skeleton";
import { formatRelativeTime } from "../../utils/format";

type ProjectsView = "timeline" | "cards";

export function ProjectsPage() {
  const { t } = useTranslation();
  const portfolioQuery = useProjectPortfolio();
  const employees = useEmployees().data ?? [];
  const [dialogOpen, setDialogOpen] = useState(false);
  const [view, setView] = useState<ProjectsView>("timeline");
  const projects = portfolioQuery.data ?? [];
  const active = projects.filter(
    (project) => !["completed", "cancelled", "rejected"].includes(project.status),
  ).length;
  const completed = projects.filter((project) => project.status === "completed").length;

  return (
    <div className="space-y-5 panel-enter">
      <PageHeader
        icon={FolderKanban}
        title={t("project:listTitle")}
        description={t("project:listDescription")}
        actions={
          <Button data-tutorial-target="create-project" onClick={() => setDialogOpen(true)}>
            <Plus className="h-4 w-4" />
            {t("project:newProject")}
          </Button>
        }
      />

      <section className="command-panel relative overflow-hidden p-5 md:p-6">
        <div className="relative flex flex-wrap items-center justify-between gap-6">
          <div>
            <p className="type-kicker text-primary">{t("project:command.boardKicker")}</p>
            <h2 className="mt-2 text-2xl font-semibold tracking-tight">
              {t("project:timeline.companyTitle")}
            </h2>
            <p className="mt-2 max-w-2xl text-xs leading-5 text-muted-foreground">
              {t("project:timeline.companyDescription")}
            </p>
            {portfolioQuery.dataUpdatedAt ? (
              <p
                className="mt-3 inline-flex items-center gap-1.5 text-[10px] text-muted-foreground"
                role="status"
                aria-live="polite"
              >
                <Radio className="h-3 w-3 text-success status-pulse" />
                {t("project:timeline.updated", {
                  time: formatRelativeTime(new Date(portfolioQuery.dataUpdatedAt).toISOString()),
                })}
              </p>
            ) : null}
          </div>
          <div className="flex gap-3">
            <div className="min-w-28 rounded-2xl border border-success/20 bg-success/7 p-4">
              <Radio className="h-4 w-4 text-success status-pulse" />
              <p className="type-telemetry mt-4 text-2xl font-semibold">{active}</p>
              <p className="text-[10px] text-muted-foreground">
                {t("project:command.activeMissions")}
              </p>
            </div>
            <div className="min-w-28 rounded-2xl border border-border bg-background/45 p-4">
              <Target className="h-4 w-4 text-primary" />
              <p className="type-telemetry mt-4 text-2xl font-semibold">{completed}</p>
              <p className="text-[10px] text-muted-foreground">
                {t("project:command.completedMissions")}
              </p>
            </div>
          </div>
        </div>
      </section>

      <div className="flex items-center justify-between gap-3">
        <h2 className="text-base font-semibold">{t("project:timeline.portfolioView")}</h2>
        <div className="inline-flex rounded-xl border border-border bg-background/45 p-1">
          <Button
            variant={view === "timeline" ? "secondary" : "ghost"}
            size="sm"
            className="h-8"
            onClick={() => setView("timeline")}
          >
            <ChartNoAxesGantt className="h-3.5 w-3.5" />
            {t("project:timeline.gantt")}
          </Button>
          <Button
            variant={view === "cards" ? "secondary" : "ghost"}
            size="sm"
            className="h-8"
            onClick={() => setView("cards")}
          >
            <LayoutGrid className="h-3.5 w-3.5" />
            {t("project:timeline.cards")}
          </Button>
        </div>
      </div>

      {portfolioQuery.isLoading ? (
        <Skeleton className="h-[560px] rounded-[var(--radius-panel)]" />
      ) : portfolioQuery.isError ? (
        <ErrorState error={portfolioQuery.error} onRetry={() => portfolioQuery.refetch()} />
      ) : projects.length === 0 ? (
        <EmptyState title={t("project:emptyTitle")} hint={t("project:emptyHint")} />
      ) : view === "timeline" ? (
        <section className="command-panel p-3 md:p-5">
          <ProjectGanttChart projects={projects} employees={employees} mode="portfolio" />
        </section>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2 2xl:grid-cols-3">
          {projects.map((project) => (
            <ProjectCommandCard
              key={project.id}
              project={project}
              owner={employees.find((employee) => employee.id === project.owner_id)}
            />
          ))}
        </div>
      )}

      <NewProjectDialog open={dialogOpen} onOpenChange={setDialogOpen} />
    </div>
  );
}
