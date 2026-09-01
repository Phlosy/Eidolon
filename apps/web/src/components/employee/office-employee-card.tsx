import { useTask } from "../../hooks/useEmployees";
import { EmployeeCard } from "./employee-card";
import type { Employee, RuntimeImageInfo, RuntimeInstance } from "../../types";

/** EmployeeCard wired to the API: resolves the employee's current task. */
export function OfficeEmployeeCard({
  employee,
  runtime,
  runtimeImage,
}: {
  employee: Employee;
  runtime?: RuntimeInstance | null;
  runtimeImage?: RuntimeImageInfo | null;
}) {
  const taskQuery = useTask(employee.current_task_id);
  return (
    <EmployeeCard
      employee={employee}
      currentTask={taskQuery.data}
      runtime={runtime}
      runtimeImage={runtimeImage}
    />
  );
}
