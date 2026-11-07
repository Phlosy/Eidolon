import i18n from "../i18n";

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
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

/** The ledger keeps micro-units (1 000 000 = 1.00). Show up to four decimals. */
export function formatMicro(micro: number | null | undefined, currency?: string | null): string {
  if (micro == null) return "—";
  const units = micro / 1_000_000;
  const text = units.toLocaleString(i18n.resolvedLanguage ?? undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 4,
  });
  return currency ? `${text} ${currency}` : text;
}

/** Institution budgets are in minor units (cents / 分): 10 000 micro each. */
export function formatUnits(units: number | null | undefined, currency?: string | null): string {
  if (units == null) return "—";
  return formatMicro(units * 10_000, currency);
}

export function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

export function formatLabel(value: string): string {
  return value
    .split(/[_.]/)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

export function shortId(id: string | null | undefined, n = 8): string {
  return id ? id.slice(0, n) : "—";
}

export function initials(name: string): string {
  return name
    .split(/[\s-]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part.charAt(0).toUpperCase())
    .join("");
}
