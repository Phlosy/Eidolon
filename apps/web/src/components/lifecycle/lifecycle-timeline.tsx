import { useTranslation } from "react-i18next";
import { useEmployeeTimeline } from "../../hooks/useLifecycle";
import { EmptyState, ErrorState } from "../common/states";
import { ScrollArea, Skeleton } from "../common/skeleton";
import { EventItem } from "../../features/activity-feed/event-item";

/** Overview-tab section: lifecycle events only (GET /employees/{id}/timeline). */
export function LifecycleTimeline({ employeeId }: { employeeId: number }) {
  const { t } = useTranslation();
  const timelineQuery = useEmployeeTimeline(employeeId);

  return (
    <section>
      <h3 className="mb-2 text-sm font-semibold">{t("lifecycle:timeline.title")}</h3>
      {timelineQuery.isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-8 w-full" />
          ))}
        </div>
      ) : timelineQuery.isError ? (
        <ErrorState error={timelineQuery.error} onRetry={() => timelineQuery.refetch()} />
      ) : (timelineQuery.data ?? []).length === 0 ? (
        <EmptyState title={t("lifecycle:timeline.emptyTitle")} />
      ) : (
        <ScrollArea className="max-h-72 pr-3">
          <ul className="divide-y divide-border/60">
            {timelineQuery.data!.map((event) => (
              <EventItem key={event.id} event={event} />
            ))}
          </ul>
        </ScrollArea>
      )}
    </section>
  );
}
