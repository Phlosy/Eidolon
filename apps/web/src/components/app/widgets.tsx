import { type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { Badge, type BadgeProps } from "../common/badge";
import { cn } from "../../utils/cn";
import { shortId } from "../../utils/format";

const TONE: Record<string, BadgeProps["variant"]> = {
  proposed: "info",
  accepted: "success",
  rejected: "danger",
  planning: "info",
  active: "violet",
  delivered: "success",
  cancelled: "muted",
  todo: "muted",
  in_progress: "info",
  in_review: "warning",
  done: "success",
  blocked: "danger",
  failed: "danger",
  running: "info",
  completed: "success",
  budget_exceeded: "danger",
  pending: "warning",
  approved: "success",
  rework: "warning",
  ended: "muted",
  pass: "success",
  escalate: "danger",
};

export function StatusBadge({ status, className }: { status: string; className?: string }) {
  const { t } = useTranslation("app");
  const label = t(`status.${status}`, { defaultValue: status });
  return (
    <Badge variant={TONE[status] ?? "default"} className={className} data-status={status}>
      {label}
    </Badge>
  );
}

export function KeyValue({ items }: { items: Array<[string, ReactNode]> }) {
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-xs">
      {items.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-muted-foreground">{k}</dt>
          <dd className="min-w-0 break-words font-medium">{v ?? "—"}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Mono({
  children,
  className,
  title,
}: {
  children: ReactNode;
  className?: string;
  title?: string;
}) {
  return (
    <span className={cn("font-mono text-[11px]", className)} title={title}>
      {children}
    </span>
  );
}

export function IdChip({
  id,
  to,
  title,
}: {
  id: string | null | undefined;
  to?: string;
  title?: string;
}) {
  if (!id) return <span className="text-muted-foreground">—</span>;
  const body = (
    <Mono className="rounded border border-border bg-muted/40 px-1.5 py-0.5" title={title ?? id}>
      {shortId(id)}
    </Mono>
  );
  return to ? (
    <Link to={to} className="hover:underline">
      {body}
    </Link>
  ) : (
    body
  );
}

export function Section({
  title,
  actions,
  children,
  className,
}: {
  title: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={cn("command-panel p-4", className)}>
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 className="text-sm font-semibold">{title}</h2>
        {actions}
      </div>
      {children}
    </section>
  );
}

export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div className="command-panel p-4">
      <p className="text-[11px] uppercase tracking-[0.12em] text-muted-foreground">{label}</p>
      <p className="mt-1 text-2xl font-semibold tracking-tight">{value}</p>
      {hint ? <p className="mt-1 text-xs text-muted-foreground">{hint}</p> : null}
    </div>
  );
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <p
      className="rounded-xl border border-danger/30 bg-danger/10 px-3 py-2 text-xs text-danger"
      role="alert"
    >
      {error instanceof Error ? error.message : String(error)}
    </p>
  );
}
