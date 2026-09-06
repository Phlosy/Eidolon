import { del, get, patch, post } from "./client";
import type {
  CreateProviderInput,
  Provider,
  ProviderModels,
  ProviderPreset,
  ProviderProbeResult,
  ProviderTestResult,
  ProviderType,
  UpdateProviderInput,
} from "../types";

/** 内置厂商预设清单（静态数据，长缓存）。 */
export function listProviderPresets(): Promise<ProviderPreset[]> {
  return get<ProviderPreset[]>("/providers/presets");
}

/** 不保存配置的试连：创建对话框"探测可用模型"用，api_key 不落库。 */
export function probeProviderConfig(body: {
  provider_type: ProviderType;
  base_url?: string;
  api_key?: string;
}): Promise<ProviderProbeResult> {
  return post<ProviderProbeResult>("/providers/probe", body);
}

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
