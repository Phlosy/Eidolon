import { useQuery } from "@tanstack/react-query";
import { getVacantSlots } from "../api/talentRoster";

export function useVacantSlots() {
  return useQuery({
    queryKey: ["organizations", "vacancies"],
    queryFn: getVacantSlots,
  });
}
