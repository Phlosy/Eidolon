import { useState } from "react";
import { ExternalLink, GitBranch, Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useInstallBuiltinGit, useStartBuiltinGit, useStopBuiltinGit } from "../../hooks/useGit";
import { Badge } from "../common/badge";
import { Button } from "../common/button";
import { Dialog } from "../common/dialog";
import { enumLabel } from "../../utils/labels";
import type { StatusVariant } from "../../utils/status";
import type { GitBuiltinStatus, GitOverview } from "../../types";

const STATUS_VARIANT: Record<GitBuiltinStatus, StatusVariant> = {
  not_installed: "muted",
  installing: "info",
  stopped: "warning",
  running: "success",
  error: "danger",
};

type BuiltinState = GitOverview["builtin"];

/**
 * Settings → Git: the built-in Gitea lifecycle card (install → start/stop).
 * While status is "installing" the overview query polls (see useGitOverview).
 */
export function BuiltinGiteaCard({ builtin }: { builtin: BuiltinState }) {
  const { t } = useTranslation();
  const install = useInstallBuiltinGit();
  const start = useStartBuiltinGit();
  const stop = useStopBuiltinGit();
  const [confirmOpen, setConfirmOpen] = useState(false);

  const busy = install.isPending || start.isPending || stop.isPending;
  const unavailable = !builtin.docker_available;

  return (
    <div
      data-testid="builtin-gitea-card"
      className={
        unavailable
          ? "rounded-lg border border-border bg-card p-4 opacity-60"
          : "rounded-lg border border-border bg-card p-4 shadow-card transition-shadow duration-200 hover:shadow-lift"
      }
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-accent/10">
            <GitBranch className="h-4 w-4 text-accent" />
          </div>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <p className="text-sm font-semibold">{t("git:builtin.title")}</p>
              <Badge variant={STATUS_VARIANT[builtin.status]}>
                {enumLabel(t, "git:builtin.status", builtin.status)}
              </Badge>
            </div>
            {unavailable ? (
              <p className="mt-1 text-xs text-muted-foreground">
                {t("git:builtin.dockerUnavailable")}
              </p>
            ) : builtin.status === "not_installed" ? (
              <p className="mt-1 text-xs text-muted-foreground">{t("git:builtin.description")}</p>
            ) : builtin.status === "installing" ? (
              <p className="mt-1 inline-flex items-center gap-1.5 text-xs text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                {t("git:builtin.installing")}
              </p>
            ) : builtin.status === "error" ? (
              <p className="mt-1 text-xs text-red-600 dark:text-red-400">
                {t("git:builtin.errorHint")}
              </p>
            ) : (
              <p className="mt-1 font-mono text-[11px] text-muted-foreground">
                {builtin.version ? t("git:builtin.version", { version: builtin.version }) : null}
                {builtin.version && builtin.url ? " · " : ""}
                {builtin.url ? (
                  <a
                    href={builtin.url}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 text-foreground underline"
                  >
                    {t("git:builtin.openUi")}
                    <ExternalLink className="h-3 w-3" />
                  </a>
                ) : null}
              </p>
            )}
          </div>
        </div>
        {!unavailable ? (
          <div className="flex shrink-0 items-center gap-2">
            {builtin.status === "not_installed" ? (
              <Button size="sm" disabled={busy} onClick={() => setConfirmOpen(true)}>
                {t("git:builtin.install")}
              </Button>
            ) : null}
            {builtin.status === "stopped" ? (
              <Button size="sm" disabled={busy} onClick={() => start.mutate()}>
                {t("git:builtin.start")}
              </Button>
            ) : null}
            {builtin.status === "running" ? (
              <Button
                variant="outline"
                size="sm"
                disabled={busy}
                onClick={() => stop.mutate()}
              >
                {t("git:builtin.stop")}
              </Button>
            ) : null}
          </div>
        ) : null}
      </div>

      <Dialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title={t("git:builtin.installConfirmTitle")}
        description={t("git:builtin.installConfirmText")}
      >
        <div className="flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={() => setConfirmOpen(false)}>
            {t("common:cancel")}
          </Button>
          <Button
            size="sm"
            disabled={install.isPending}
            onClick={() =>
              install.mutate(undefined, { onSuccess: () => setConfirmOpen(false) })
            }
          >
            {install.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
            {t("git:builtin.installConfirmAction")}
          </Button>
        </div>
      </Dialog>
    </div>
  );
}
