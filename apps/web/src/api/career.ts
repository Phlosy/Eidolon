import { get, post } from "./client";
import type { CareerOverview, CareerPlan, CareerReadiness } from "../types";

export function getCareerOverview(employeeId: number): Promise<CareerOverview> {
  return get<CareerOverview>(`/employees/${employeeId}/career`);
}

export function getCareerReadiness(
  employeeId: number,
  positionDefinitionId: number,
): Promise<CareerReadiness> {
  return get<CareerReadiness>(`/employees/${employeeId}/career-readiness/${positionDefinitionId}`);
}

export function createDevelopmentPlan(
  employeeId: number,
  targetPositionDefinitionId: number,
): Promise<CareerPlan> {
  return post(`/employees/${employeeId}/development-plans`, {
    target_position_definition_id: targetPositionDefinitionId,
  });
}

export function activateDevelopmentPlan(planId: number): Promise<CareerPlan> {
  return post(`/development-plans/${planId}/activate`);
}

export function createLearningPriority(itemId: number): Promise<unknown> {
  return post(`/development-plan-items/${itemId}/create-learning-priority`);
}

export function promoteEmployee(
  employeeId: number,
  payload: { target_definition_id: number; slot_id: number; reason: string },
): Promise<unknown> {
  return post(`/employees/${employeeId}/career/promote`, payload);
}
