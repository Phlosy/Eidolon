import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ArrowLeftRight, CirclePause, CirclePlay, TriangleAlert, UserMinus } from "lucide-react";
import {
  useAccessPackages,
  useEmployeeEmployment,
  useEmployeeEntitlements,
  useOffboardEmployee,
  usePositions,
  useResumeEmployee,
  useSuspendEmployee,
  useTransferEmployee,
} from "../../hooks/useLifecycle";
import { useCompany } from "../../hooks/useSystem";
import { useEmployees } from "../../hooks/useEmployees";
import { Badge } from "../common/badge";
import { Button } from "../common/button";
import { Dialog } from "../common/dialog";
import { Input } from "../common/input";
import { Skeleton } from "../common/skeleton";
import { cn } from "../../utils/cn";
import { availableActions, type LifecycleAction as Action } from "./action-availability";
import type { Employee, OffboardEmployeeInput } from "../../types";

const selectClass =
  "w-full rounded-md border border-border bg-transparent px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50";

/** Lifecycle action buttons + dialogs for the employee detail header. */
export function LifecycleActions({
  employee,
  onJobStarted,
}: {
  employee: Employee;
  onJobStarted: (jobId: number) => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState<Action | null>(null);
  const actions = availableActions(employee.lifecycle_status);

  if (actions.length === 0) {
    return employee.lifecycle_status === "offboarded" ? null : (
      <p className="text-xs text-muted-foreground">{t("lifecycle:actions.jobInProgress")}</p>
    );
  }

  const close = () => setOpen(null);
  const buttons: Record<
    Action,
    { label: string; icon: typeof ArrowLeftRight; destructive?: boolean }
  > = {
    transfer: { label: t("lifecycle:actions.transfer"), icon: ArrowLeftRight },
    suspend: { label: t("lifecycle:actions.suspend"), icon: CirclePause },
    resume: { label: t("lifecycle:actions.resume"), icon: CirclePlay },
    offboard: { label: t("lifecycle:actions.offboard"), icon: UserMinus, destructive: true },
  };

  return (
    <div className="flex flex-wrap items-center gap-2">
      {actions.map((action) => {
        const meta = buttons[action];
        const Icon = meta.icon;
        return (
          <Button
            key={action}
            variant={meta.destructive ? "destructive" : "outline"}
            size="sm"
            data-testid={`action-${action}`}
            onClick={() => setOpen(action)}
          >
            <Icon className="h-3.5 w-3.5" />
            {meta.label}
          </Button>
        );
      })}

      <TransferDialog
        employee={employee}
        open={open === "transfer"}
        onOpenChange={(next) => (next ? setOpen("transfer") : close())}
        onJobStarted={(jobId) => {
          close();
          onJobStarted(jobId);
        }}
      />
      <SuspendDialog
        employee={employee}
        open={open === "suspend"}
        onOpenChange={(next) => (next ? setOpen("suspend") : close())}
        onJobStarted={(jobId) => {
          close();
          onJobStarted(jobId);
        }}
      />
      <ResumeDialog
        employee={employee}
        open={open === "resume"}
        onOpenChange={(next) => (next ? setOpen("resume") : close())}
        onJobStarted={(jobId) => {
          close();
          onJobStarted(jobId);
        }}
      />
      <OffboardDialog
        employee={employee}
        open={open === "offboard"}
        onOpenChange={(next) => (next ? setOpen("offboard") : close())}
        onJobStarted={(jobId) => {
          close();
          onJobStarted(jobId);
        }}
      />
    </div>
  );
}

interface ActionDialogProps {
  employee: Employee;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onJobStarted: (jobId: number) => void;
}

function TransferDialog({ employee, open, onOpenChange, onJobStarted }: ActionDialogProps) {
  const { t } = useTranslation();
  const employmentQuery = useEmployeeEmployment(employee.id, open);
  const entitlementsQuery = useEmployeeEntitlements(employee.id);
  const companyQuery = useCompany();
  const employeesQuery = useEmployees();
  const packagesQuery = useAccessPackages();

  const current = employmentQuery.data?.current ?? null;
  const [departmentId, setDepartmentId] = useState<number | null>(null);
  const [positionId, setPositionId] = useState<number | null>(null);
  const [managerId, setManagerId] = useState<number | null>(null);
  const [packageIds, setPackageIds] = useState<number[] | null>(null);
  const [reason, setReason] = useState("");
  const positionsQuery = usePositions(departmentId ?? current?.department_id ?? null);
  const transfer = useTransferEmployee(employee.id);

  // Pre-fill from the current employment / packages once the dialog data loads.
  const effectiveDepartmentId = departmentId ?? current?.department_id ?? null;
  const effectivePositionId = positionId ?? current?.position_id ?? null;
  const effectiveManagerId = managerId ?? current?.manager_employee_id ?? null;
  const currentPackageIds = packageIds ?? [
    ...new Set((entitlementsQuery.data ?? []).flatMap((e) => e.sources.map((s) => s.package_id))),
  ];

  const submit = () => {
    if (effectiveDepartmentId == null) return;
    transfer.mutate(
      {
        department_id: effectiveDepartmentId,
        ...(effectivePositionId != null ? { position_id: effectivePositionId } : {}),
        ...(effectiveManagerId != null ? { manager_employee_id: effectiveManagerId } : {}),
        ...(currentPackageIds.length > 0 ? { access_package_ids: currentPackageIds } : {}),
        ...(reason.trim() ? { reason: reason.trim() } : {}),
      },
      { onSuccess: (result) => onJobStarted(result.job.id) },
    );
  };

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={t("lifecycle:transfer.title")}
      description={t("lifecycle:transfer.description")}
      className="max-w-lg"
    >
      {employmentQuery.isLoading ? (
        <Skeleton className="h-32 w-full" />
      ) : (
        <div className="space-y-3">
          <select
            className={selectClass}
            data-testid="transfer-department"
            value={effectiveDepartmentId ?? ""}
            onChange={(e) => {
              setDepartmentId(Number(e.target.value));
              setPositionId(null);
            }}
          >
            {(companyQuery.data?.departments ?? []).map((dept) => (
              <option key={dept.id} value={dept.id}>
                {dept.name}
              </option>
            ))}
          </select>
          <select
            className={selectClass}
            value={positionId ?? current?.position_id ?? ""}
            onChange={(e) => setPositionId(e.target.value === "" ? null : Number(e.target.value))}
          >
            <option value="">{t("lifecycle:wizard.noPosition")}</option>
            {(positionsQuery.data ?? []).map((position) => (
              <option key={position.id} value={position.id}>
                {position.title}
              </option>
            ))}
          </select>
          <select
            className={selectClass}
            value={managerId ?? current?.manager_employee_id ?? ""}
            onChange={(e) => setManagerId(e.target.value === "" ? null : Number(e.target.value))}
          >
            <option value="">{t("lifecycle:wizard.noManager")}</option>
            {(employeesQuery.data ?? [])
              .filter((e) => e.id !== employee.id)
              .map((e) => (
                <option key={e.id} value={e.id}>
                  {e.name}
                </option>
              ))}
          </select>
          <div className="space-y-1.5">
            {(packagesQuery.data ?? []).map((pkg) => {
              const checked = currentPackageIds.includes(pkg.id);
              return (
                <label
                  key={pkg.id}
                  className={cn(
                    "flex cursor-pointer items-center gap-2.5 rounded-md border border-border px-3 py-1.5 text-sm",
                    checked ? "border-foreground/40 bg-muted" : "hover:bg-muted/50",
                  )}
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() =>
                      setPackageIds(
                        checked
                          ? currentPackageIds.filter((id) => id !== pkg.id)
                          : [...currentPackageIds, pkg.id],
                      )
                    }
                  />
                  <span className="font-medium">{pkg.name}</span>
                  {pkg.built_in ? (
                    <Badge variant="muted">{t("lifecycle:packages.builtIn")}</Badge>
                  ) : null}
                </label>
              );
            })}
          </div>
          <Input
            placeholder={t("lifecycle:transfer.reasonPlaceholder")}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
        </div>
      )}
      {transfer.isError ? (
        <p className="mt-3 text-xs text-red-600 dark:text-red-400">{transfer.error.message}</p>
      ) : null}
      <div className="mt-5 flex justify-end gap-2">
        <Button variant="ghost" size="sm" onClick={() => onOpenChange(false)}>
          {t("common:cancel")}
        </Button>
        <Button
          size="sm"
          data-testid="transfer-submit"
          disabled={transfer.isPending || effectiveDepartmentId == null}
          onClick={submit}
        >
          {transfer.isPending ? t("lifecycle:transfer.submitting") : t("lifecycle:transfer.submit")}
        </Button>
      </div>
    </Dialog>
  );
}

