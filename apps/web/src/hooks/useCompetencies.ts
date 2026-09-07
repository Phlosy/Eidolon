import { useQuery } from "@tanstack/react-query";
import {
  getAssessment,
  getCompetencies,
  getCompetencyDomains,
  getCompetencyExplanation,
  getEmployeeAssessments,
  getEmployeeCapabilities,
  getEmployeeCompetencyEvidence,
  getEmployeeTraits,
} from "../api/competencies";

export function useCompetencyDomains(kind?: "general" | "professional") {
  return useQuery({
    queryKey: ["competency-domains", kind ?? "all"],
    queryFn: () => getCompetencyDomains(kind),
  });
}

export function useCompetencies(domainId?: number) {
  return useQuery({
    queryKey: ["competencies", domainId],
    queryFn: () => getCompetencies(domainId),
    enabled: domainId !== undefined,
  });
}

export function useEmployeeCapabilities(employeeId: number) {
  return useQuery({
    queryKey: ["employees", employeeId, "competencies"],
    queryFn: () => getEmployeeCapabilities(employeeId),
  });
}

export function useEmployeeTraits(employeeId: number) {
  return useQuery({
    queryKey: ["employees", employeeId, "traits"],
    queryFn: () => getEmployeeTraits(employeeId),
  });
}

export function useEmployeeCompetencyEvidence(employeeId: number) {
  return useQuery({
    queryKey: ["employees", employeeId, "competency-evidence"],
    queryFn: () => getEmployeeCompetencyEvidence(employeeId),
  });
}

export function useCompetencyExplanation(employeeId: number, competency: string | null) {
  return useQuery({
    queryKey: ["employees", employeeId, "competencies", competency, "explanation"],
    queryFn: () => getCompetencyExplanation(employeeId, competency as string),
    enabled: competency !== null,
  });
}

export function useEmployeeAssessments(employeeId: number) {
  return useQuery({
    queryKey: ["employees", employeeId, "assessments"],
    queryFn: () => getEmployeeAssessments(employeeId),
  });
}

export function useAssessment(runId: number | null) {
  return useQuery({
    queryKey: ["assessments", runId],
    queryFn: () => getAssessment(runId as number),
    enabled: runId !== null,
  });
}
