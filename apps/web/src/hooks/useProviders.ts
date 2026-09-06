import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createProvider,
  deleteProvider,
  listProviderModels,
  listProviderPresets,
  listProviders,
  probeProviderConfig,
  testProvider,
  updateProvider,
} from "../api/providers";
import {
  addEmployeeBinding,
  createEmployeeProvider,
  deleteEmployeeBinding,
  listEmployeeBindings,
  listEmployeeProviders,
  setPrimaryBinding,
  updateEmployeeBinding,
} from "../api/employees";
import type {
  CreateEmployeeProviderInput,
  CreateProviderInput,
  UpdateProviderInput,
} from "../types";

export function useProviders(employeeId?: number) {
  return useQuery({
    queryKey: ["providers", { employeeId: employeeId ?? null }],
    queryFn: () => listProviders(employeeId),
  });
}

/** 内置厂商预设：静态数据，一次拉取、长期缓存。 */
export function useProviderPresets() {
  return useQuery({
    queryKey: ["providers", "presets"],
    queryFn: listProviderPresets,
    staleTime: Infinity,
  });
}

/** 不保存配置的试连（探测可用模型）：一次性动作，用 mutation。 */
export function useProbeProviderConfig() {
  return useMutation({ mutationFn: probeProviderConfig });
}

// ---- model bindings：多模型绑定与默认切换 ----

export function useEmployeeBindings(employeeId: number) {
  return useQuery({
    queryKey: ["employees", employeeId, "bindings"],
    queryFn: () => listEmployeeBindings(employeeId),
  });
}

function useInvalidateBindings(employeeId: number) {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: ["employees", employeeId, "bindings"] });
    // in_use_by 计数会变
    void queryClient.invalidateQueries({ queryKey: ["employees", employeeId, "providers"] });
  };
}

export function useAddEmployeeBinding(employeeId: number) {
  const invalidate = useInvalidateBindings(employeeId);
  return useMutation({
    mutationFn: (body: {
      provider_id: number;
      model: string;
      alias?: string;
      make_primary?: boolean;
    }) => addEmployeeBinding(employeeId, body),
    onSuccess: invalidate,
  });
}

export function useSetPrimaryBinding(employeeId: number) {
  const invalidate = useInvalidateBindings(employeeId);
  return useMutation({
    mutationFn: (bindingId: number) => setPrimaryBinding(employeeId, bindingId),
    onSuccess: invalidate,
  });
}

export function useUpdateBinding(employeeId: number) {
  const invalidate = useInvalidateBindings(employeeId);
  return useMutation({
    mutationFn: ({ bindingId, alias }: { bindingId: number; alias: string }) =>
      updateEmployeeBinding(employeeId, bindingId, { alias }),
    onSuccess: invalidate,
  });
}

export function useDeleteBinding(employeeId: number) {
  const invalidate = useInvalidateBindings(employeeId);
  return useMutation({
    mutationFn: (bindingId: number) => deleteEmployeeBinding(employeeId, bindingId),
    onSuccess: invalidate,
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
