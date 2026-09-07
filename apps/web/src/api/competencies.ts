import { get } from "./client";
import type {
  CompetencyDefinition,
  CompetencyDomain,
  CompetencyEvidenceView,
  EmployeeCapabilities,
  TraitView,
} from "../types";

function withQuery(path: string, params: Record<string, string | number | undefined>): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) query.set(key, String(value));
  }
  const serialized = query.toString();
  return serialized ? `${path}?${serialized}` : path;
}

export function getCompetencyDomains(
  kind?: "general" | "professional",
): Promise<CompetencyDomain[]> {
  return get<CompetencyDomain[]>(withQuery("/competency-domains", { kind }));
}

export function getCompetencies(domainId?: number): Promise<CompetencyDefinition[]> {
  return get<CompetencyDefinition[]>(withQuery("/competencies", { domain_id: domainId }));
}

export function getEmployeeCapabilities(id: number): Promise<EmployeeCapabilities> {
  return get<EmployeeCapabilities>(`/employees/${id}/competencies`);
}

export function getEmployeeTraits(id: number): Promise<TraitView[]> {
  return get<TraitView[]>(`/employees/${id}/traits`);
}

export function getEmployeeCompetencyEvidence(id: number): Promise<CompetencyEvidenceView[]> {
  return get<CompetencyEvidenceView[]>(`/employees/${id}/competency-evidence`);
}
