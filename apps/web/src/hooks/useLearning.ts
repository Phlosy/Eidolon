import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  cancelLearningSession,
  getEmployeeLearningPolicy,
  getEmployeeLearningSessions,
  startLearningSession,
} from "../api/learning";

export function useEmployeeLearning(employeeId: number) {
  const policyQuery = useQuery({
    queryKey: ["learning-policy", employeeId],
    queryFn: () => getEmployeeLearningPolicy(employeeId),
  });
  const sessionsQuery = useQuery({
    queryKey: ["learning-sessions", employeeId],
    queryFn: () => getEmployeeLearningSessions(employeeId),
  });
  const queryClient = useQueryClient();
  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["learning-sessions", employeeId] });
  };
  const startMutation = useMutation({
    mutationFn: (payload: { topic: string; learning_mode?: string; reason?: string }) =>
      startLearningSession(employeeId, payload),
    onSuccess: invalidate,
  });
  const cancelMutation = useMutation({
    mutationFn: (sessionId: number) => cancelLearningSession(sessionId),
    onSuccess: invalidate,
  });
  return { policyQuery, sessionsQuery, startMutation, cancelMutation };
}
