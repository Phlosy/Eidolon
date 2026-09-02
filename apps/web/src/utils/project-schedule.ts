import type { Milestone, Project, Task, TaskStatus } from "../types";

const DAY_MS = 24 * 60 * 60 * 1000;
const FALLBACK_PROJECT_DAYS = 18;

export interface ScheduleRange {
  start: Date;
  end: Date;
}

export interface ProjectSchedule {
  project: ScheduleRange;
  milestones: Map<number, ScheduleRange>;
  tasks: Map<number, ScheduleRange>;
}

function parseDate(value: string | null | undefined, fallback: Date): Date {
  if (!value) return new Date(fallback);
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? new Date(fallback) : parsed;
}

function addDays(date: Date, days: number): Date {
  return new Date(date.getTime() + days * DAY_MS);
}

function safeRange(start: Date, end: Date): ScheduleRange {
  return { start, end: end.getTime() > start.getTime() ? end : addDays(start, 1) };
}

/**
 * Projects created before timeline support have no planned dates. This projection
 * gives them a deterministic 18-day horizon and divides milestones/tasks across it,
 * while always preferring persisted schedule values when present.
 */
export function buildProjectSchedule(
  project: Project,
  milestones: Milestone[],
  tasks: Task[],
): ProjectSchedule {
  const fallbackStart = parseDate(project.created_at, new Date());
  const projectStart = parseDate(project.planned_start_at, fallbackStart);
  const projectEnd = parseDate(
    project.planned_end_at,
    addDays(projectStart, FALLBACK_PROJECT_DAYS),
  );
  const projectRange = safeRange(projectStart, projectEnd);
  const sortedMilestones = [...milestones].sort((a, b) => a.order - b.order);
  const milestoneRanges = new Map<number, ScheduleRange>();
  const taskRanges = new Map<number, ScheduleRange>();
  const projectDuration = projectRange.end.getTime() - projectRange.start.getTime();

  sortedMilestones.forEach((milestone, index) => {
    const slotStart = new Date(
      projectRange.start.getTime() +
        (projectDuration * index) / Math.max(1, sortedMilestones.length),
    );
    const slotEnd = new Date(
      projectRange.start.getTime() +
        (projectDuration * (index + 1)) / Math.max(1, sortedMilestones.length),
    );
    const range = safeRange(
      parseDate(milestone.planned_start_at, slotStart),
      parseDate(milestone.planned_end_at, slotEnd),
    );
    milestoneRanges.set(milestone.id, range);

    const childTasks = tasks
      .filter((task) => task.milestone_id === milestone.id)
      .sort((a, b) => a.sequence - b.sequence);
    const duration = range.end.getTime() - range.start.getTime();
    childTasks.forEach((task, taskIndex) => {
      const taskStart = new Date(
        range.start.getTime() + (duration * taskIndex) / Math.max(1, childTasks.length),
      );
      const taskEnd = new Date(
        range.start.getTime() + (duration * (taskIndex + 1)) / Math.max(1, childTasks.length),
      );
      taskRanges.set(
        task.id,
        safeRange(
          parseDate(task.planned_start_at, taskStart),
          parseDate(task.planned_end_at, taskEnd),
        ),
      );
    });
  });

  const ungrouped = tasks
    .filter((task) => task.milestone_id == null)
    .sort((a, b) => a.sequence - b.sequence);
  ungrouped.forEach((task, index) => {
    const start = addDays(projectRange.start, index);
    taskRanges.set(
      task.id,
      safeRange(
        parseDate(task.planned_start_at, start),
        parseDate(task.planned_end_at, addDays(start, 1)),
      ),
    );
  });

  return { project: projectRange, milestones: milestoneRanges, tasks: taskRanges };
}

export function taskProgress(status: TaskStatus): number {
  switch (status) {
    case "todo":
      return 12;
    case "in_progress":
      return 55;
    case "in_review":
      return 82;
    case "done":
      return 100;
    case "failed":
    case "rejected":
      return 100;
    default:
      return 0;
  }
}

export function completionPercent(tasks: Task[]): number {
  if (tasks.length === 0) return 0;
  return Math.round((tasks.filter((task) => task.status === "done").length / tasks.length) * 100);
}
