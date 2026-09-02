import { useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Boxes, ChartNoAxesGantt, ListChecks, Radio, Target } from "lucide-react";
import { useCompleteProjectPhase, useProject, useProjectLifecycle } from "../../hooks/useProjects";
import { useEmployees } from "../../hooks/useEmployees";
import { Badge } from "../../components/common/badge";
import { ErrorState } from "../../components/common/states";
import { Skeleton } from "../../components/common/skeleton";
import { MilestoneExecutionPlan } from "../../components/project/milestone-execution-plan";
import { ProjectGanttChart } from "../../components/project/project-gantt-chart";
import { ProjectFilesSection } from "../../components/project/project-files-section";
import { ProjectLifecycleBoard } from "../../components/project/project-lifecycle-board";
import { ChangeRequestPanel } from "../../components/project/change-request-panel";
import { TaskTable } from "../../components/project/task-table";
import { Panel, SectionHeader } from "../../components/shared/panel";
import { ProgressTrack } from "../../components/game/progress-track";
import { deriveProjectProgress } from "../../utils/company-metrics";
import { enumLabel } from "../../utils/labels";
import { eid, formatDateTime, formatRelativeTime } from "../../utils/format";
import { PROJECT_STATUS_VARIANT } from "../../utils/status";

export function ProjectDetailPage() {
  const { t } = useTranslation();
  const { id } = useParams<{ id: string }>();
  const projectId = Number(id);
  const projectQuery = useProject(projectId);
  const lifecycleQuery = useProjectLifecycle(projectId);
  const completePhase = useCompleteProjectPhase(projectId);
  const employeesQuery = useEmployees();

  if (projectQuery.isLoading)
    return (
      <div className="space-y-4">
        <Skeleton className="h-72 w-full rounded-[var(--radius-panel)]" />
        <Skeleton className="h-96 w-full rounded-[var(--radius-panel)]" />
      </div>
    );
  if (projectQuery.isError || !projectQuery.data)
    return <ErrorState error={projectQuery.error} onRetry={() => projectQuery.refetch()} />;

  const project = projectQuery.data;
  const employees = employeesQuery.data ?? [];
  const progress = deriveProjectProgress(project.tasks);
  const blockers = project.tasks.filter(
    (task) => task.status === "failed" || task.status === "rejected",
  ).length;
  const owner = employees.find((employee) => employee.id === project.owner_id);

  return (
    <div className="space-y-5 panel-enter">
      <Panel className="border-primary/20 p-6 md:p-8">
        <div className="absolute right-0 top-0 h-full w-1/2 bg-[radial-gradient(circle_at_top_right,var(--status-researching-glow),transparent_64%)]" />
        <div className="relative flex flex-col gap-8 xl:flex-row xl:items-end xl:justify-between">
          <div className="max-w-3xl">
            <div className="flex flex-wrap items-center gap-2">
              <span className="type-kicker text-primary">{eid(project.id)}</span>
              <Badge variant={PROJECT_STATUS_VARIANT[project.status]}>
                {enumLabel(t, "project:status", project.status)}
              </Badge>
              <span
                className="inline-flex items-center gap-1.5 text-[10px] text-muted-foreground"
                role="status"
                aria-live="polite"
              >
                <Radio className="h-3 w-3 text-success status-pulse" />
                {t("project:timeline.updated", {
                  time: formatRelativeTime(new Date(projectQuery.dataUpdatedAt).toISOString()),
                })}
              </span>
            </div>
            <h1 className="mt-5 text-3xl font-semibold tracking-[-0.04em] md:text-5xl">
              {project.name}
            </h1>
            <p className="mt-4 max-w-2xl text-sm leading-6 text-muted-foreground">
              {project.goal ?? project.description ?? t("project:command.noBrief")}
            </p>
            <p className="mt-3 font-mono text-[9px] text-muted-foreground">
              {t("project:createdAt", { time: formatDateTime(project.created_at) })} ·{" "}
              {t("project:updatedAt", { time: formatDateTime(project.updated_at) })}
            </p>
          </div>
          <div className="grid min-w-full grid-cols-2 gap-2 sm:grid-cols-4 xl:min-w-[500px]">
            {[
              [Target, t("project:command.owner"), owner?.name ?? t("project:command.unassigned")],
              [ListChecks, t("project:command.tasks"), `${progress.completed}/${progress.total}`],
              [Boxes, t("project:command.artifacts"), String(project.artifacts.length)],
              [ChartNoAxesGantt, t("project:command.blockers"), String(blockers)],
            ].map(([Icon, label, value]) => {
              const MetricIcon = Icon as typeof Target;
              return (
                <div
                  key={String(label)}
                  className="rounded-2xl border border-border bg-background/45 p-3"
                >
                  <MetricIcon className="h-3.5 w-3.5 text-primary" />
                  <p className="mt-3 text-[9px] text-muted-foreground">{String(label)}</p>
                  <p className="mt-1 truncate text-sm font-semibold">{String(value)}</p>
                </div>
              );
            })}
            <div className="col-span-2 sm:col-span-4">
              <div className="mb-2 flex justify-between text-[10px] text-muted-foreground">
                <span>{t("project:command.missionProgress")}</span>
                <span className="type-telemetry">{progress.percent}%</span>
              </div>
              <ProgressTrack value={progress.percent} />
            </div>
          </div>
        </div>
      </Panel>

      {lifecycleQuery.data?.phases.length ? (
        <>
          <Panel className="p-5 md:p-6">
            <ProjectLifecycleBoard
              lifecycle={lifecycleQuery.data}
              completing={completePhase.isPending}
              onCompletePhase={(phase) => completePhase.mutate(phase.id)}
              tasks={project.tasks}
              employees={employees}
            />
            {completePhase.isError ? (
              <p role="alert" className="mt-4 text-xs text-danger">
                {completePhase.error instanceof Error
                  ? completePhase.error.message
                  : "阶段推进失败，请重试。"}
              </p>
            ) : null}
          </Panel>
          <ChangeRequestPanel lifecycle={lifecycleQuery.data} />
        </>
      ) : null}

      <Panel className="p-5 md:p-6">
        <SectionHeader
          kicker={t("project:detail.milestonesKicker")}
          title={t("project:timeline.executionPlan")}
          description={t("project:timeline.executionDescription")}
          icon={Target}
        />
        <MilestoneExecutionPlan
          project={project}
          milestones={project.milestones}
          tasks={project.tasks}
          employees={employees}
        />
      </Panel>

      <Panel className="p-3 md:p-6">
        <SectionHeader
          kicker={t("project:detail.workflowKicker")}
          title={t("project:timeline.projectTitle")}
          description={t("project:timeline.projectDescription")}
          icon={ChartNoAxesGantt}
        />
        <ProjectGanttChart projects={[project]} employees={employees} />
      </Panel>

      <Panel className="p-5 md:p-6">
        <SectionHeader
          kicker={t("project:detail.tasksKicker")}
          title={t("project:tasks")}
          icon={ListChecks}
        />
        <TaskTable tasks={project.tasks} employees={employees} />
      </Panel>
      <ProjectFilesSection projectId={projectId} employees={employees} />
    </div>
  );
}
