import { useQuery } from "@tanstack/react-query";
import {
  getEmployee,
  getEmployeeActivity,
  getEmployeeKnowledge,
  getEmployeeLearningRecords,
  getEmployeeMemory,
  getEmployeePerformance,
  getEmployeeSkills,
  listEmployees,
} from "../api/employees";
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
