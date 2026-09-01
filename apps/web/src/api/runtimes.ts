import { ApiError, del, get, patch, post } from "./client";
import type {
  CreateEmployeeRuntimeInput,
  EmployeeBrain,
  RuntimeImageInfo,
  RuntimeInstance,
  RuntimeLogs,
  RuntimeTypeInfo,
  UpdateRuntimeProviderInput,
} from "../types";

// ---------- Runtime types & instances ----------

export function listRuntimeTypes(): Promise<RuntimeTypeInfo[]> {
  return get<RuntimeTypeInfo[]>("/runtime-types");
}

export function listRuntimeInstances(): Promise<RuntimeInstance[]> {
  return get<RuntimeInstance[]>("/runtimes");
}

export function getRuntimeInstance(id: number): Promise<RuntimeInstance> {
  return get<RuntimeInstance>(`/runtimes/${id}`);
}

export function startRuntime(id: number): Promise<RuntimeInstance> {
  return post<RuntimeInstance>(`/runtimes/${id}/start`);
}

export function stopRuntime(id: number): Promise<RuntimeInstance> {
  return post<RuntimeInstance>(`/runtimes/${id}/stop`);
}

export function restartRuntime(id: number): Promise<RuntimeInstance> {
  return post<RuntimeInstance>(`/runtimes/${id}/restart`);
}

export function getRuntimeLogs(id: number, tail = 200): Promise<RuntimeLogs> {
  return get<RuntimeLogs>(`/runtimes/${id}/logs?tail=${tail}`);
}

// ---------- Employee runtime & brain ----------

/** The employee's runtime instance, or null when none exists (404 → null). */
export async function getEmployeeRuntime(employeeId: number): Promise<RuntimeInstance | null> {
  try {
    return await get<RuntimeInstance>(`/employees/${employeeId}/runtime`);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}

export function createEmployeeRuntime(
  employeeId: number,
  body: CreateEmployeeRuntimeInput,
): Promise<RuntimeInstance> {
  return post<RuntimeInstance>(`/employees/${employeeId}/runtime`, body);
}

export function updateEmployeeRuntimeProvider(
  employeeId: number,
  body: UpdateRuntimeProviderInput,
): Promise<RuntimeInstance> {
  return patch<RuntimeInstance>(`/employees/${employeeId}/runtime/provider`, body);
}

export function deleteEmployeeRuntime(employeeId: number): Promise<void> {
  return del<void>(`/employees/${employeeId}/runtime`);
}

export function getEmployeeBrain(employeeId: number): Promise<EmployeeBrain> {
  return get<EmployeeBrain>(`/employees/${employeeId}/brain`);
}

export function updateEmployeeBrain(
  employeeId: number,
  body: Partial<Omit<EmployeeBrain, "employee_id">>,
): Promise<EmployeeBrain> {
  return patch<EmployeeBrain>(`/employees/${employeeId}/brain`, body);
}

// ---------- Runtime images ----------

export function listRuntimeImages(): Promise<RuntimeImageInfo[]> {
  return get<RuntimeImageInfo[]>("/runtime-images");
}

export function checkRuntimeImageUpdates(): Promise<RuntimeImageInfo[]> {
  return post<RuntimeImageInfo[]>("/runtime-images/check-updates");
}

export function updateRuntimeImage(runtimeType: string): Promise<RuntimeImageInfo> {
  return post<RuntimeImageInfo>(`/runtime-images/${runtimeType}/update`);
}
