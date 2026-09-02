import { useEmployeePerformance, useEmployeeSkills, useTask } from "../../hooks/useEmployees";
import { EmployeeCard } from "./employee-card";
import type { Employee, RuntimeImageInfo, RuntimeInstance } from "../../types";

/** EmployeeCard wired to the API: current task, performance, validated skills. */
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
  const performanceQuery = useEmployeePerformance(employee.id);
  const skillsQuery = useEmployeeSkills(employee.id);
  const skillsValidated =
    skillsQuery.data?.filter((skill) => skill.validation_status === "validated").length ?? null;
  return (
    <EmployeeCard
      employee={employee}
      currentTask={taskQuery.data}
      runtime={runtime}
      runtimeImage={runtimeImage}
      performance={performanceQuery.data ?? null}
      skillsValidated={skillsValidated}
    />
  );
}
