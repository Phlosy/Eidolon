import { useTranslation } from "react-i18next";
import {
  Activity,
  BookOpen,
  Boxes,
  Building2,
  Cpu,
  FileText,
  FolderKanban,
  GraduationCap,
  HardDrive,
  ListTodo,
  Plug,
  Sparkles,
  User,
  type LucideIcon,
} from "lucide-react";
import { formatRelativeTime } from "../../utils/format";
import { eventLabel, payloadSummary } from "./event-utils";
import type { CompanyEvent, StreamEvent } from "../../types";

/** Event domain → icon + tinted chip (uses the --status-* tokens). */
const EVENT_DOMAIN_META: Record<string, { icon: LucideIcon; chipClass: string }> = {
  task: {
    icon: ListTodo,
    chipClass: "border-status-working/25 bg-status-working/10 text-status-working",
  },
  project: {
    icon: FolderKanban,
    chipClass: "border-status-reflecting/25 bg-status-reflecting/10 text-status-reflecting",
  },
  employee: {
    icon: User,
    chipClass: "border-status-meeting/25 bg-status-meeting/10 text-status-meeting",
  },
  runtime: {
    icon: Cpu,
    chipClass: "border-status-learning/25 bg-status-learning/10 text-status-learning",
  },
  provider: {
    icon: Plug,
    chipClass: "border-status-researching/25 bg-status-researching/10 text-status-researching",
  },
  artifact: { icon: FileText, chipClass: "border-accent/25 bg-accent/10 text-accent" },
  learning: {
    icon: GraduationCap,
    chipClass: "border-status-learning/25 bg-status-learning/10 text-status-learning",
  },
  knowledge: {
    icon: BookOpen,
    chipClass: "border-status-researching/25 bg-status-researching/10 text-status-researching",
  },
  skill: {
    icon: Sparkles,
    chipClass: "border-status-reflecting/25 bg-status-reflecting/10 text-status-reflecting",
  },
  company: { icon: Building2, chipClass: "border-border bg-muted text-muted-foreground" },
  resource: {
    icon: Boxes,
    chipClass: "border-status-researching/25 bg-status-researching/10 text-status-researching",
  },
  asset: {
    icon: HardDrive,
    chipClass: "border-status-meeting/25 bg-status-meeting/10 text-status-meeting",
  },
};

const DEFAULT_EVENT_META = {
  icon: Activity,
  chipClass: "border-border bg-muted text-muted-foreground",
};

function eventDomainMeta(type: string) {
  return EVENT_DOMAIN_META[type.split(".")[0]] ?? DEFAULT_EVENT_META;
}

function EventChip({ type }: { type: string }) {
  const meta = eventDomainMeta(type);
  const Icon = meta.icon;
  return (
    <span
      className={`mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md border ${meta.chipClass}`}
    >
      <Icon className="h-3 w-3" />
    </span>
  );
}

export function EventItem({ event }: { event: CompanyEvent }) {
  const { t } = useTranslation();
  const summary = payloadSummary(event.payload ?? {});
  return (
    <li className="flex items-start gap-3 py-2">
      <EventChip type={event.type} />
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
    <li className="ticker-in flex items-start gap-3 py-2">
      <EventChip type={event.type} />
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
