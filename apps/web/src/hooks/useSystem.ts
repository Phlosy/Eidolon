import { useQuery } from "@tanstack/react-query";
import { getCompany } from "../api/company";
import { getSettings, listEvents } from "../api/system";

export function useCompany() {
  return useQuery({ queryKey: ["company"], queryFn: getCompany });
}

export function useSettings() {
  return useQuery({ queryKey: ["settings"], queryFn: getSettings });
}

export function useEvents(limit = 30) {
  return useQuery({
    queryKey: ["events", limit],
    queryFn: () => listEvents(limit),
  });
}
