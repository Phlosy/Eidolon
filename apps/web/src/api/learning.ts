import { get, post } from "./client";
import type { EmployeeLearningPolicyView, LearningSessionView } from "../types";

export function getEmployeeLearningPolicy(employeeId: number): Promise<EmployeeLearningPolicyView> {
  return get<EmployeeLearningPolicyView>(`/employees/${employeeId}/learning-policy`);
}

export function getEmployeeLearningSessions(employeeId: number): Promise<LearningSessionView[]> {
  return get<LearningSessionView[]>(`/employees/${employeeId}/learning-sessions`);
}

export function startLearningSession(
  employeeId: number,
  payload: { topic: string; learning_mode?: string; reason?: string; minutes?: number },
): Promise<LearningSessionView> {
  return post(`/employees/${employeeId}/learning-sessions`, payload);
}

export function cancelLearningSession(sessionId: number): Promise<LearningSessionView> {
  return post(`/learning-sessions/${sessionId}/cancel`);
}
