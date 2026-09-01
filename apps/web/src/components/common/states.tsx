import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { AlertCircle, Inbox } from "lucide-react";
import { Button } from "./button";

export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border py-12 text-center">
      <Inbox className="h-5 w-5 text-muted-foreground" />
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
    <div className="flex flex-col items-center justify-center gap-3 rounded-lg border border-red-500/30 bg-red-500/5 py-12 text-center">
      <AlertCircle className="h-5 w-5 text-red-500" />
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
  actions,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="mb-5 flex items-start justify-between gap-4">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">{title}</h1>
        {description ? <p className="mt-1 text-sm text-muted-foreground">{description}</p> : null}
      </div>
      {actions}
    </div>
  );
}
