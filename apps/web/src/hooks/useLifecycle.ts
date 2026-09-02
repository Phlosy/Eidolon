import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  getEmployeeAccounts,
  getEmployeeAssets,
  getEmployeeEmployment,
  getEmployeeEntitlements,
  getEmployeeTimeline,
  getProvisioningJob,
  listAccessPackages,
  listPositions,
  listProvisioningJobs,
  offboardEmployee,
  onboardEmployee,
  previewProvisioning,
  reconcileEmployee,
  resumeEmployee,
  retryProvisioningJob,
  suspendEmployee,
  transferEmployee,
} from "../api/lifecycle";
import type {
  OffboardEmployeeInput,
  OnboardEmployeeInput,
  ProvisioningJob,
  ProvisioningJobStatus,
  ProvisioningPreviewInput,
  TransferEmployeeInput,
} from "../types";

// ---------- Queries ----------

export function usePositions(departmentId: number | null | undefined) {
  return useQuery({
    queryKey: ["positions", departmentId ?? "all"],
    queryFn: () => listPositions(departmentId ?? undefined),
  });
}

export function useAccessPackages() {
  return useQuery({ queryKey: ["access-packages"], queryFn: listAccessPackages });
}

export function useEmployeeAccounts(employeeId: number) {
  return useQuery({
    queryKey: ["employees", employeeId, "accounts"],
    queryFn: () => getEmployeeAccounts(employeeId),
  });
}

export function useEmployeeEntitlements(employeeId: number) {
  return useQuery({
    queryKey: ["employees", employeeId, "entitlements"],
    queryFn: () => getEmployeeEntitlements(employeeId),
  });
}

export function useEmployeeEmployment(employeeId: number, enabled = true) {
  return useQuery({
    queryKey: ["employees", employeeId, "employment"],
    queryFn: () => getEmployeeEmployment(employeeId),
    enabled,
  });
}

export function useEmployeeAssets(employeeId: number) {
  return useQuery({
    queryKey: ["employees", employeeId, "assets"],
    queryFn: () => getEmployeeAssets(employeeId),
  });
}

export function useEmployeeTimeline(employeeId: number) {
  return useQuery({
    queryKey: ["employees", employeeId, "timeline"],
    queryFn: () => getEmployeeTimeline(employeeId),
  });
}

/** Jobs for one employee, newest first (the backend returns them in created order). */
export function useProvisioningJobs(employeeId: number) {
  return useQuery({
    queryKey: ["provisioning-jobs", { employeeId }],
    queryFn: () => listProvisioningJobs(employeeId),
  });
}

export function isJobRunning(status: ProvisioningJobStatus): boolean {
  return status === "pending" || status === "running";
}

/** Single job with steps; polls while the job is still running. */
export function useProvisioningJob(id: number | null | undefined) {
  return useQuery({
    queryKey: ["provisioning-jobs", id],
    queryFn: () => getProvisioningJob(id!),
    enabled: id != null,
    refetchInterval: (query) =>
      query.state.data && isJobRunning(query.state.data.status) ? 2000 : false,
  });
}

export function useProvisioningPreview(input: ProvisioningPreviewInput | null) {
  return useQuery({
    queryKey: ["provisioning-preview", input],
    queryFn: () => previewProvisioning(input!),
    enabled: input != null,
    // A preview is a pure function of its inputs — one fetch per combination.
    staleTime: Infinity,
  });
}

// ---------- Mutations ----------

function useInvalidateEmployee(employeeId?: number) {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: ["employees"] });
    void queryClient.invalidateQueries({ queryKey: ["provisioning-jobs"] });
    if (employeeId != null) {
      void queryClient.invalidateQueries({ queryKey: ["employees", employeeId] });
    }
  };
}

export function useOnboardEmployee() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: OnboardEmployeeInput) => onboardEmployee(body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["employees"] });
    },
  });
}

export function useTransferEmployee(employeeId: number) {
  const invalidate = useInvalidateEmployee(employeeId);
  return useMutation({
    mutationFn: (body: TransferEmployeeInput) => transferEmployee(employeeId, body),
    onSuccess: invalidate,
  });
}

export function useSuspendEmployee(employeeId: number) {
  const invalidate = useInvalidateEmployee(employeeId);
  return useMutation({
    mutationFn: (reason?: string) => suspendEmployee(employeeId, reason),
    onSuccess: invalidate,
  });
}

export function useResumeEmployee(employeeId: number) {
  const invalidate = useInvalidateEmployee(employeeId);
  return useMutation({
    mutationFn: () => resumeEmployee(employeeId),
    onSuccess: invalidate,
  });
}

export function useOffboardEmployee(employeeId: number) {
  const invalidate = useInvalidateEmployee(employeeId);
  return useMutation({
    mutationFn: (body: OffboardEmployeeInput) => offboardEmployee(employeeId, body),
    onSuccess: invalidate,
  });
}

export function useReconcileEmployee(employeeId: number) {
  return useMutation({
    mutationFn: () => reconcileEmployee(employeeId),
  });
}

export function useRetryProvisioningJob() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (jobId: number) => retryProvisioningJob(jobId),
    onSuccess: (job: ProvisioningJob) => {
      void queryClient.invalidateQueries({ queryKey: ["provisioning-jobs"] });
      void queryClient.invalidateQueries({ queryKey: ["employees", job.employee_id] });
    },
  });
}
