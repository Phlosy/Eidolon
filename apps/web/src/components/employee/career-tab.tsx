import { useTranslation } from "react-i18next";
import { useEmployeeActivity } from "../../hooks/useEmployees";
import { EmptyState, ErrorState } from "../common/states";
import { ScrollArea, Skeleton } from "../common/skeleton";
import { EventItem } from "../../features/activity-feed/event-item";

/**
 * Career tab: a simple read-only timeline built from the employee's activity
 * events (milestones like task completions, learnings, runtime changes).
 */
export function CareerTab({ employeeId }: { employeeId: number }) {
  const { t } = useTranslation();
  const activityQuery = useEmployeeActivity(employeeId);

  if (activityQuery.isLoading) {
    return (
      <div className="space-y-3">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-8 w-full" />
        ))}
      </div>
    );
  }
  if (activityQuery.isError) {
    return <ErrorState error={activityQuery.error} onRetry={() => activityQuery.refetch()} />;
  }
  const events = activityQuery.data ?? [];
  if (events.length === 0) {
    return (
      <EmptyState title={t("employee:career.emptyTitle")} hint={t("employee:career.emptyHint")} />
    );
  }
  return (
    <ScrollArea className="max-h-96 pr-3">
      <ol className="relative ml-1.5 border-l border-border pl-4">
        {events.map((event) => (
          <li key={event.id} className="relative">
            <span className="absolute top-3 -left-[21px] h-1.5 w-1.5 rounded-full bg-muted-foreground/60" />
            <EventItem event={event} />
          </li>
        ))}
      </ol>
    </ScrollArea>
  );
}
