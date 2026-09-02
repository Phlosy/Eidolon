import { useTranslation } from "react-i18next";
import { useEmployeeEmployment, usePositions } from "../../hooks/useLifecycle";
import { useCompany } from "../../hooks/useSystem";
import { useEmployees } from "../../hooks/useEmployees";
import { EmptyState, ErrorState } from "../common/states";
import { Skeleton } from "../common/skeleton";
import { formatDateTime } from "../../utils/format";
import type { Employment } from "../../types";

/**
 * Employment tab: current employment card + history table. Department /
 * position / manager ids are resolved against the company, positions and
 * employees queries (all cached app-wide).
 */
export function EmploymentTab({ employeeId }: { employeeId: number }) {
  const { t } = useTranslation();
  const employmentQuery = useEmployeeEmployment(employeeId);
  const companyQuery = useCompany();
  const positionsQuery = usePositions(null);
  const employeesQuery = useEmployees();

  if (employmentQuery.isLoading) {
    return <Skeleton className="h-40 w-full" />;
  }
  if (employmentQuery.isError || !employmentQuery.data) {
    return <ErrorState error={employmentQuery.error} onRetry={() => employmentQuery.refetch()} />;
  }

  const departmentName = (id: number) =>
    companyQuery.data?.departments.find((d) => d.id === id)?.name ?? `#${id}`;
  const positionTitle = (id: number | null) =>
    id == null ? "—" : (positionsQuery.data?.find((p) => p.id === id)?.title ?? `#${id}`);
  const managerName = (id: number | null) =>
    id == null ? "—" : (employeesQuery.data?.find((e) => e.id === id)?.name ?? `#${id}`);

  const { current, history } = employmentQuery.data;
  const rows: Employment[] = [
    ...(current ? [current] : []),
    ...history.filter((h) => h.id !== current?.id),
  ];

  if (!current && history.length === 0) {
    return <EmptyState title={t("lifecycle:employment.emptyTitle")} />;
  }

  return (
    <div className="space-y-5">
      {current ? (
        <section>
          <h3 className="mb-2 text-sm font-semibold">{t("lifecycle:employment.currentTitle")}</h3>
          <dl className="grid grid-cols-1 gap-4 rounded-md border border-border p-4 sm:grid-cols-2 lg:grid-cols-3">
            <Field
              label={t("lifecycle:employment.department")}
              value={departmentName(current.department_id)}
            />
            <Field
              label={t("lifecycle:employment.position")}
              value={positionTitle(current.position_id)}
            />
            <Field
              label={t("lifecycle:employment.manager")}
              value={managerName(current.manager_employee_id)}
            />
            <Field
              label={t("lifecycle:employment.joinedAt")}
              value={formatDateTime(current.joined_at)}
            />
            <Field
              label={t("lifecycle:employment.effectiveFrom")}
              value={formatDateTime(current.effective_from)}
            />
            <Field label={t("lifecycle:employment.status")} value={current.employment_status} />
          </dl>
        </section>
      ) : null}

      {rows.length > 0 ? (
        <section>
          <h3 className="mb-2 text-sm font-semibold">{t("lifecycle:employment.historyTitle")}</h3>
          <div className="overflow-x-auto rounded-lg border border-border">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-xs text-muted-foreground">
                  <th className="px-4 py-2.5 font-medium">
                    {t("lifecycle:employment.department")}
                  </th>
                  <th className="px-4 py-2.5 font-medium">{t("lifecycle:employment.position")}</th>
                  <th className="px-4 py-2.5 font-medium">{t("lifecycle:employment.manager")}</th>
                  <th className="px-4 py-2.5 font-medium">
                    {t("lifecycle:employment.effectiveFrom")}
                  </th>
                  <th className="px-4 py-2.5 font-medium">
                    {t("lifecycle:employment.effectiveTo")}
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.id} className="border-b border-border/60 last:border-0">
                    <td className="px-4 py-2.5">{departmentName(row.department_id)}</td>
                    <td className="px-4 py-2.5 text-muted-foreground">
                      {positionTitle(row.position_id)}
                    </td>
                    <td className="px-4 py-2.5 text-muted-foreground">
                      {managerName(row.manager_employee_id)}
                    </td>
                    <td className="px-4 py-2.5 font-mono text-xs text-muted-foreground">
                      {formatDateTime(row.effective_from)}
                    </td>
                    <td className="px-4 py-2.5 font-mono text-xs text-muted-foreground">
                      {row.effective_to
                        ? formatDateTime(row.effective_to)
                        : t("lifecycle:employment.present")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}
    </div>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 truncate text-sm">{value}</dd>
    </div>
  );
}
