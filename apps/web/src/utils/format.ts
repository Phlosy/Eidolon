import i18n from "../i18n";

/** Display number contract (§3.3): Task/Project render as `#EID-{id}`. */
export function eid(id: number | string): string {
  return `#EID-${id}`;
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  // Locale follows the active UI language so dates read naturally in zh-CN / en-US.
  return d.toLocaleString(i18n.resolvedLanguage ?? undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatRelativeTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  const diffSec = Math.max(0, Math.floor((Date.now() - d.getTime()) / 1000));
  if (diffSec < 60) return i18n.t("common:time.secondsAgo", { count: diffSec });
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return i18n.t("common:time.minutesAgo", { count: diffMin });
  const diffH = Math.floor(diffMin / 60);
  if (diffH < 24) return i18n.t("common:time.hoursAgo", { count: diffH });
  return i18n.t("common:time.daysAgo", { count: Math.floor(diffH / 24) });
}

export function formatPercent(rate: number | null | undefined): string {
  if (rate == null) return "—";
  return `${Math.round(rate * 100)}%`;
}

export function formatUsd(amount: number): string {
  return `$${amount.toFixed(2)}`;
}

export function formatLabel(value: string): string {
  return value
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

export function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part.charAt(0).toUpperCase())
    .join("");
}
