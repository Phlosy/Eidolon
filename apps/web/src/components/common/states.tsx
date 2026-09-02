import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { AlertCircle, Inbox, type LucideIcon } from "lucide-react";
import { Button } from "./button";

export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="command-panel flex flex-col items-center justify-center gap-2 border-dashed py-12 text-center">
      <div className="flex h-10 w-10 items-center justify-center rounded-full bg-accent/10">
        <Inbox className="h-5 w-5 text-accent" />
      </div>
      <p className="text-sm font-medium">{title}</p>
      {hint ? <p className="text-xs text-muted-foreground">{hint}</p> : null}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const { t } = useTranslation();
  const message =
    error instanceof Error ? error.message : t("common:errorFallback");
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-[var(--radius-panel)] border border-danger/30 bg-danger/5 py-12 text-center">
      <div className="flex h-10 w-10 items-center justify-center rounded-full bg-danger/10">
        <AlertCircle className="h-5 w-5 text-danger" />
      </div>
      <p className="text-sm font-medium">{t("common:errorTitle")}</p>
      <p className="max-w-md text-xs text-muted-foreground">{message}</p>
      {onRetry ? (
        <Button variant="outline" size="sm" onClick={onRetry}>
          {t("common:retry")}
        </Button>
      ) : null}
    </div>
  );
}

/** i18n'd 404 page for unmatched routes (see routes/index.tsx). */
export function PageNotFound() {
  const { t } = useTranslation();
  return <EmptyState title={t("common:notFound.title")} hint={t("common:notFound.hint")} />;
}

export function PageHeader({
  title,
  description,
  icon: Icon,
  actions,
}: {
  title: string;
  description?: string;
  /** Optional leading icon rendered in a small tile. */
  icon?: LucideIcon;
  actions?: ReactNode;
}) {
  return (
    <div className="mb-5 flex items-start justify-between gap-4">
      <div className="flex items-start gap-3">
        {Icon ? (
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-primary/20 bg-primary/8 shadow-[var(--shadow-panel)]">
            <Icon className="h-4 w-4 text-accent" />
          </div>
        ) : null}
        <div>
          <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
          {description ? (
            <p className="mt-1 text-sm text-muted-foreground">{description}</p>
          ) : null}
        </div>
      </div>
      {actions}
    </div>
  );
}
