import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createProvider,
  deleteProvider,
  listProviderModels,
  listProviders,
  testProvider,
  updateProvider,
} from "../api/providers";
import { createEmployeeProvider, listEmployeeProviders } from "../api/employees";
import type { CreateEmployeeProviderInput, CreateProviderInput, UpdateProviderInput } from "../types";

export function useProviders(employeeId?: number) {
  return useQuery({
    queryKey: ["providers", { employeeId: employeeId ?? null }],
    queryFn: () => listProviders(employeeId),
  });
}

/**
 * v0.3 main entry point: the employee's own accounts plus company-shared ones
 * (GET /employees/{id}/providers).
 */
export function useEmployeeProviders(employeeId: number) {
  return useQuery({
    queryKey: ["employees", employeeId, "providers"],
    queryFn: () => listEmployeeProviders(employeeId),
  });
}

export function useCreateEmployeeProvider(employeeId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateEmployeeProviderInput) => createEmployeeProvider(employeeId, body),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["employees", employeeId, "providers"] }),
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
