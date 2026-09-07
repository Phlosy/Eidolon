import { useQuery } from "@tanstack/react-query";
import {
  getCompetencies,
  getCompetencyDomains,
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
