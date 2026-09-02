import { Check, CircleDot, Flag, UserRound } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { Employee, Milestone, Project, Task } from "../../types";
import { cn } from "../../utils/cn";
import { enumLabel } from "../../utils/labels";
import { buildProjectSchedule, completionPercent } from "../../utils/project-schedule";
import { MILESTONE_STATUS_VARIANT, TASK_STATUS_VARIANT } from "../../utils/status";
import { Badge } from "../common/badge";
import { ProgressTrack } from "../game/progress-track";

interface MilestoneExecutionPlanProps {
  project: Project;
  milestones: Milestone[];
  tasks: Task[];
  employees: Employee[];
}

function formatDay(value: Date): string {
  return value.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function MilestoneExecutionPlan({
  project,
  milestones,
  tasks,
  employees,
}: MilestoneExecutionPlanProps) {
  const { t } = useTranslation();
  const employeeById = new Map(employees.map((employee) => [employee.id, employee]));
  const schedule = buildProjectSchedule(project, milestones, tasks);
  const sorted = [...milestones].sort((a, b) => a.order - b.order);

  if (sorted.length === 0) {
    return <p className="text-sm text-muted-foreground">{t("project:milestonesEmpty")}</p>;
  }

  return (
    <div className="grid gap-3 xl:grid-cols-2">
      {sorted.map((milestone, index) => {
        const childTasks = tasks
          .filter((task) => task.milestone_id === milestone.id)
          .sort((a, b) => a.sequence - b.sequence);
        const progress = completionPercent(childTasks);
        const ownerId =
          milestone.owner_id ??
          childTasks.find((task) => task.assignee_id != null)?.assignee_id ??
          project.owner_id;
        const owner = ownerId != null ? employeeById.get(ownerId) : undefined;
        const range = schedule.milestones.get(milestone.id);

        return (
          <article
            key={milestone.id}
            className={cn(
              "rounded-2xl border bg-background/45 p-4",
              milestone.status === "in_progress"
                ? "border-primary/35 shadow-[var(--glow-primary)]"
                : "border-border",
            )}
          >
            <div className="flex items-start gap-3">
              <span
                className={cn(
                  "flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border text-xs font-semibold",
                  milestone.status === "completed"
                    ? "border-success/25 bg-success/10 text-success"
                    : milestone.status === "in_progress"
                      ? "border-primary/25 bg-primary/10 text-primary"
                      : "border-border bg-muted text-muted-foreground",
                )}
              >
                {milestone.status === "completed" ? (
                  <Check className="h-4 w-4" />
                ) : (
                  String(index + 1).padStart(2, "0")
                )}
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="min-w-0">
                    <h3 className="truncate text-sm font-semibold">{milestone.name}</h3>
                    <p className="mt-1 text-[10px] text-muted-foreground">
                      {range ? `${formatDay(range.start)} — ${formatDay(range.end)}` : "—"}
                    </p>
                  </div>
                  <Badge variant={MILESTONE_STATUS_VARIANT[milestone.status]}>
                    {enumLabel(t, "project:milestoneStatus", milestone.status)}
                  </Badge>
                </div>

                <div className="mt-4 grid grid-cols-[1fr_auto] items-center gap-3">
                  <ProgressTrack value={progress} />
                  <span className="type-telemetry text-xs font-semibold text-primary">
                    {progress}%
                  </span>
                </div>
                <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[10px] text-muted-foreground">
                  <span className="inline-flex items-center gap-1.5">
                    <UserRound className="h-3 w-3" />
                    {owner?.name ?? t("project:tasksTable.unassigned")}
                  </span>
                  <span className="inline-flex items-center gap-1.5">
                    <Flag className="h-3 w-3" />
                    {t("project:timeline.taskCount", { count: childTasks.length })}
                  </span>
                </div>
              </div>
            </div>

            <div className="mt-4 space-y-2 border-t border-border/70 pt-3">
              {childTasks.length === 0 ? (
                <p className="rounded-xl border border-dashed border-border px-3 py-4 text-center text-xs text-muted-foreground">
                  {t("project:timeline.noChildTasks")}
                </p>
              ) : (
                childTasks.map((task) => {
                  const assignee =
                    task.assignee_id != null ? employeeById.get(task.assignee_id) : undefined;
                  return (
                    <div
                      key={task.id}
                      className="grid gap-2 rounded-xl border border-border/70 bg-surface/55 px-3 py-3 sm:grid-cols-[minmax(0,1fr)_auto_auto] sm:items-center"
                    >
                      <div className="flex min-w-0 items-center gap-2">
                        <CircleDot
                          className={cn(
                            "h-3.5 w-3.5 shrink-0",
                            task.status === "done"
                              ? "text-success"
                              : task.status === "in_progress"
                                ? "text-primary"
                                : "text-muted-foreground",
                          )}
                        />
                        <span className="truncate text-xs font-medium">{task.title}</span>
                      </div>
                      <Badge variant={TASK_STATUS_VARIANT[task.status]}>
                        {enumLabel(t, "project:taskStatus", task.status)}
                      </Badge>
                      <span className="inline-flex items-center gap-1.5 text-[10px] text-muted-foreground sm:min-w-24 sm:justify-end">
                        <UserRound className="h-3 w-3" />
                        {assignee?.name ?? t("project:tasksTable.unassigned")}
                      </span>
                    </div>
                  );
                })
              )}
            </div>
          </article>
        );
      })}
    </div>
  );
}
