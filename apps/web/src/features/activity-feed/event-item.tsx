import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
import { formatRelativeTime } from "../../utils/format";
import type { CompanyEvent, StreamEvent } from "../../types";

function eventLabel(t: TFunction, type: string): string {
  const key = `event:${type}`;
  const translated = t(key);
  // Missing everywhere → i18next returns the key itself; show the raw type.
  return translated === key ? type : translated;
}

function payloadSummary(data: Record<string, unknown>): string | null {
  const candidates = ["title", "name", "employee_name", "project_name", "status"];
  for (const key of candidates) {
    const value = data[key];
    if (typeof value === "string" && value.length > 0) return value;
  }
  return null;
}

export function EventItem({ event }: { event: CompanyEvent }) {
  const { t } = useTranslation();
  const summary = payloadSummary(event.payload ?? {});
  return (
    <li className="flex items-start gap-3 py-2">
      <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-muted-foreground/60" />
      <div className="min-w-0 flex-1">
        <p className="text-sm">
          <span className="font-medium">{eventLabel(t, event.type)}</span>
          {summary ? <span className="text-muted-foreground"> — {summary}</span> : null}
        </p>
        <p className="mt-0.5 font-mono text-[11px] text-muted-foreground">
          {event.type} · {formatRelativeTime(event.created_at)}
        </p>
      </div>
    </li>
  );
}

export function StreamEventItem({ event }: { event: StreamEvent }) {
  const { t } = useTranslation();
  const summary = payloadSummary(event.data ?? {});
  return (
    <li className="flex items-start gap-3 py-2">
      <span className="status-pulse mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-500" />
      <div className="min-w-0 flex-1">
        <p className="text-sm">
          <span className="font-medium">{eventLabel(t, event.type)}</span>
          {summary ? <span className="text-muted-foreground"> — {summary}</span> : null}
        </p>
        <p className="mt-0.5 font-mono text-[11px] text-muted-foreground">
          {event.type} · {formatRelativeTime(event.ts)}
        </p>
      </div>
    </li>
  );
}
