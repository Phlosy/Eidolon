import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  acceptWorkOrder,
  getWorkOrder,
  getWorkOrders,
  submitWorkOrder,
  type WorkOrderFilters,
  type WorkOrderSubmissionInput,
} from "../api/workOrders";

export function useWorkOrders(filters: WorkOrderFilters = {}) {
  return useQuery({
    queryKey: ["work-orders", filters],
    queryFn: () => getWorkOrders(filters),
  });
}

export function useWorkOrder(orderId: number | null) {
  return useQuery({
    queryKey: ["work-orders", "detail", orderId],
    queryFn: () => getWorkOrder(orderId!),
    enabled: orderId != null,
  });
}

export function useAcceptWorkOrder() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (orderId: number) => acceptWorkOrder(orderId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["work-orders"] });
      queryClient.invalidateQueries({ queryKey: ["economy"] });
    },
  });
}

export function useSubmitWorkOrder() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ orderId, input }: { orderId: number; input: WorkOrderSubmissionInput }) =>
      submitWorkOrder(orderId, input),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["work-orders"] });
      queryClient.invalidateQueries({ queryKey: ["economy"] });
    },
  });
}
