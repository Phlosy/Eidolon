import { get } from "./client";
import type { PositionFitResult } from "../types";

export function getEmployeePositionFit(
  employeeId: number,
  positionDefinitionId: number,
  profileVersionId?: number,
): Promise<PositionFitResult> {
  const params = profileVersionId ? `?profile_version_id=${profileVersionId}` : "";
  return get<PositionFitResult>(
    `/employees/${employeeId}/position-fit/${positionDefinitionId}${params}`,
  );
}

export function getCurrentPositionFit(employeeId: number): Promise<PositionFitResult | null> {
  return get<PositionFitResult | null>(`/employees/${employeeId}/position-fit/current`);
}
