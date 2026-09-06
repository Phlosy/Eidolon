import { del, get, patch, post } from "./client";
import type {
  CompanyEvent,
  CreateEmployeeProviderInput,
  Employee,
  EmployeePerformance,
  KnowledgeItem,
  LearningPriority,
  LearningRecord,
  MemoryEntry,
  ModelBinding,
  Provider,
  Skill,
} from "../types";

export function listEmployees(): Promise<Employee[]> {
  return get<Employee[]>("/employees");
}

export function getEmployee(id: number): Promise<Employee> {
  return get<Employee>(`/employees/${id}`);
}

export function updateEmployee(id: number, body: Partial<Employee>): Promise<Employee> {
  return patch<Employee>(`/employees/${id}`, body);
}

export function getEmployeeMemory(id: number): Promise<MemoryEntry[]> {
  return get<MemoryEntry[]>(`/employees/${id}/memory`);
}

export function getEmployeeKnowledge(id: number): Promise<KnowledgeItem[]> {
  return get<KnowledgeItem[]>(`/employees/${id}/knowledge`);
}

export function getEmployeeSkills(id: number): Promise<Skill[]> {
  return get<Skill[]>(`/employees/${id}/skills`);
}

export function getEmployeeLearningRecords(id: number): Promise<LearningRecord[]> {
  return get<LearningRecord[]>(`/employees/${id}/learning-records`);
}

export function getEmployeeLearningPriorities(id: number): Promise<LearningPriority[]> {
  return get<LearningPriority[]>(`/employees/${id}/learning-priorities`);
}

export function getEmployeeActivity(id: number): Promise<CompanyEvent[]> {
  return get<CompanyEvent[]>(`/employees/${id}/activity`);
}

export function getEmployeePerformance(id: number): Promise<EmployeePerformance> {
  return get<EmployeePerformance>(`/employees/${id}/performance`);
}

/** The employee's own provider accounts plus company-shared ones (v0.3 ownership model). */
export function listEmployeeProviders(id: number): Promise<Provider[]> {
  return get<Provider[]>(`/employees/${id}/providers`);
}

/** Add a provider account (+ optional key/model) owned by this employee. */
export function createEmployeeProvider(
  id: number,
  body: CreateEmployeeProviderInput,
): Promise<Provider> {
  return post<Provider>(`/employees/${id}/providers`, body);
}

// ---- model bindings：一个员工可绑多个模型，is_primary 为默认启动 ----

export function listEmployeeBindings(id: number): Promise<ModelBinding[]> {
  return get<ModelBinding[]>(`/employees/${id}/bindings`);
}

export function addEmployeeBinding(
  id: number,
  body: { provider_id: number; model: string; alias?: string; make_primary?: boolean },
): Promise<ModelBinding> {
  return post<ModelBinding>(`/employees/${id}/bindings`, body);
}

export function updateEmployeeBinding(
  id: number,
  bindingId: number,
  body: { alias: string },
): Promise<ModelBinding> {
  return patch<ModelBinding>(`/employees/${id}/bindings/${bindingId}`, body);
}

export function setPrimaryBinding(id: number, bindingId: number): Promise<ModelBinding[]> {
  return post<ModelBinding[]>(`/employees/${id}/bindings/${bindingId}/primary`);
}

export function deleteEmployeeBinding(id: number, bindingId: number): Promise<void> {
  return del<void>(`/employees/${id}/bindings/${bindingId}`);
}
