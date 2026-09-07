import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  assignEmployeeToSlot,
  getPositionCandidates,
  listTalentRoster,
  type RosterParams,
} from "../api/talentRoster";

export function useTalentRoster(params: RosterParams) {
  return useQuery({
    queryKey: ["talent-roster", params],
    queryFn: () => listTalentRoster(params),
  });
}

export function usePositionCandidates(positionId: number, includeAssigned = false) {
  return useQuery({
    queryKey: ["position-candidates", positionId, includeAssigned],
    queryFn: () => getPositionCandidates(positionId, { include_assigned: includeAssigned }),
  });
}

export function useAssignEmployee() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: { employeeId: number; slotId: number; reason?: string }) =>
      assignEmployeeToSlot(payload.employeeId, payload.slotId, payload.reason ?? ""),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["talent-roster"] });
      void queryClient.invalidateQueries({ queryKey: ["position-candidates"] });
    },
  });
}
