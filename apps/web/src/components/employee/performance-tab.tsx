import { useTranslation } from "react-i18next";
import { useEmployeePerformance } from "../../hooks/useEmployees";
import { EmptyState, ErrorState } from "../common/states";
import { Skeleton } from "../common/skeleton";
import { formatPercent } from "../../utils/format";

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-md border border-border px-3 py-2">
      <p className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className="mt-1 text-lg font-semibold">{value}</p>
    </div>
  );
}

/** Performance tab: aggregate delivery stats for the employee. */
export function PerformanceTab({ employeeId }: { employeeId: number }) {
  const { t } = useTranslation();
  const perfQuery = useEmployeePerformance(employeeId);

  if (perfQuery.isLoading) {
    return <Skeleton className="h-24 w-full" />;
  }
  if (perfQuery.isError) {
    return <ErrorState error={perfQuery.error} onRetry={() => perfQuery.refetch()} />;
  }
  const perf = perfQuery.data;
  if (!perf) {
    return <EmptyState title={t("employee:performance.emptyTitle")} />;
  }
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
      <Stat label={t("employee:performance.attempts")} value={perf.attempts} />
      <Stat label={t("employee:performance.successes")} value={perf.success_count} />
      <Stat
        label={t("employee:performance.successRate")}
        value={formatPercent(perf.success_rate)}
      />
      <Stat label={t("employee:performance.artifacts")} value={perf.artifacts_count} />
      <Stat label={t("employee:performance.learningRecords")} value={perf.learning_records_count} />
    </div>
  );
}
