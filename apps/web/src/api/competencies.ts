import { get, post } from "./client";
import type {
  AssessmentProfileView,
  AssessmentRunDetail,
  AssessmentRunSummary,
  CompetencyDefinition,
  CompetencyDomain,
  CompetencyEvidenceView,
  CompetencyExplanation,
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

export function getCompetencyExplanation(
  id: number,
  competency: string,
): Promise<CompetencyExplanation> {
  return get<CompetencyExplanation>(`/employees/${id}/competencies/${competency}/explanation`);
}

export function getEmployeeAssessments(id: number): Promise<AssessmentRunSummary[]> {
  return get<AssessmentRunSummary[]>(`/employees/${id}/assessments`);
}

export function getAssessment(runId: number): Promise<AssessmentRunDetail> {
  return get<AssessmentRunDetail>(`/assessments/${runId}`);
}

export function getAssessmentProfiles(): Promise<AssessmentProfileView[]> {
  return get<AssessmentProfileView[]>("/assessment-profiles");
}

export function triggerAssessmentRun(id: number, codes?: string[]): Promise<AssessmentRunDetail> {
  return post<AssessmentRunDetail>(`/employees/${id}/assessments/run`, {
    profile_code: codes?.[0],
  });
}
