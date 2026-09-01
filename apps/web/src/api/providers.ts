import { del, get, patch, post } from "./client";
import type {
  CreateProviderInput,
  Provider,
  ProviderModels,
  ProviderTestResult,
  UpdateProviderInput,
} from "../types";

/** List providers; pass `employeeId` to scope the list to one employee (company + their private). */
export function listProviders(employeeId?: number): Promise<Provider[]> {
  const query = employeeId != null ? `?employee_id=${employeeId}` : "";
  return get<Provider[]>(`/providers${query}`);
}

export function getProvider(id: number): Promise<Provider> {
  return get<Provider>(`/providers/${id}`);
}

export function createProvider(body: CreateProviderInput): Promise<Provider> {
  return post<Provider>("/providers", body);
}

/** PATCH may include `api_key` to replace the stored credential (write-only). */
export function updateProvider(id: number, body: UpdateProviderInput): Promise<Provider> {
  return patch<Provider>(`/providers/${id}`, body);
}

export function deleteProvider(id: number): Promise<void> {
  return del<void>(`/providers/${id}`);
}

export function testProvider(id: number): Promise<ProviderTestResult> {
  return post<ProviderTestResult>(`/providers/${id}/test`);
}

export function listProviderModels(id: number): Promise<ProviderModels> {
  return get<ProviderModels>(`/providers/${id}/models`);
}
