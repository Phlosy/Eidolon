import { useTranslation } from "react-i18next";
import { RuntimeBadge } from "../runtime/runtime-badge";
import { StatusDot } from "../common/status-dot";
import { LifecycleStatusBadge } from "../lifecycle/lifecycle-status-badge";
import { EMPLOYEE_STATUS_META } from "../../utils/status";
import { enumLabel } from "../../utils/labels";
import { eid, initials } from "../../utils/format";
import type { Employee } from "../../types";

function Field({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</dt>
      <dd className={`mt-0.5 truncate text-sm ${mono ? "font-mono text-xs" : ""}`}>{value}</dd>
    </div>
  );
}

export function ProfileSection({ employee }: { employee: Employee }) {
  const { t } = useTranslation();
  const meta = EMPLOYEE_STATUS_META[employee.status];
  return (
    <div className="space-y-5">
      <div className="flex items-center gap-4">
        <div className="relative">
          <div className="flex h-14 w-14 items-center justify-center rounded-full bg-muted text-lg font-semibold">
            {initials(employee.name)}
          </div>
          <StatusDot
            status={employee.status}
            className="absolute -right-0.5 -bottom-0.5 h-3 w-3 ring-2 ring-card"
          />
        </div>
        <div>
          <p className="text-base font-semibold">{employee.name}</p>
          <p className="text-sm text-muted-foreground">
            {employee.title ?? enumLabel(t, "employee:role", employee.role)} ·{" "}
            <span className={meta.textClass}>
              {enumLabel(t, "employee:status", employee.status)}
            </span>
          </p>
        </div>
      </div>
      <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <Field label={t("employee:profile.id")} value={eid(employee.id)} mono />
        <Field
          label={t("employee:profile.role")}
          value={enumLabel(t, "employee:role", employee.role)}
        />
        <Field label={t("employee:profile.slug")} value={employee.slug} mono />
        <div className="min-w-0">
          <dt className="text-[11px] uppercase tracking-wide text-muted-foreground">
            {t("employee:profile.lifecycleStatus")}
          </dt>
          <dd className="mt-1">
            <LifecycleStatusBadge status={employee.lifecycle_status} />
          </dd>
        </div>
        <div className="min-w-0">
          <dt className="text-[11px] uppercase tracking-wide text-muted-foreground">
            {t("employee:profile.runtime")}
          </dt>
          <dd className="mt-1">
            <RuntimeBadge type={employee.runtime_type} />
          </dd>
        </div>
        <Field label={t("employee:profile.workspacePath")} value={employee.workspace_path} mono />
        <Field
          label={t("employee:profile.memoryNamespace")}
          value={employee.memory_namespace}
          mono
        />
        <Field
          label={t("employee:profile.currentTask")}
          value={employee.current_task_id != null ? eid(employee.current_task_id) : "—"}
          mono
        />
      </dl>
    </div>
  );
}
