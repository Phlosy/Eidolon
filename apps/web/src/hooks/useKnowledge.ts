import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  listKnowledgeItems,
  proposeKnowledgePromotion,
  reviewKnowledgeProposal,
  type ListKnowledgeParams,
  type PromotionTargetScope,
} from "../api/knowledge";

/** 公司知识库列表：scope/topic 过滤直接下发给后端。 */
export function useKnowledgeItems(params: ListKnowledgeParams = {}) {
  return useQuery({
    queryKey: [
      "knowledge",
      {
        scope: params.scope ?? null,
        topic: params.topic ?? null,
        employeeId: params.employeeId ?? null,
      },
    ],
    queryFn: () => listKnowledgeItems(params),
  });
}

export function useProposeKnowledgePromotion() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ itemId, targetScope }: { itemId: number; targetScope: PromotionTargetScope }) =>
      proposeKnowledgePromotion(itemId, targetScope),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["knowledge"] }),
  });
}

export function useReviewKnowledgeProposal() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ itemId, approve }: { itemId: number; approve: boolean }) =>
      reviewKnowledgeProposal(itemId, approve),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["knowledge"] }),
  });
}
