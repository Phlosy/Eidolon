import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  getEmployee,
  getEmployeeActivity,
  getEmployeeKnowledge,
  getEmployeeLearningRecords,
  getEmployeeMemory,
  getEmployeePerformance,
  getEmployeeRoleContext,
  getEmployeeSkillUsageBenchmarks,
  getEmployeeSkillUsages,
  getEmployeeSkills,
  listEmployees,
} from "../api/employees";
import { getEmployeeBrain, getEmployeeBrainProjection, updateEmployeeBrain } from "../api/runtimes";
import type { EmployeeBrain } from "../types";
import { getTask } from "../api/tasks";

export function useEmployees() {
  return useQuery({ queryKey: ["employees"], queryFn: listEmployees });
}

export function useEmployee(id: number) {
  return useQuery({
    queryKey: ["employees", id],
    queryFn: () => getEmployee(id),
  });
}

/** 员工大脑 + 服务端解析出的 BehaviorPolicy 摘要（`brain.behavior`）。 */
export function useEmployeeBrain(id: number) {
  return useQuery({
    queryKey: ["employees", id, "brain"],
    queryFn: () => getEmployeeBrain(id),
  });
}

/** 写 traits（权威）：成功后同时刷新 brain 与投影，档位/额度立刻跟着变。 */
export function useUpdateEmployeeBrain(employeeId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Partial<Omit<EmployeeBrain, "employee_id" | "behavior">>) =>
      updateEmployeeBrain(employeeId, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["employees", employeeId, "brain"] });
    },
  });
}

export function useEmployeeBrainProjection(id: number) {
  return useQuery({
    queryKey: ["employees", id, "brain", "projection"],
    queryFn: () => getEmployeeBrainProjection(id),
  });
}

export function useEmployeeSkillUsages(id: number) {
  return useQuery({
    queryKey: ["employees", id, "skill-usages"],
    queryFn: () => getEmployeeSkillUsages(id),
  });
}

export function useEmployeeSkillUsageBenchmarks(id: number) {
  return useQuery({
    queryKey: ["employees", id, "skill-usages", "benchmarks"],
    queryFn: () => getEmployeeSkillUsageBenchmarks(id),
  });
}

export function useEmployeeMemory(id: number) {
  return useQuery({
    queryKey: ["employees", id, "memory"],
    queryFn: () => getEmployeeMemory(id),
  });
}

export function useEmployeeSkills(id: number) {
  return useQuery({
    queryKey: ["employees", id, "skills"],
    queryFn: () => getEmployeeSkills(id),
  });
}

export function useEmployeeLearningRecords(id: number) {
  return useQuery({
    queryKey: ["employees", id, "learning-records"],
    queryFn: () => getEmployeeLearningRecords(id),
  });
}

export function useEmployeeKnowledge(id: number) {
  return useQuery({
    queryKey: ["employees", id, "knowledge"],
    queryFn: () => getEmployeeKnowledge(id),
  });
}

export function useEmployeeActivity(id: number) {
  return useQuery({
    queryKey: ["employees", id, "activity"],
    queryFn: () => getEmployeeActivity(id),
  });
}

export function useEmployeePerformance(id: number, enabled = true) {
  return useQuery({
    queryKey: ["employees", id, "performance"],
    queryFn: () => getEmployeePerformance(id),
    enabled,
  });
}

export function useTask(id: number | null | undefined) {
  return useQuery({
    queryKey: ["tasks", id],
    queryFn: () => getTask(id!),
    enabled: id != null,
  });
}

/** M2.2：履职上下文（读面；不轮询 —— 只在任职变化后才有意义）。 */
export function useEmployeeRoleContext(employeeId: number) {
  return useQuery({
    queryKey: ["employees", employeeId, "role-context"],
    queryFn: () => getEmployeeRoleContext(employeeId),
    enabled: Number.isFinite(employeeId),
  });
}
