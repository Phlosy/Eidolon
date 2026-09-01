import { get } from "./client";
import type { CompanyEvent, SettingsResponse } from "../types";

export function listEvents(limit = 30): Promise<CompanyEvent[]> {
  return get<CompanyEvent[]>(`/events?limit=${limit}`);
}

export function getSettings(): Promise<SettingsResponse> {
  return get<SettingsResponse>("/settings");
}
