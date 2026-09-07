import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  activateDevelopmentPlan,
  createDevelopmentPlan,
  createLearningPriority,
  getCareerOverview,
  getCareerReadiness,
  promoteEmployee,
} from "../api/career";

export function useCareerOverview(employeeId: number) {
  return useQuery({
    queryKey: ["career", employeeId],
    queryFn: () => getCareerOverview(employeeId),
  });
}

export function useCareerReadiness(employeeId: number, positionDefinitionId: number | null) {
  return useQuery({
    queryKey: ["career-readiness", employeeId, positionDefinitionId],
    queryFn: () => getCareerReadiness(employeeId, positionDefinitionId as number),
    enabled: positionDefinitionId !== null,
  });
}

export function useCareerMutations(employeeId: number) {
  const queryClient = useQueryClient();
  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["career", employeeId] });
    void queryClient.invalidateQueries({ queryKey: ["career-readiness", employeeId] });
    void queryClient.invalidateQueries({ queryKey: ["talent-roster"] });
    void queryClient.invalidateQueries({ queryKey: ["position-candidates"] });
  };
  return {
    createPlan: useMutation({
      mutationFn: (targetPositionDefinitionId: number) =>
        createDevelopmentPlan(employeeId, targetPositionDefinitionId),
      onSuccess: invalidate,
    }),
    activatePlan: useMutation({
      mutationFn: (planId: number) => activateDevelopmentPlan(planId),
      onSuccess: invalidate,
    }),
    addLearningPriority: useMutation({
      mutationFn: (itemId: number) => createLearningPriority(itemId),
      onSuccess: invalidate,
    }),
    promote: useMutation({
      mutationFn: (payload: { target_definition_id: number; slot_id: number; reason: string }) =>
        promoteEmployee(employeeId, payload),
      onSuccess: invalidate,
    }),
  };
}
