import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { useEvents } from "../../hooks/useSystem";
import { useEventStreamStore } from "../../stores/events";
import { ScrollArea, Skeleton } from "../../components/common/skeleton";
import { EmptyState, ErrorState } from "../../components/common/states";
import { EventItem, StreamEventItem } from "./event-item";

/**
 * Recent activity: historical events from GET /events?limit= merged with
 * live WS events from the zustand buffer (deduped by type+ts).
 */
export function ActivityFeed({ limit = 30 }: { limit?: number }) {
  const { t } = useTranslation();
  const eventsQuery = useEvents(limit);
  const liveEvents = useEventStreamStore((s) => s.events);

  const knownTs = useMemo(
    () => new Set((eventsQuery.data ?? []).map((e) => `${e.type}|${e.created_at}`)),
    [eventsQuery.data],
  );
  const freshLive = useMemo(
    () => liveEvents.filter((e) => !knownTs.has(`${e.type}|${e.ts}`)).slice(0, limit),
    [liveEvents, knownTs, limit],
  );

  if (eventsQuery.isLoading) {
    return (
      <div className="space-y-3 p-1">
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} className="h-8 w-full" />
        ))}
      </div>
    );
  }

  if (eventsQuery.isError) {
    return <ErrorState error={eventsQuery.error} onRetry={() => eventsQuery.refetch()} />;
  }

  const historical = eventsQuery.data ?? [];
  if (historical.length === 0 && freshLive.length === 0) {
    return <EmptyState title={t("event:emptyTitle")} hint={t("event:emptyHint")} />;
  }

  return (
    <ScrollArea className="max-h-[28rem] pr-3">
      <ul className="divide-y divide-border/60">
        {freshLive.map((event, i) => (
          <StreamEventItem key={`live-${event.ts}-${i}`} event={event} />
        ))}
        {historical.map((event) => (
          <EventItem key={event.id} event={event} />
        ))}
      </ul>
    </ScrollArea>
  );
}
