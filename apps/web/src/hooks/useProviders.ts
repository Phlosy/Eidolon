import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createProvider,
  deleteProvider,
  listProviderModels,
  listProviders,
  testProvider,
  updateProvider,
} from "../api/providers";
import type { CreateProviderInput, UpdateProviderInput } from "../types";

export function useProviders(employeeId?: number) {
  return useQuery({
    queryKey: ["providers", { employeeId: employeeId ?? null }],
    queryFn: () => listProviders(employeeId),
  });
}

export function useCreateProvider() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateProviderInput) => createProvider(body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["providers"] }),
  });
}

export function useUpdateProvider() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: UpdateProviderInput }) =>
      updateProvider(id, body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["providers"] }),
  });
}

export function useDeleteProvider() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => deleteProvider(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["providers"] }),
  });
}

/** POST /providers/{id}/test — result is transient, nothing to invalidate. */
export function useTestProvider() {
  return useMutation({ mutationFn: (id: number) => testProvider(id) });
}

/** Lazy model discovery — only runs when `enabled` (ProviderModelSelector "Discover" click). */
export function useProviderModels(id: number | null, enabled: boolean) {
  return useQuery({
    queryKey: ["providers", id, "models"],
    queryFn: () => listProviderModels(id!),
    enabled: enabled && id != null,
    staleTime: 60_000,
  });
}