function SuspendDialog({ employee, open, onOpenChange, onJobStarted }: ActionDialogProps) {
  const { t } = useTranslation();
  const [reason, setReason] = useState("");
  const suspend = useSuspendEmployee(employee.id);
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={t("lifecycle:suspend.title")}
      description={t("lifecycle:suspend.description")}
    >
      <Input
        placeholder={t("lifecycle:suspend.reasonPlaceholder")}
        value={reason}
        onChange={(e) => setReason(e.target.value)}
      />
      {suspend.isError ? (
        <p className="mt-3 text-xs text-red-600 dark:text-red-400">{suspend.error.message}</p>
      ) : null}
      <div className="mt-5 flex justify-end gap-2">
        <Button variant="ghost" size="sm" onClick={() => onOpenChange(false)}>
          {t("common:cancel")}
        </Button>
        <Button
          size="sm"
          data-testid="suspend-confirm"
          disabled={suspend.isPending}
          onClick={() =>
            suspend.mutate(reason.trim() || undefined, {
              onSuccess: (result) => onJobStarted(result.job.id),
            })
          }
        >
          {t("lifecycle:suspend.confirm")}
        </Button>
      </div>
    </Dialog>
  );
}

function ResumeDialog({ employee, open, onOpenChange, onJobStarted }: ActionDialogProps) {
  const { t } = useTranslation();
  const resume = useResumeEmployee(employee.id);
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={t("lifecycle:resume.title")}
      description={t("lifecycle:resume.description")}
    >
      {resume.isError ? (
        <p className="mt-3 text-xs text-red-600 dark:text-red-400">{resume.error.message}</p>
      ) : null}
      <div className="mt-2 flex justify-end gap-2">
        <Button variant="ghost" size="sm" onClick={() => onOpenChange(false)}>
          {t("common:cancel")}
        </Button>
        <Button
          size="sm"
          data-testid="resume-confirm"
          disabled={resume.isPending}
          onClick={() =>
            resume.mutate(undefined, { onSuccess: (result) => onJobStarted(result.job.id) })
          }
        >
          {t("lifecycle:resume.confirm")}
        </Button>
      </div>
    </Dialog>
  );
}

