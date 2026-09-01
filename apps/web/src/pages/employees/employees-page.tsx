import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useEmployees } from "../../hooks/useEmployees";
import { StatusDot } from "../../components/common/status-dot";
import { RuntimeBadge } from "../../components/runtime/runtime-badge";
import { EmptyState, ErrorState, PageHeader } from "../../components/common/states";
import { Skeleton } from "../../components/common/skeleton";
import { EMPLOYEE_STATUS_META } from "../../utils/status";
import { enumLabel } from "../../utils/labels";
import { eid } from "../../utils/format";

export function EmployeesPage() {
  const { t } = useTranslation();
  const employeesQuery = useEmployees();

  return (
    <div>
      <PageHeader title={t("employee:listTitle")} description={t("employee:listDescription")} />
      {employeesQuery.isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </div>
      ) : employeesQuery.isError ? (
        <ErrorState error={employeesQuery.error} onRetry={() => employeesQuery.refetch()} />
      ) : (employeesQuery.data ?? []).length === 0 ? (
        <EmptyState title={t("employee:emptyTitle")} hint={t("employee:emptyHint")} />
      ) : (
        <div className="overflow-x-auto rounded-lg border border-border bg-card">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-left text-xs text-muted-foreground">
                <th className="px-4 py-2.5 font-medium">{t("employee:table.name")}</th>
                <th className="px-4 py-2.5 font-medium">{t("employee:table.role")}</th>
                <th className="px-4 py-2.5 font-medium">{t("employee:table.status")}</th>
                <th className="px-4 py-2.5 font-medium">{t("employee:table.runtime")}</th>
                <th className="px-4 py-2.5 font-medium">{t("employee:table.currentTask")}</th>
              </tr>
            </thead>
            <tbody>
              {employeesQuery.data!.map((employee) => (
                <tr
                  key={employee.id}
                  className="border-b border-border/60 last:border-0 hover:bg-muted/40"
                >
                  <td className="px-4 py-2.5">
                    <Link to={`/employees/${employee.id}`} className="font-medium hover:underline">
                      {employee.name}
                    </Link>
                    <span className="ml-2 font-mono text-[11px] text-muted-foreground">
                      {eid(employee.id)}
                    </span>
                  </td>
                  <td className="px-4 py-2.5 text-muted-foreground">
                    {employee.title ?? enumLabel(t, "employee:role", employee.role)}
                  </td>
                  <td className="px-4 py-2.5">
                    <span className="flex items-center gap-1.5">
                      <StatusDot status={employee.status} />
                      <span className={EMPLOYEE_STATUS_META[employee.status].textClass}>
                        {enumLabel(t, "employee:status", employee.status)}
                      </span>
                    </span>
                  </td>
                  <td className="px-4 py-2.5">
                    <RuntimeBadge type={employee.runtime_type} />
                  </td>
                  <td className="px-4 py-2.5 font-mono text-xs text-muted-foreground">
                    {employee.current_task_id != null ? eid(employee.current_task_id) : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
