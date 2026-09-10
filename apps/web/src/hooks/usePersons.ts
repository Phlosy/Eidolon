import { useQuery } from "@tanstack/react-query";
import {
  getPersonEvidence,
  getPersonProfile,
  getPersonTimeline,
  type PersonInclude,
} from "../api/persons";

/**
 * T2.1 Person 读面 hooks。query key 以 `["persons", id, ...]` 为前缀，
 * 使培养/市场/员工三处页面对同一 person 共享同一份缓存。
 */
export function usePersonProfile(
  personId: number | null,
  options: { include?: PersonInclude[]; timelineLimit?: number; evidenceLimit?: number } = {},
) {
  const include = options.include ?? [];
  return useQuery({
    queryKey: ["persons", personId, "profile", { include: [...include].sort(), ...options }],
    queryFn: () => getPersonProfile(personId!, options),
    enabled: personId != null,
  });
}

export function usePersonTimeline(personId: number | null, options: { limit?: number } = {}) {
  return useQuery({
    queryKey: ["persons", personId, "timeline", options],
    queryFn: () => getPersonTimeline(personId!, options),
    enabled: personId != null,
  });
}

export function usePersonEvidence(
  personId: number | null,
  options: { limit?: number; sourceType?: string } = {},
) {
  return useQuery({
    queryKey: ["persons", personId, "evidence", options],
    queryFn: () => getPersonEvidence(personId!, options),
    enabled: personId != null,
  });
}
