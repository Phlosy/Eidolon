import { useTranslation } from "react-i18next";
import { useProviders } from "../../hooks/useProviders";
import { useEmployees } from "../../hooks/useEmployees";
import { Badge } from "../common/badge";
import { Card, CardContent, CardHeader, CardTitle } from "../common/card";
import { EmptyState, ErrorState } from "../common/states";
import { Skeleton } from "../common/skeleton";
import { ProviderScopeBadge } from "./provider-scope-badge";
import { ProviderTestButton } from "./provider-test-button";
import { enumLabel } from "../../utils/labels";

/**
 * Settings → Providers: read-only company overview (v0.3 — provider accounts
 * are managed per employee on Employee → Runtime). Test Connection stays
 * available since it doesn't mutate anything.
 */
export function ProvidersOverviewTable() {
  const { t } = useTranslation();
  const providersQuery = useProviders();
  const employeesQuery = useEmployees();

  const employees = employeesQuery.data ?? [];
  const providers = providersQuery.data ?? [];

  const ownerName = (id: number | null) =>
    id == null ? "—" : (employees.find((e) => e.id === id)?.name ?? `#${id}`);

  return (
    <Card className="mb-6">
      <CardHeader>
        <div>
          <CardTitle>{t("provider:overview.title")}</CardTitle>
          <p className="mt-1 text-xs text-muted-foreground">{t("provider:overview.description")}</p>
        </div>
      </CardHeader>
      <CardContent>
        {providersQuery.isLoading ? (
          <Skeleton className="h-32 w-full" />
        ) : providersQuery.isError ? (
          <ErrorState error={providersQuery.error} onRetry={() => providersQuery.refetch()} />
        ) : providers.length === 0 ? (
          <EmptyState
            title={t("provider:emptyTitle")}
            hint={t("provider:overview.emptyHint")}
          />
        ) : (
          <div className="overflow-x-auto rounded-lg border border-border">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-xs text-muted-foreground">
                  <th className="px-3 py-2 font-medium">{t("provider:overview.columns.name")}</th>
                  <th className="px-3 py-2 font-medium">{t("provider:overview.columns.type")}</th>
                  <th className="px-3 py-2 font-medium">{t("provider:overview.columns.scope")}</th>
                  <th className="px-3 py-2 font-medium">{t("provider:overview.columns.owner")}</th>
                  <th className="px-3 py-2 font-medium">{t("provider:overview.columns.inUse")}</th>
                  <th className="px-3 py-2 font-medium">{t("provider:overview.columns.status")}</th>
                  <th className="px-3 py-2 font-medium">{t("provider:overview.columns.actions")}</th>
                </tr>
              </thead>
              <tbody>
                {providers.map((provider) => (
                  <tr
                    key={provider.id}
                    data-testid={`provider-row-${provider.id}`}
                    className="border-b border-border/60 last:border-0"
                  >
                    <td className="px-3 py-2">
                      <p className="font-medium">{provider.name}</p>
                      <p className="font-mono text-[11px] text-muted-foreground">
                        {provider.credential_mask ?? t("provider:noCredential")}
                      </p>
                    </td>
                    <td className="px-3 py-2 text-xs">
                      {enumLabel(t, "provider:type", provider.provider_type)}
                    </td>
                    <td className="px-3 py-2">
                      <ProviderScopeBadge scope={provider.scope} />
                    </td>
                    <td className="px-3 py-2 text-xs text-muted-foreground">
                      {ownerName(provider.owner_employee_id)}
                    </td>
                    <td className="px-3 py-2 text-xs text-muted-foreground">
                      {t("provider:inUse", { count: provider.in_use_by })}
                    </td>
                    <td className="px-3 py-2">
                      {provider.enabled ? (
                        <Badge variant="success">{t("provider:overview.enabled")}</Badge>
                      ) : (
                        <Badge variant="muted">{t("provider:disabled")}</Badge>
                      )}
                    </td>
                    <td className="px-3 py-2">
                      <ProviderTestButton providerId={provider.id} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