function OffboardDialog({ employee, open, onOpenChange, onJobStarted }: ActionDialogProps) {
  const { t } = useTranslation();
  const [mode, setMode] = useState<"department" | "company" | "archive" | "employee">("department");
  const [targetEmployeeId, setTargetEmployeeId] = useState<number | null>(null);
  const [reason, setReason] = useState("");
  const employeesQuery = useEmployees();
  const offboard = useOffboardEmployee(employee.id);

  const modes = ["department", "company", "archive", "employee"] as const;
  const needsTarget = mode === "employee";

  const submit = () => {
    const transfer_to: OffboardEmployeeInput["transfer_to"] =
      mode === "employee" ? targetEmployeeId! : mode;
    offboard.mutate(
      { transfer_to, ...(reason.trim() ? { reason: reason.trim() } : {}) },
      { onSuccess: (result) => onJobStarted(result.job.id) },
    );
  };

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={t("lifecycle:offboard.title")}
      description={t("lifecycle:offboard.description")}
      className="border-red-500/40"
    >
      <div className="space-y-3">
        <p className="flex items-start gap-2 rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-600 dark:text-red-400">
          <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          {t("lifecycle:offboard.warning")}
        </p>
        <label className="block">
          <span className="mb-1 block text-xs text-muted-foreground">
            {t("lifecycle:offboard.transferToLabel")}
          </span>
          <select
            className={selectClass}
            data-testid="offboard-transfer-to"
            value={mode}
            onChange={(e) => setMode(e.target.value as typeof mode)}
          >
            {modes.map((m) => (
              <option key={m} value={m}>
                {t(`lifecycle:offboard.transferTo.${m}`)}
              </option>
            ))}
          </select>
        </label>
        {needsTarget ? (
          <select
            className={selectClass}
            data-testid="offboard-target-employee"
            value={targetEmployeeId ?? ""}
            onChange={(e) =>
              setTargetEmployeeId(e.target.value === "" ? null : Number(e.target.value))
            }
          >
            <option value="" disabled>
              —
            </option>
            {(employeesQuery.data ?? [])
              .filter((e) => e.id !== employee.id)
              .map((e) => (
                <option key={e.id} value={e.id}>
                  {e.name}
                </option>
              ))}
          </select>
        ) : null}
        <Input
          placeholder={t("lifecycle:offboard.reasonPlaceholder")}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />
      </div>
      {offboard.isError ? (
        <p className="mt-3 text-xs text-red-600 dark:text-red-400">{offboard.error.message}</p>
      ) : null}
      <div className="mt-5 flex justify-end gap-2">
        <Button variant="ghost" size="sm" onClick={() => onOpenChange(false)}>
          {t("common:cancel")}
        </Button>
        <Button
          variant="destructive"
          size="sm"
          data-testid="offboard-confirm"
          disabled={offboard.isPending || (needsTarget && targetEmployeeId == null)}
          onClick={submit}
        >
          {offboard.isPending
            ? t("lifecycle:offboard.confirming")
            : t("lifecycle:offboard.confirm")}
        </Button>
      </div>
    </Dialog>
  );
}
