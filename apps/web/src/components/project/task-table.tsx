import { useTranslation } from "react-i18next";
import { Badge } from "../common/badge";
import { enumLabel } from "../../utils/labels";
import { eid } from "../../utils/format";
import { TASK_STATUS_VARIANT } from "../../utils/status";
import type { Employee, Task } from "../../types";

interface TaskTableProps {
  tasks: Task[];
  employees: Employee[];
}

export function TaskTable({ tasks, employees }: TaskTableProps) {
  const { t } = useTranslation();
  if (tasks.length === 0) {
    return <p className="text-sm text-muted-foreground">{t("project:tasksEmpty")}</p>;
  }
  const employeeById = new Map(employees.map((e) => [e.id, e]));
  const sorted = [...tasks].sort((a, b) => a.sequence - b.sequence);

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border text-left text-xs text-muted-foreground">
            <th className="py-2 pr-4 font-medium">{t("project:tasksTable.id")}</th>
            <th className="py-2 pr-4 font-medium">{t("project:tasksTable.title")}</th>
            <th className="py-2 pr-4 font-medium">{t("project:tasksTable.kind")}</th>
            <th className="py-2 pr-4 font-medium">{t("project:tasksTable.status")}</th>
            <th className="py-2 font-medium">{t("project:tasksTable.assignee")}</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((task) => {
            const assignee =
              task.assignee_id != null ? employeeById.get(task.assignee_id) : undefined;
            return (
              <tr key={task.id} className="border-b border-border/60 last:border-0">
                <td className="py-2 pr-4 font-mono text-xs text-muted-foreground">
                  {eid(task.id)}
                </td>
                <td className="max-w-72 truncate py-2 pr-4 font-medium">{task.title}</td>
                <td className="py-2 pr-4">
                  <Badge variant="muted">{enumLabel(t, "project:taskKind", task.kind)}</Badge>
                </td>
                <td className="py-2 pr-4">
                  <Badge variant={TASK_STATUS_VARIANT[task.status]}>
                    {enumLabel(t, "project:taskStatus", task.status)}
                  </Badge>
                </td>
                <td className="py-2 text-muted-foreground">
                  {assignee?.name ?? t("project:tasksTable.unassigned")}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
