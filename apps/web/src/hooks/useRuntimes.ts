import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  checkRuntimeImageUpdates,
  createEmployeeRuntime,
  deleteEmployeeRuntime,
  getEmployeeBrain,
  getEmployeeRuntime,
  getRuntimeLogs,
  listRuntimeImages,
  listRuntimeInstances,
  listRuntimeTypes,
  restartRuntime,
  startRuntime,
  stopRuntime,
  updateEmployeeBrain,
  updateEmployeeRuntimeProvider,
  updateRuntimeImage,
} from "../api/runtimes";
import type {
  CreateEmployeeRuntimeInput,
  EmployeeBrain,
  UpdateRuntimeProviderInput,
} from "../types";

// ---------- Queries ----------

export function useRuntimeTypes() {
  return useQuery({ queryKey: ["runtime-types"], queryFn: listRuntimeTypes });
}

export function useRuntimeInstances() {
  return useQuery({ queryKey: ["runtimes"], queryFn: listRuntimeInstances });
}

/** The employee's runtime instance; `data === null` means none exists yet. */
export function useEmployeeRuntime(employeeId: number) {
  return useQuery({
    queryKey: ["employees", employeeId, "runtime"],
    queryFn: () => getEmployeeRuntime(employeeId),
  });
}

/** Runtime logs, fetched on demand (`enabled`) — used by the logs viewer dialog. */
export function useRuntimeLogs(id: number, tail = 200, enabled = true, refetchInterval?: number) {
  return useQuery({
    queryKey: ["runtimes", id, "logs", tail],
    queryFn: () => getRuntimeLogs(id, tail),
    enabled,
    refetchInterval,
  });
}

export function useBrain(employeeId: number) {
  return useQuery({
    queryKey: ["employees", employeeId, "brain"],
    queryFn: () => getEmployeeBrain(employeeId),
  });
}

export function useRuntimeImages() {
  return useQuery({ queryKey: ["runtime-images"], queryFn: listRuntimeImages });
}

// ---------- Mutations ----------

function useInvalidateRuntime(employeeId: number) {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: ["runtimes"] });
    void queryClient.invalidateQueries({ queryKey: ["employees", employeeId, "runtime"] });
  };
}

export function useCreateEmployeeRuntime(employeeId: number) {
  const invalidate = useInvalidateRuntime(employeeId);
  return useMutation({
    mutationFn: (body: CreateEmployeeRuntimeInput) => createEmployeeRuntime(employeeId, body),
    onSuccess: invalidate,
  });
}

export function useUpdateRuntimeProvider(employeeId: number) {
  const invalidate = useInvalidateRuntime(employeeId);
  return useMutation({
    mutationFn: (body: UpdateRuntimeProviderInput) =>
      updateEmployeeRuntimeProvider(employeeId, body),
    onSuccess: invalidate,
  });
}

export function useDeleteEmployeeRuntime(employeeId: number) {
  const invalidate = useInvalidateRuntime(employeeId);
  return useMutation({
    mutationFn: () => deleteEmployeeRuntime(employeeId),
    onSuccess: invalidate,
  });
}

export function useRuntimeAction(employeeId: number) {
  const invalidate = useInvalidateRuntime(employeeId);
  const action = { start: startRuntime, stop: stopRuntime, restart: restartRuntime };
  return useMutation({
    mutationFn: ({ id, op }: { id: number; op: keyof typeof action }) => action[op](id),
    onSuccess: invalidate,
  });
}

export function useUpdateBrain(employeeId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Partial<Omit<EmployeeBrain, "employee_id">>) =>
      updateEmployeeBrain(employeeId, body),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["employees", employeeId, "brain"] }),
  });
}

export function useCheckRuntimeImageUpdates() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => checkRuntimeImageUpdates(),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["runtime-images"] }),
  });
}

export function useUpdateRuntimeImage() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (runtimeType: string) => updateRuntimeImage(runtimeType),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["runtime-images"] });
      void queryClient.invalidateQueries({ queryKey: ["runtimes"] });
    },
  });
}
