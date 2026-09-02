import { useTranslation } from "react-i18next";
import { useEmployeeEntitlements } from "../../hooks/useLifecycle";
import { Badge } from "../common/badge";
import { EmptyState, ErrorState } from "../common/states";
import { Skeleton } from "../common/skeleton";
import { enumLabel } from "../../utils/labels";
import type { EmployeeEntitlement } from "../../types";

/** Access tab: effective entitlements grouped by resource_type, each with its source packages. */
export function AccessTab({ employeeId }: { employeeId: number }) {
  const { t } = useTranslation();
  const entitlementsQuery = useEmployeeEntitlements(employeeId);

  if (entitlementsQuery.isLoading) {
    return <Skeleton className="h-32 w-full" />;
  }
  if (entitlementsQuery.isError) {
    return (
      <ErrorState error={entitlementsQuery.error} onRetry={() => entitlementsQuery.refetch()} />
    );
  }
  const entitlements = entitlementsQuery.data ?? [];
  if (entitlements.length === 0) {
    return (
      <EmptyState title={t("lifecycle:access.emptyTitle")} hint={t("lifecycle:access.emptyHint")} />
    );
  }
  return <EntitlementList entitlements={entitlements} />;
}

/** Pure grouped rendering, exported for tests. */
export function EntitlementList({ entitlements }: { entitlements: EmployeeEntitlement[] }) {
  const { t } = useTranslation();
  const groups = new Map<string, EmployeeEntitlement[]>();
  for (const item of entitlements) {
    const list = groups.get(item.entitlement.resource_type) ?? [];
    list.push(item);
    groups.set(item.entitlement.resource_type, list);
  }
  return (
    <div className="space-y-4">
      {[...groups.entries()].map(([resourceType, items]) => (
        <section key={resourceType}>
          <h3 className="mb-2 font-mono text-xs uppercase tracking-wide text-muted-foreground">
            {resourceType}
          </h3>
          <ul className="space-y-1.5">
            {items.map(({ entitlement, sources }) => (
              <li
                key={entitlement.id}
                data-testid="entitlement-row"
                className="flex flex-wrap items-center gap-2 rounded-md border border-border px-3 py-2 text-sm"
              >
                <span className="font-medium">{entitlement.name}</span>
                <span className="font-mono text-[11px] text-muted-foreground">
                  {entitlement.key}
                </span>
                <Badge variant="muted">
                  {enumLabel(t, "lifecycle:access.type", entitlement.type)}
                </Badge>
                <span className="ml-auto flex flex-wrap gap-1">
                  {sources.map((source) => (
                    <Badge key={source.package_id} variant="info" data-testid="entitlement-source">
                      {t("lifecycle:access.source", { name: source.package_name })}
                    </Badge>
                  ))}
                </span>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}
