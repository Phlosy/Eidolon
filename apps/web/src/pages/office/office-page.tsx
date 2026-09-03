import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Building2, Radio, Users } from "lucide-react";
import { useEmployees } from "../../hooks/useEmployees";
import { useCompany } from "../../hooks/useSystem";
import { useRuntimeInstances } from "../../hooks/useRuntimes";
import { EmptyState, ErrorState, PageHeader } from "../../components/common/states";
import { Skeleton } from "../../components/common/skeleton";
import { DepartmentZone } from "../../components/office/department-zone";
import { EmployeeQuickPanel } from "../../components/office/employee-quick-panel";
import { ActivityTicker } from "./activity-ticker";
import type { Employee } from "../../types";

export function OfficePage() {
  const { t } = useTranslation();
  const companyQuery = useCompany();
  const employeesQuery = useEmployees();
  const runtimes = useRuntimeInstances().data ?? [];
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const employees = useMemo(() => employeesQuery.data ?? [], [employeesQuery.data]);
  const departments = companyQuery.data?.departments ?? [];
  useEffect(() => {
    if (selectedId != null && !employees.some((employee) => employee.id === selectedId))
      setSelectedId(null);
  }, [employees, selectedId]);
  if (employeesQuery.isError || companyQuery.isError)
    return (
      <ErrorState
        error={employeesQuery.error ?? companyQuery.error}
        onRetry={() => {
          void employeesQuery.refetch();
          void companyQuery.refetch();
        }}
      />
    );
  if (employeesQuery.isLoading || companyQuery.isLoading)
    return (
      <div className="space-y-4">
        <Skeleton className="h-24 rounded-[var(--radius-panel)]" />
        <div className="grid gap-4 lg:grid-cols-2">
          <Skeleton className="h-72 rounded-[var(--radius-panel)]" />
          <Skeleton className="h-72 rounded-[var(--radius-panel)]" />
        </div>
      </div>
    );
  if (employees.length === 0)
    return <EmptyState title={t("office:emptyTitle")} hint={t("office:emptyHint")} />;
  const selected = employees.find((employee) => employee.id === selectedId);
  const selectedDepartment = departments.find(
    (department) => department.id === selected?.department_id,
  );
  const selectedRuntime = runtimes.find((runtime) => runtime.employee_id === selectedId);
  const byDepartment = new Map<number, Employee[]>();
  for (const employee of employees)
    byDepartment.set(employee.department_id, [
      ...(byDepartment.get(employee.department_id) ?? []),
      employee,
    ]);
  const active = employees.filter((employee) => employee.status !== "offline").length;
  return (
    <div className="space-y-5 panel-enter">
      <PageHeader
        icon={Building2}
        title={t("office:title")}
        description={t("office:description")}
      />
      <section className="command-panel relative overflow-hidden px-5 py-4">
        <div className="relative flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <span className="flex h-10 w-10 items-center justify-center rounded-xl border border-success/20 bg-success/8 text-success">
              <Radio className="h-4 w-4 status-pulse" />
            </span>
            <div>
              <p className="type-kicker text-success">{t("office:floorLive")}</p>
              <p className="mt-1 text-sm font-medium">{companyQuery.data?.name}</p>
            </div>
          </div>
          <div className="flex items-center gap-5">
            <div className="text-right">
              <p className="type-telemetry text-lg font-semibold">
                {active}/{employees.length}
              </p>
              <p className="text-[9px] text-muted-foreground">{t("office:activeNow")}</p>
            </div>
            <Users className="h-4 w-4 text-primary" />
          </div>
        </div>
      </section>
      <ActivityTicker />
      <div
        className={
          selected
            ? "grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_340px]"
            : "grid items-start gap-5"
        }
      >
        <div className="grid gap-4 lg:grid-cols-2">
          {departments.map((department, index) => {
            const members = byDepartment.get(department.id) ?? [];
            if (!members.length) return null;
            return (
              <DepartmentZone
                key={department.id}
                department={department}
                employees={members}
                selectedId={selectedId}
                onSelect={(employee) => setSelectedId(employee.id)}
                featured={index === 0 && members.length > 2}
              />
            );
          })}
        </div>
        {selected ? (
          <EmployeeQuickPanel
            employee={selected}
            department={selectedDepartment}
            runtime={selectedRuntime}
            onClose={() => setSelectedId(null)}
          />
        ) : null}
      </div>
    </div>
  );
}
