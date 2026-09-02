import { useTranslation } from "react-i18next";
import { Package } from "lucide-react";
import { useAccessPackages } from "../../hooks/useLifecycle";
import { Badge } from "../common/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../common/card";
import { EmptyState, ErrorState } from "../common/states";
import { Skeleton } from "../common/skeleton";
import { enumLabel } from "../../utils/labels";

/** Settings section: read-only access package catalog (v0.4, no editor in v1). */
export function AccessPackagesSection() {
  const { t } = useTranslation();
  const packagesQuery = useAccessPackages();

  return (
    <Card className="mb-6">
      <CardHeader>
        <CardTitle>{t("lifecycle:packages.settingsTitle")}</CardTitle>
        <CardDescription>{t("lifecycle:packages.settingsDescription")}</CardDescription>
      </CardHeader>
      <CardContent>
        {packagesQuery.isLoading ? (
          <Skeleton className="h-32 w-full" />
        ) : packagesQuery.isError ? (
          <ErrorState error={packagesQuery.error} onRetry={() => packagesQuery.refetch()} />
        ) : (packagesQuery.data ?? []).length === 0 ? (
          <EmptyState title={t("lifecycle:packages.emptyTitle")} />
        ) : (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {packagesQuery.data!.map((pkg) => (
              <div
                key={pkg.id}
                data-testid="access-package-card"
                className="rounded-lg border border-border p-3.5"
              >
                <div className="mb-1 flex items-center gap-2">
                  <span className="flex h-7 w-7 items-center justify-center rounded-md border border-accent/25 bg-accent/10 text-accent">
                    <Package className="h-3.5 w-3.5" />
                  </span>
                  <span className="text-sm font-medium">{pkg.name}</span>
                  <Badge variant={pkg.built_in ? "muted" : "info"}>
                    {pkg.built_in
                      ? t("lifecycle:packages.builtIn")
                      : t("lifecycle:packages.custom")}
                  </Badge>
                  <span className="ml-auto text-[11px] text-muted-foreground">
                    {t("lifecycle:packages.entitlementCount", { count: pkg.entitlements.length })}
                  </span>
                </div>
                {pkg.description ? (
                  <p className="mb-2 text-xs text-muted-foreground">{pkg.description}</p>
                ) : null}
                <div className="flex flex-wrap gap-1">
                  {pkg.entitlements.map((entitlement) => (
                    <Badge key={entitlement.id} variant="default">
                      {entitlement.name}
                      <span className="text-muted-foreground">
                        · {enumLabel(t, "lifecycle:access.type", entitlement.type)}
                      </span>
                    </Badge>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
