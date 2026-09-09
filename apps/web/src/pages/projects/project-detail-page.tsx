import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  ArrowRight,
  Boxes,
  ChartNoAxesGantt,
  FolderOpen,
  ListChecks,
  Radio,
  ShieldCheck,
  Target,
} from "lucide-react";
import { useCompleteProjectPhase, useProject, useProjectLifecycle } from "../../hooks/useProjects";
import { useEmployees } from "../../hooks/useEmployees";
import { Badge } from "../../components/common/badge";
import { Button } from "../../components/common/button";
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
import { Body, Caption, H1, H3, Overline } from "../../components/typography/text";
import { deriveProjectProgress } from "../../utils/company-metrics";
import { enumLabel } from "../../utils/labels";
import { eid, formatDateTime, formatRelativeTime } from "../../utils/format";
import { PROJECT_STATUS_VARIANT } from "../../utils/status";
import { cn } from "../../utils/cn";

type DetailTab = "lifecycle" | "plan" | "tasks" | "files";

/**
 * 项目详情 = 项目指挥中心。
 *
 * 结构：项目横幅（是什么/到哪了）→ 下一步行动（唯一主行动）→ 分页工作区。
 * 生命周期、计划、任务、文件不再纵向堆成 7 个面板，一次只看一页。
 */
export function ProjectDetailPage() {
  const { t } = useTranslation();
  const { id } = useParams<{ id: string }>();
  const projectId = Number(id);
  const projectQuery = useProject(projectId);
  const lifecycleQuery = useProjectLifecycle(projectId);
  const completePhase = useCompleteProjectPhase(projectId);
  const employeesQuery = useEmployees();
  const [tab, setTab] = useState<DetailTab>("lifecycle");

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
  const lifecycle = lifecycleQuery.data;
  const activePhase = lifecycle?.phases.find((phase) =>
    ["in_progress", "changes_requested", "waiting_review"].includes(phase.status),
  );
  const pending = lifecycle?.pending_user_action;
  const tabs: Array<{ key: DetailTab; label: string; icon: typeof ShieldCheck }> = [
    { key: "lifecycle", label: t("project:detail.tabLifecycle"), icon: ShieldCheck },
    { key: "plan", label: t("project:detail.tabPlan"), icon: ChartNoAxesGantt },
    { key: "tasks", label: t("project:detail.tabTasks"), icon: ListChecks },
    { key: "files", label: t("project:detail.tabFiles"), icon: FolderOpen },
  ];

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
            <H1 className="mt-5">{project.name}</H1>
            <Body tone="muted" className="mt-4 max-w-2xl">
              {project.goal ?? project.description ?? t("project:command.noBrief")}
            </Body>
            <Caption tone="muted" className="mt-3 block font-mono">
              {t("project:createdAt", { time: formatDateTime(project.created_at) })} ·{" "}
              {t("project:updatedAt", { time: formatDateTime(project.updated_at) })}
            </Caption>
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
                  <Caption tone="muted" className="mt-3 block">
                    {String(label)}
                  </Caption>
                  <p className="mt-1 truncate text-sm font-semibold">{String(value)}</p>
                </div>
              );
            })}
            <div className="col-span-2 sm:col-span-4">
              <div className="mb-2 flex justify-between">
                <Caption tone="muted">{t("project:command.missionProgress")}</Caption>
                <span className="type-telemetry text-xs">{progress.percent}%</span>
              </div>
              <ProgressTrack value={progress.percent} />
            </div>
          </div>
        </div>
      </Panel>

      {lifecycle && pending ? (
        <Panel className="border-warning/35 bg-warning/8 p-5">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div className="flex items-start gap-3">
              <ShieldCheck className="mt-0.5 h-5 w-5 shrink-0 text-warning" />
              <div>
                <Overline tone="warning">{t("project:detail.nextAction")}</Overline>
                <H3 className="mt-1">
                  {t("project:detail.pendingReview", { title: pending.title })}
                </H3>
              </div>
            </div>
            <Link
              to={`/projects/${project.id}/reviews/${pending.review_id}`}
              className="inline-flex h-11 items-center gap-2 rounded-xl bg-warning px-5 text-sm font-semibold text-background shadow-[var(--glow-primary)] transition hover:brightness-105"
            >
              {t("project:detail.enterReview")}
              <ArrowRight className="h-4 w-4" />
            </Link>
          </div>
        </Panel>
      ) : lifecycle && activePhase ? (
        <Panel className="p-5">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div>
              <Overline tone="muted">{t("project:detail.nextAction")}</Overline>
              <H3 className="mt-1">
                {t("project:detail.currentPhase", { phase: activePhase.name })}
              </H3>
            </div>
            {!activePhase.gate_required ? (
              <Button
                onClick={() => completePhase.mutate(activePhase.id)}
                disabled={completePhase.isPending}
              >
                {completePhase.isPending
                  ? t("lifecycleBoard.completing")
                  : t("project:detail.completePhase")}
                <ArrowRight className="h-4 w-4" />
              </Button>
            ) : (
              <Caption tone="muted">{t("project:detail.waitingTeam")}</Caption>
            )}
          </div>
        </Panel>
      ) : null}

      {lifecycleQuery.data?.phases.length ? (
        <>
          <div
            className="flex flex-wrap gap-1 rounded-2xl border border-border bg-background/40 p-1"
            role="tablist"
            aria-label={t("project:detail.liveMission")}
          >
            {tabs.map(({ key, label, icon: Icon }) => (
              <button
                key={key}
                type="button"
                role="tab"
                aria-selected={tab === key}
                onClick={() => setTab(key)}
                className={cn(
                  "inline-flex h-10 items-center gap-2 rounded-xl px-4 text-sm transition-colors",
                  tab === key
                    ? "bg-primary/12 font-medium text-primary"
                    : "text-muted-foreground hover:bg-muted",
                )}
              >
                <Icon className="h-4 w-4" />
                {label}
              </button>
            ))}
          </div>

          {tab === "lifecycle" ? (
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

          {tab === "plan" ? (
            <>
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
            </>
          ) : null}

          {tab === "tasks" ? (
            <Panel className="p-5 md:p-6">
              <SectionHeader
                kicker={t("project:detail.tasksKicker")}
                title={t("project:tasks")}
                icon={ListChecks}
              />
              <TaskTable tasks={project.tasks} employees={employees} />
            </Panel>
          ) : null}

          {tab === "files" ? (
            <ProjectFilesSection projectId={projectId} employees={employees} />
          ) : null}
        </>
      ) : (
        <>
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
          <Panel className="p-5 md:p-6">
            <SectionHeader
              kicker={t("project:detail.tasksKicker")}
              title={t("project:tasks")}
              icon={ListChecks}
            />
            <TaskTable tasks={project.tasks} employees={employees} />
          </Panel>
          <ProjectFilesSection projectId={projectId} employees={employees} />
        </>
      )}
    </div>
  );
}
