import { get, post } from "./client";
import type { CandidateAnalysisResult, TalentRosterItem } from "../types";

export interface RosterParams {
  status?: string[];
  department_id?: number;
  position_code?: string;
  runtime_type?: string;
  provider_id?: number;
  competency_code?: string;
  min_competency_score?: number;
  min_competency_confidence?: number;
  trait_code?: string;
  min_trait_value?: number;
  search?: string;
  limit?: number;
  include_offboarded?: boolean;
}

export function listTalentRoster(params: RosterParams = {}): Promise<TalentRosterItem[]> {
  const query = new URLSearchParams();
  const append = (key: string, value: string | number | boolean | undefined) => {
    if (value !== undefined) query.append(key, String(value));
  };
  (params.status ?? []).forEach((value) => query.append("status", value));
  append("department_id", params.department_id);
  append("position_code", params.position_code);
  append("runtime_type", params.runtime_type);
  append("provider_id", params.provider_id);
  append("competency_code", params.competency_code);
  append("min_competency_score", params.min_competency_score);
  append("min_competency_confidence", params.min_competency_confidence);
  append("trait_code", params.trait_code);
  append("min_trait_value", params.min_trait_value);
  if (params.search) query.set("search", params.search);
  append("limit", params.limit ?? 500);
  append("include_offboarded", params.include_offboarded);
  const serialized = query.toString();
  return get<TalentRosterItem[]>(`/talent-roster${serialized ? `?${serialized}` : ""}`);
}

export function getPositionCandidates(
  positionId: number,
  params: { include_assigned?: boolean; search?: string; limit?: number } = {},
): Promise<CandidateAnalysisResult> {
  const query = new URLSearchParams();
  if (params.include_assigned) query.set("include_assigned", "true");
  if (params.search) query.set("search", params.search);
  query.set("limit", String(params.limit ?? 100));
  return get<CandidateAnalysisResult>(
    `/position-definitions/${positionId}/candidates?${query.toString()}`,
  );
}

export function getVacantSlots(): Promise<Array<Record<string, unknown>>> {
  return get<Array<Record<string, unknown>>>("/organizations/vacancies");
}

export function assignEmployeeToSlot(
  employeeId: number,
  slotId: number,
  reason = "",
): Promise<Record<string, unknown>> {
  return post(`/talent-roster/${employeeId}/assignments`, { slot_id: slotId, reason });
}
