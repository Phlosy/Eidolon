import { useMemo, useState } from "react";
import { ChevronDown, ChevronRight, Minus, Plus, UserRound } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import type {
  Employee,
  MilestoneStatus,
  ProjectStatus,
  ProjectTimeline,
  TaskStatus,
} from "../../types";
import { cn } from "../../utils/cn";
import { initials } from "../../utils/format";
import {
  buildProjectSchedule,
  completionPercent,
  taskProgress,
  type ScheduleRange,
} from "../../utils/project-schedule";
import { Button } from "../common/button";

const DAY_MS = 24 * 60 * 60 * 1000;

type TimelineStatus = ProjectStatus | MilestoneStatus | TaskStatus;

interface TimelineRow {
  id: string;
  level: 0 | 1 | 2;
  label: string;
  meta: string;
  ownerId: number | null;
  status: TimelineStatus;
  progress: number;
  range: ScheduleRange;
  href?: string;
  toggle?: () => void;
  expanded?: boolean;
}

interface ProjectGanttChartProps {
  projects: ProjectTimeline[];
  employees: Employee[];
  mode?: "project" | "portfolio";
}

function statusTone(status: TimelineStatus): string {
  if (status === "completed" || status === "done") return "bg-success";
  if (status === "failed" || status === "rejected" || status === "cancelled") return "bg-danger";
  if (status === "in_progress") return "bg-primary";
  if (status === "in_review") return "bg-warning";
  return "bg-muted-foreground";
}

