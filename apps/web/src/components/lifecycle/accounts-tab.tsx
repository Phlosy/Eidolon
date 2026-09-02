import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ShieldCheck } from "lucide-react";
import { useEmployeeAccounts, useReconcileEmployee } from "../../hooks/useLifecycle";
import { Badge } from "../common/badge";
import { Button } from "../common/button";
import { EmptyState, ErrorState } from "../common/states";
import { Skeleton } from "../common/skeleton";
import { ACCOUNT_STATUS_VARIANT } from "../../utils/status";
import { enumLabel } from "../../utils/labels";
import { formatRelativeTime } from "../../utils/format";
import { resourceTypeIcon } from "./resource-type-icon";
import type { ReconcileDrift } from "../../types";

/** Accounts tab: per-account cards plus the v1 detect-only reconcile action. */
export function AccountsTab({ employeeId }: { employeeId: number }) {
  const { t } = useTranslation();
  const accountsQuery = useEmployeeAccounts(employeeId);
  const reconcile = useReconcileEmployee(employeeId);
  const [drifts, setDrifts] = useState<ReconcileDrift[] | null>(null);

  if (accountsQuery.isLoading) {
    return <Skeleton className="h-32 w-full" />;
  }
  if (accountsQuery.isError) {
    return <ErrorState error={accountsQuery.error} onRetry={() => accountsQuery.refetch()} />;
  }
  const accounts = accountsQuery.data ?? [];

  const runReconcile = () => {
    reconcile.mutate(undefined, { onSuccess: (result) => setDrifts(result.drifts) });
  };

  return (
    <div className="space-y-4">
      {accounts.length === 0 ? (
        <EmptyState
          title={t("lifecycle:accounts.emptyTitle")}
          hint={t("lifecycle:accounts.emptyHint")}
        />
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {accounts.map((account) => {
            const Icon = resourceTypeIcon(account.resource_type);
            return (
              <div
                key={account.id}
                data-testid="account-card"
                className="rounded-lg border border-border p-3.5"
              >
                <div className="mb-2 flex items-center justify-between gap-2">
                  <span className="flex items-center gap-2 text-sm font-medium">
                    <span className="flex h-7 w-7 items-center justify-center rounded-md border border-accent/25 bg-accent/10 text-accent">
                      <Icon className="h-3.5 w-3.5" />
                    </span>
                    {account.display_name ?? account.username}
                  </span>
                  <Badge variant={ACCOUNT_STATUS_VARIANT[account.status]}>
                    {enumLabel(t, "lifecycle:accountStatus", account.status)}
                  </Badge>
                </div>
                <p className="font-mono text-xs text-muted-foreground">
                  {account.resource_type} · @{account.username}
                </p>
                <p className="mt-1 text-[11px] text-muted-foreground">
                  {account.last_synced_at
                    ? t("lifecycle:accounts.lastSynced", {
                        time: formatRelativeTime(account.last_synced_at),
                      })
                    : t("lifecycle:accounts.neverSynced")}
                </p>
              </div>
            );
          })}
        </div>
      )}

      <div className="flex items-center gap-3">
        <Button
          variant="outline"
          size="sm"
          data-testid="reconcile-button"
          disabled={reconcile.isPending}
          onClick={runReconcile}
        >
          <ShieldCheck className="h-3.5 w-3.5" />
          {reconcile.isPending
            ? t("lifecycle:accounts.reconciling")
            : t("lifecycle:accounts.reconcile")}
        </Button>
        {reconcile.isError ? (
          <p className="text-xs text-red-600 dark:text-red-400">{reconcile.error.message}</p>
        ) : null}
      </div>

      {drifts != null ? (
        drifts.length === 0 ? (
          <p className="text-xs text-status-working">{t("lifecycle:accounts.noDrift")}</p>
        ) : (
          <div className="rounded-md border border-amber-500/30 bg-amber-500/5 p-3">
            <p className="mb-2 text-xs font-medium text-amber-600 dark:text-amber-400">
              {t("lifecycle:accounts.driftTitle", { count: drifts.length })}
            </p>
            <ul className="space-y-1 text-xs">
              {drifts.map((drift, i) => (
                <li key={i} data-testid="drift-item" className="flex gap-2">
                  <Badge variant="warning">{drift.kind}</Badge>
                  <span className="font-mono text-muted-foreground">{drift.resource_type}</span>
                  <span>{drift.detail}</span>
                </li>
              ))}
            </ul>
          </div>
        )
      ) : null}
    </div>
  );
}
