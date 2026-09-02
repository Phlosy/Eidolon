import { get, post } from "./client";
import type {
  AccessPackage,
  CompanyEvent,
  EmployeeEntitlement,
  EmploymentInfo,
  OffboardEmployeeInput,
  OnboardEmployeeInput,
  OnboardEmployeeResult,
  Position,
  ProvisioningJob,
  ProvisioningPreviewInput,
  ProvisioningPreviewStep,
  ReconcileDrift,
  ResourceAccount,
  ResourceAsset,
  TransferEmployeeInput,
} from "../types";

// ---------- v0.4: employee lifecycle (docs/design-v0.4-lifecycle.md §10) ----------

export function onboardEmployee(body: OnboardEmployeeInput): Promise<OnboardEmployeeResult> {
  return post<OnboardEmployeeResult>("/employees/onboard", body);
}

export function transferEmployee(
  id: number,
  body: TransferEmployeeInput,
): Promise<{ job: ProvisioningJob }> {
  return post<{ job: ProvisioningJob }>(`/employees/${id}/transfer`, body);
}

export function suspendEmployee(id: number, reason?: string): Promise<{ job: ProvisioningJob }> {
  return post<{ job: ProvisioningJob }>(`/employees/${id}/suspend`, reason ? { reason } : {});
}

export function resumeEmployee(id: number): Promise<{ job: ProvisioningJob }> {
  return post<{ job: ProvisioningJob }>(`/employees/${id}/resume`);
}

export function offboardEmployee(
  id: number,
  body: OffboardEmployeeInput,
): Promise<{ job: ProvisioningJob }> {
  return post<{ job: ProvisioningJob }>(`/employees/${id}/offboard`, body);
}

export function getEmployeeAccounts(id: number): Promise<ResourceAccount[]> {
  return get<ResourceAccount[]>(`/employees/${id}/accounts`);
}

export function getEmployeeEntitlements(id: number): Promise<EmployeeEntitlement[]> {
  return get<EmployeeEntitlement[]>(`/employees/${id}/entitlements`);
}

export function getEmployeeEmployment(id: number): Promise<EmploymentInfo> {
  return get<EmploymentInfo>(`/employees/${id}/employment`);
}

export function getEmployeeAssets(id: number): Promise<ResourceAsset[]> {
  return get<ResourceAsset[]>(`/employees/${id}/assets`);
}

export function getEmployeeTimeline(id: number): Promise<CompanyEvent[]> {
  return get<CompanyEvent[]>(`/employees/${id}/timeline`);
}

export function reconcileEmployee(id: number): Promise<{ drifts: ReconcileDrift[] }> {
  return post<{ drifts: ReconcileDrift[] }>(`/employees/${id}/reconcile`);
}

export function listPositions(departmentId?: number): Promise<Position[]> {
  const query = departmentId != null ? `?department_id=${departmentId}` : "";
  return get<Position[]>(`/positions${query}`);
}

export function listAccessPackages(): Promise<AccessPackage[]> {
  return get<AccessPackage[]>("/access-packages");
}

export function listProvisioningJobs(employeeId?: number): Promise<ProvisioningJob[]> {
  const query = employeeId != null ? `?employee_id=${employeeId}` : "";
  return get<ProvisioningJob[]>(`/provisioning-jobs${query}`);
}

export function getProvisioningJob(id: number): Promise<ProvisioningJob> {
  return get<ProvisioningJob>(`/provisioning-jobs/${id}`);
}

export function retryProvisioningJob(id: number): Promise<ProvisioningJob> {
  return post<ProvisioningJob>(`/provisioning-jobs/${id}/retry`);
}

export function previewProvisioning(
  body: ProvisioningPreviewInput,
): Promise<{ steps: ProvisioningPreviewStep[] }> {
  return post<{ steps: ProvisioningPreviewStep[] }>("/provisioning/preview", body);
}
