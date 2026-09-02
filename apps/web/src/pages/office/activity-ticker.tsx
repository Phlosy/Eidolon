import { useTranslation } from "react-i18next";
import { Radio } from "lucide-react";
import { useEventStreamStore } from "../../stores/events";
import { eventLabel, payloadSummary } from "../../features/activity-feed/event-utils";
import { formatRelativeTime } from "../../utils/format";

const TICKER_COUNT = 6;

/**
 * Thin company activity ticker: the latest live WS events slide in at the
 * top of the office floor. Hidden when nothing has happened yet.
 */
export function ActivityTicker() {
  const { t } = useTranslation();
  const events = useEventStreamStore((s) => s.events);

  if (events.length === 0) return null;
  const latest = events.slice(0, TICKER_COUNT);

  return (
    <div className="mb-5 flex items-center gap-3 overflow-hidden rounded-lg border border-border bg-card px-3 py-2 shadow-card">
      <span className="flex shrink-0 items-center gap-1.5 text-[11px] font-semibold text-status-working">
        <Radio className="h-3.5 w-3.5 status-pulse" />
        {t("office:tickerLabel")}
      </span>
      <div className="flex min-w-0 flex-1 items-center gap-5 overflow-hidden [mask-image:linear-gradient(to_right,black_80%,transparent)]">
        {latest.map((event, i) => {
          const summary = payloadSummary(event.data ?? {});
          return (
            <span
              key={`${event.ts}-${i}`}
              className="ticker-in flex shrink-0 items-center gap-1.5 text-xs text-muted-foreground"
            >
              <span className="font-medium text-foreground">{eventLabel(t, event.type)}</span>
              {summary ? <span className="max-w-40 truncate">{summary}</span> : null}
              <span className="font-mono text-[10px] tabular-nums">
                {formatRelativeTime(event.ts)}
              </span>
            </span>
          );
        })}
      </div>
    </div>
  );
}