function formatDay(date: Date): string {
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function ProjectGanttChart({
  projects,
  employees,
  mode = "project",
}: ProjectGanttChartProps) {
  const { t } = useTranslation();
  const [dayWidth, setDayWidth] = useState(mode === "portfolio" ? 30 : 38);
  const [expandedProjects, setExpandedProjects] = useState<Set<number>>(
    () =>
      new Set(
        mode === "project"
          ? projects.map((project) => project.id)
          : projects.slice(0, 2).map((project) => project.id),
      ),
  );
  const [expandedMilestones, setExpandedMilestones] = useState<Set<number>>(
    () =>
      new Set(
        mode === "project"
          ? projects.flatMap((project) => project.milestones.map((milestone) => milestone.id))
          : [],
      ),
  );
  const employeeById = useMemo(
    () => new Map(employees.map((employee) => [employee.id, employee])),
    [employees],
  );

  const rows = useMemo(() => {
    const result: TimelineRow[] = [];
    for (const project of projects) {
      const schedule = buildProjectSchedule(project, project.milestones, project.tasks);
      result.push({
        id: `project-${project.id}`,
        level: 0,
        label: project.name,
        meta: t("project:timeline.project"),
        ownerId: project.owner_id,
        status: project.status,
        progress: completionPercent(project.tasks),
        range: schedule.project,
        href: `/projects/${project.id}`,
        expanded: expandedProjects.has(project.id),
        toggle: () =>
          setExpandedProjects((current) => {
            const next = new Set(current);
            if (next.has(project.id)) next.delete(project.id);
            else next.add(project.id);
            return next;
          }),
      });
      if (!expandedProjects.has(project.id)) continue;

      for (const milestone of [...project.milestones].sort((a, b) => a.order - b.order)) {
        const childTasks = project.tasks.filter((task) => task.milestone_id === milestone.id);
        const ownerId =
          milestone.owner_id ??
          childTasks.find((task) => task.assignee_id != null)?.assignee_id ??
          project.owner_id;
        const range = schedule.milestones.get(milestone.id);
        if (!range) continue;
        result.push({
          id: `milestone-${milestone.id}`,
          level: 1,
          label: milestone.name,
          meta: t("project:timeline.milestone"),
          ownerId: ownerId ?? null,
          status: milestone.status,
          progress: completionPercent(childTasks),
          range,
          expanded: expandedMilestones.has(milestone.id),
          toggle: () =>
            setExpandedMilestones((current) => {
              const next = new Set(current);
              if (next.has(milestone.id)) next.delete(milestone.id);
              else next.add(milestone.id);
              return next;
            }),
        });
        if (!expandedMilestones.has(milestone.id)) continue;
        for (const task of childTasks.sort((a, b) => a.sequence - b.sequence)) {
          const taskRange = schedule.tasks.get(task.id);
          if (!taskRange) continue;
          result.push({
            id: `task-${task.id}`,
            level: 2,
            label: task.title,
            meta: t("project:timeline.task"),
            ownerId: task.assignee_id,
            status: task.status,
            progress: taskProgress(task.status),
            range: taskRange,
          });
        }
      }

      const ungrouped = project.tasks.filter((task) => task.milestone_id == null);
      for (const task of ungrouped.sort((a, b) => a.sequence - b.sequence)) {
        const taskRange = schedule.tasks.get(task.id);
        if (!taskRange) continue;
        result.push({
          id: `task-${task.id}`,
          level: 1,
          label: task.title,
          meta: t("project:timeline.preparation"),
          ownerId: task.assignee_id,
          status: task.status,
          progress: taskProgress(task.status),
          range: taskRange,
        });
      }
    }
    return result;
  }, [expandedMilestones, expandedProjects, projects, t]);

  const bounds = useMemo(() => {
    const today = new Date();
    const starts = rows.map((row) => row.range.start.getTime());
    const ends = rows.map((row) => row.range.end.getTime());
    const start = new Date(Math.min(today.getTime(), ...starts));
    start.setHours(0, 0, 0, 0);
    const end = new Date(Math.max(today.getTime() + 14 * DAY_MS, ...ends));
    end.setHours(0, 0, 0, 0);
    return { start, end };
  }, [rows]);
  const totalDays = Math.max(
    14,
    Math.ceil((bounds.end.getTime() - bounds.start.getTime()) / DAY_MS) + 1,
  );
  const unitDays = totalDays > 365 ? 30 : totalDays > 90 ? 7 : 1;
  const columnCount = Math.ceil(totalDays / unitDays);
  const pixelsPerDay = dayWidth / unitDays;
  const timelineWidth = Math.max(720, columnCount * dayWidth);
  const todayOffset = ((Date.now() - bounds.start.getTime()) / DAY_MS) * pixelsPerDay;
  const scaleLabel = unitDays === 30 ? "monthScale" : unitDays === 7 ? "weekScale" : "dayScale";

  if (projects.length === 0) {
    return (
      <div className="rounded-2xl border border-dashed border-border p-10 text-center text-sm text-muted-foreground">
        {t("project:timeline.empty")}
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded-2xl border border-border bg-background/35">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-4 py-3">
        <div>
          <p className="text-xs font-semibold">
            {t(
              mode === "portfolio"
                ? "project:timeline.companyTitle"
                : "project:timeline.projectTitle",
            )}
          </p>
          <p className="mt-1 text-[10px] text-muted-foreground">{t("project:timeline.liveHint")}</p>
        </div>
        <div className="flex items-center gap-1" aria-label={t("project:timeline.zoom")}>
          <Button
            variant="ghost"
            size="icon"
            className="h-8 w-8"
            onClick={() => setDayWidth((value) => Math.max(22, value - 4))}
            aria-label={t("project:timeline.zoomOut")}
          >
            <Minus className="h-3.5 w-3.5" />
          </Button>
          <span className="min-w-16 text-center text-[10px] text-muted-foreground">
            {t(`project:timeline.${scaleLabel}`)}
          </span>
          <Button
            variant="ghost"
            size="icon"
            className="h-8 w-8"
            onClick={() => setDayWidth((value) => Math.min(54, value + 4))}
            aria-label={t("project:timeline.zoomIn")}
          >
            <Plus className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>

      <div className="overflow-auto">
        <div className="grid min-w-max grid-cols-[260px_auto] md:grid-cols-[340px_auto]">
          <div className="sticky left-0 z-30 flex h-14 items-end border-b border-r border-border bg-surface px-4 pb-2 text-[10px] font-medium text-muted-foreground">
            {t("project:timeline.workBreakdown")}
          </div>
          <div
            className="relative h-14 border-b border-border bg-surface"
            style={{ width: timelineWidth }}
          >
            {Array.from({ length: columnCount }, (_, index) => {
              const date = new Date(bounds.start.getTime() + index * unitDays * DAY_MS);
              const showLabel =
                index === 0 ||
                (unitDays === 1 ? date.getDay() === 1 : unitDays === 7 ? index % 2 === 0 : true);
              return (
                <div
                  key={date.toISOString()}
                  data-testid="gantt-axis-column"
                  className={cn(
                    "absolute inset-y-0 border-l border-border/55",
                    date.getDay() === 1 && "border-border",
                  )}
                  style={{ left: index * dayWidth, width: dayWidth }}
                >
                  {showLabel ? (
                    <span className="absolute bottom-2 left-1.5 whitespace-nowrap text-[9px] text-muted-foreground">
                      {formatDay(date)}
                    </span>
                  ) : null}
                </div>
              );
            })}
            {todayOffset >= 0 && todayOffset <= timelineWidth ? (
              <div
                className="absolute inset-y-0 z-10 w-px bg-danger/60"
                style={{ left: todayOffset }}
              >
                <span className="absolute top-1 -translate-x-1/2 rounded bg-danger px-1 py-0.5 text-[8px] font-semibold text-white">
                  {t("project:timeline.today")}
                </span>
              </div>
            ) : null}
          </div>

          {rows.map((row) => {
            const owner = row.ownerId != null ? employeeById.get(row.ownerId) : undefined;
            const left = Math.max(
              0,
              ((row.range.start.getTime() - bounds.start.getTime()) / DAY_MS) * pixelsPerDay,
            );
            const width = Math.max(
              8,
              ((row.range.end.getTime() - row.range.start.getTime()) / DAY_MS) * pixelsPerDay,
            );
            return (
              <div key={row.id} className="contents">
                <div
                  className={cn(
                    "sticky left-0 z-20 flex h-14 items-center gap-2 border-b border-r border-border/70 bg-surface px-3",
                    row.level === 0 && "bg-surface-elevated",
                  )}
                  style={{ paddingLeft: 12 + row.level * 18 }}
                >
                  {row.toggle ? (
                    <button
                      type="button"
                      onClick={row.toggle}
                      className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-muted-foreground hover:bg-muted hover:text-foreground"
                      aria-label={
                        row.expanded ? t("project:timeline.collapse") : t("project:timeline.expand")
                      }
                    >
                      {row.expanded ? (
                        <ChevronDown className="h-3.5 w-3.5" />
                      ) : (
                        <ChevronRight className="h-3.5 w-3.5" />
                      )}
                    </button>
                  ) : (
                    <span className="w-7 shrink-0" />
                  )}
                  <div className="min-w-0 flex-1">
                    {row.href ? (
                      <Link
                        to={row.href}
                        className="block truncate text-xs font-semibold hover:text-primary hover:underline"
                      >
                        {row.label}
                      </Link>
                    ) : (
                      <p
                        className={cn(
                          "truncate text-xs",
                          row.level === 0 ? "font-semibold" : "font-medium",
                        )}
                      >
                        {row.label}
                      </p>
                    )}
                    <p className="mt-0.5 truncate text-[9px] text-muted-foreground">
                      {row.meta} · {owner?.name ?? t("project:tasksTable.unassigned")} ·{" "}
                      {row.progress}%
                    </p>
                  </div>
                  {owner ? (
                    <span
                      className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-border bg-background text-[9px] font-semibold"
                      title={owner.name}
                    >
                      {initials(owner.name)}
                    </span>
                  ) : (
                    <UserRound className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                  )}
                </div>
                <div
                  className="relative h-14 border-b border-border/70"
                  style={{ width: timelineWidth }}
                >
                  <div
                    className="absolute inset-0"
                    style={{
                      backgroundImage: `repeating-linear-gradient(to right, transparent 0, transparent ${dayWidth - 1}px, color-mix(in srgb, var(--border) 55%, transparent) ${dayWidth - 1}px, color-mix(in srgb, var(--border) 55%, transparent) ${dayWidth}px)`,
                    }}
                  />
                  {todayOffset >= 0 && todayOffset <= timelineWidth ? (
                    <div
                      className="absolute inset-y-0 z-10 w-px bg-danger/45"
                      style={{ left: todayOffset }}
                    />
                  ) : null}
                  <div
                    className={cn(
                      "absolute top-1/2 h-6 -translate-y-1/2 overflow-hidden rounded-lg shadow-sm",
                      statusTone(row.status),
                      row.level > 0 && "opacity-85",
                    )}
                    style={{ left, width }}
                    title={`${row.label}: ${formatDay(row.range.start)} — ${formatDay(row.range.end)}`}
                  >
                    <span
                      className="absolute inset-y-0 left-0 bg-black/18"
                      style={{ width: `${row.progress}%` }}
                    />
                    <span className="relative block truncate px-2 py-1 text-[9px] font-semibold text-white">
                      {row.progress}%
                    </span>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
