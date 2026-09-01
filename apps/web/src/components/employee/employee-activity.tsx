import { useTranslation } from "react-i18next";
import { useEmployeeActivity } from "../../hooks/useEmployees";
import { EmptyState, ErrorState } from "../common/states";
import { ScrollArea, Skeleton } from "../common/skeleton";
import { EventItem } from "../../features/activity-feed/event-item";

export function EmployeeActivity({ employeeId }: { employeeId: number }) {
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
    return <EmptyState title={t("employee:activity.emptyTitle")} />;
  }
  return (
    <ScrollArea className="max-h-96 pr-3">
      <ul className="divide-y divide-border/60">
        {events.map((event) => (
          <EventItem key={event.id} event={event} />
        ))}
      </ul>
    </ScrollArea>
  );
}
