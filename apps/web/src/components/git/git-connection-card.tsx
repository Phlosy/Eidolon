import { useState } from "react";
import { Check, Loader2, XCircle } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useDeleteGitConnection, useTestGitConnection } from "../../hooks/useGit";
import { Badge } from "../common/badge";
import { Button } from "../common/button";
import { Dialog } from "../common/dialog";
import { PlatformBadge } from "./platform-badge";
import type { GitConnection, GitTestResult } from "../../types";

/** Test Connection button + inline ok/version/latency result (transient). */
export function GitTestButton({ connectionId }: { connectionId: number }) {
  const { t } = useTranslation();
  const test = useTestGitConnection();
  const [result, setResult] = useState<GitTestResult | null>(null);

  return (
    <span className="inline-flex items-center gap-2">
      <Button
        variant="outline"
        size="sm"
        disabled={test.isPending}
        onClick={() =>
          test.mutate(connectionId, {
            onSuccess: setResult,
          })
        }
      >
        {test.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
        {t("git:test.button")}
      </Button>
      {result ? (
        result.ok ? (
          <span
            data-testid="git-test-ok"
            className="inline-flex items-center gap-1 text-xs text-emerald-600 dark:text-emerald-400"
          >
            <Check className="h-3.5 w-3.5" />
            {result.version
              ? t("git:test.okWithVersion", { version: result.version })
              : t("git:test.ok")}
            {result.latency_ms != null ? (
              <span className="font-mono text-muted-foreground">
                {t("git:test.latency", { ms: result.latency_ms })}
              </span>
            ) : null}
          </span>
        ) : (
          <span
            data-testid="git-test-fail"
            className="inline-flex items-center gap-1 text-xs text-red-600 dark:text-red-400"
          >
            <XCircle className="h-3.5 w-3.5" />
            {t("git:test.failed", { error: result.error ?? t("git:test.unknownError") })}
          </span>
        )
      ) : null}
    </span>
  );
}

/**
 * One external Git connection. Never renders token material — only the
 * server-side `credential_mask`. Delete goes through a confirm dialog.
 */
export function GitConnectionCard({ connection }: { connection: GitConnection }) {
  const { t } = useTranslation();
  const remove = useDeleteGitConnection();
  const [confirmOpen, setConfirmOpen] = useState(false);

  return (
    <div
      data-testid={`git-connection-card-${connection.id}`}
      className="rounded-lg border border-border bg-card p-4 shadow-card transition-shadow duration-200 hover:shadow-lift"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <p className="truncate text-sm font-semibold">{connection.name}</p>
            <PlatformBadge platform={connection.platform_type} />
            {connection.enabled ? (
              <Badge variant="success">{t("git:connections.enabled")}</Badge>
            ) : (
              <Badge variant="muted">{t("git:connections.disabled")}</Badge>
            )}
          </div>
          <p className="mt-1 font-mono text-[11px] text-muted-foreground">
            {connection.credential_mask ?? t("git:connections.noCredential")}
            {` · ${connection.base_url}`}
          </p>
        </div>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <GitTestButton connectionId={connection.id} />
        <Button
          variant="destructive"
          size="sm"
          disabled={remove.isPending}
          onClick={() => setConfirmOpen(true)}
        >
          {t("git:connections.deleteConfirmAction")}
        </Button>
      </div>

      <Dialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title={t("git:connections.deleteConfirmTitle", { name: connection.name })}
        description={t("git:connections.deleteConfirmText")}
      >
        <div className="flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={() => setConfirmOpen(false)}>
            {t("common:cancel")}
          </Button>
          <Button
            variant="destructive"
            size="sm"
            disabled={remove.isPending}
            onClick={() => remove.mutate(connection.id, { onSuccess: () => setConfirmOpen(false) })}
          >
            {remove.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
            {t("git:connections.deleteConfirmAction")}
          </Button>
        </div>
      </Dialog>
    </div>
  );
}
