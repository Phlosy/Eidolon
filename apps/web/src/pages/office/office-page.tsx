import { useTranslation } from "react-i18next";
import { useEmployees } from "../../hooks/useEmployees";
import { useCompany } from "../../hooks/useSystem";
import { useRuntimeImages, useRuntimeInstances } from "../../hooks/useRuntimes";
import { OfficeEmployeeCard } from "../../components/employee/office-employee-card";
import { EmptyState, ErrorState, PageHeader } from "../../components/common/states";
import { Skeleton } from "../../components/common/skeleton";
import type { Employee } from "../../types";

export function OfficePage() {
  const { t } = useTranslation();
  const companyQuery = useCompany();
  const employeesQuery = useEmployees();
  const runtimesQuery = useRuntimeInstances();
  const imagesQuery = useRuntimeImages();

  if (employeesQuery.isError || companyQuery.isError) {
    return (
      <ErrorState
        error={employeesQuery.error ?? companyQuery.error}
        onRetry={() => {
          void employeesQuery.refetch();
          void companyQuery.refetch();
        }}
      />
    );
  }

  const isLoading = employeesQuery.isLoading || companyQuery.isLoading;
  const employees = employeesQuery.data ?? [];
  const departments = companyQuery.data?.departments ?? [];
  const runtimeByEmployee = new Map((runtimesQuery.data ?? []).map((r) => [r.employee_id, r]));
  const imageByType = new Map((imagesQuery.data ?? []).map((img) => [img.runtime_type, img]));

  const byDepartment = new Map<number, Employee[]>();
  for (const employee of employees) {
    const list = byDepartment.get(employee.department_id) ?? [];
    list.push(employee);
    byDepartment.set(employee.department_id, list);
  }
  const unassigned = byDepartment.get(0) ?? [];
  const sortedDepartments = [...departments].sort((a, b) => a.id - b.id);

  return (
    <div>
      <PageHeader title={t("office:title")} description={t("office:description")} />
      {isLoading ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-32 w-full" />
          ))}
        </div>
      ) : employees.length === 0 ? (
        <EmptyState title={t("office:emptyTitle")} hint={t("office:emptyHint")} />
      ) : (
        <div className="space-y-8">
          {sortedDepartments.map((department) => {
            const members = byDepartment.get(department.id) ?? [];
            if (members.length === 0) return null;
            return (
              <section key={department.id}>
                <div className="mb-3 flex items-baseline gap-2">
                  <h2 className="text-sm font-semibold tracking-tight">{department.name}</h2>
                  <span className="text-xs text-muted-foreground">
                    {t("office:memberCount", { count: members.length })}
                  </span>
                </div>
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                  {members.map((employee) => (
                    <OfficeEmployeeCard
                      key={employee.id}
                      employee={employee}
                      runtime={runtimeByEmployee.get(employee.id) ?? null}
                      runtimeImage={imageByType.get(employee.runtime_type) ?? null}
                    />
                  ))}
                </div>
              </section>
            );
          })}
          {unassigned.length > 0 ? (
            <section>
              <h2 className="mb-3 text-sm font-semibold tracking-tight">
                {t("office:unassigned")}
              </h2>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                {unassigned.map((employee) => (
                  <OfficeEmployeeCard
                    key={employee.id}
                    employee={employee}
                    runtime={runtimeByEmployee.get(employee.id) ?? null}
                    runtimeImage={imageByType.get(employee.runtime_type) ?? null}
                  />
                ))}
              </div>
            </section>
          ) : null}
        </div>
      )}
    </div>
  );
}
