import { useQuery } from "@tanstack/react-query";
import { getCurrentPositionFit, getEmployeePositionFit } from "../api/positionFit";

export function useEmployeePositionFit(
  employeeId: number,
  positionDefinitionId: number | null,
  profileVersionId?: number,
) {
  return useQuery({
    queryKey: ["position-fit", employeeId, positionDefinitionId, profileVersionId],
    queryFn: () =>
      getEmployeePositionFit(employeeId, positionDefinitionId as number, profileVersionId),
    enabled: positionDefinitionId !== null,
  });
}

export function useCurrentPositionFit(employeeId: number) {
  return useQuery({
    queryKey: ["position-fit", employeeId, "current"],
    queryFn: () => getCurrentPositionFit(employeeId),
  });
}
