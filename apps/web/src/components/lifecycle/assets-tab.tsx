import { useTranslation } from "react-i18next";
import { useEmployeeAssets } from "../../hooks/useLifecycle";
import { useEmployees } from "../../hooks/useEmployees";
import { EmptyState, ErrorState } from "../common/states";
import { Skeleton } from "../common/skeleton";
import { eid } from "../../utils/format";
import { resourceTypeIcon } from "./resource-type-icon";

/** Assets tab: ResourceAsset rows sorted by resource_type. */
export function AssetsTab({ employeeId }: { employeeId: number }) {
  const { t } = useTranslation();
  const assetsQuery = useEmployeeAssets(employeeId);
  const employeesQuery = useEmployees();

  if (assetsQuery.isLoading) {
    return <Skeleton className="h-32 w-full" />;
  }
  if (assetsQuery.isError) {
    return <ErrorState error={assetsQuery.error} onRetry={() => assetsQuery.refetch()} />;
  }
  const assets = [...(assetsQuery.data ?? [])].sort((a, b) =>
    a.resource_type.localeCompare(b.resource_type),
  );
  if (assets.length === 0) {
    return (
      <EmptyState title={t("lifecycle:assets.emptyTitle")} hint={t("lifecycle:assets.emptyHint")} />
    );
  }
  const ownerName = (id: number | null) =>
    id == null ? "—" : (employeesQuery.data?.find((e) => e.id === id)?.name ?? eid(id));

  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border text-left text-xs text-muted-foreground">
            <th className="px-4 py-2.5 font-medium">{t("lifecycle:assets.table.type")}</th>
            <th className="px-4 py-2.5 font-medium">{t("lifecycle:assets.table.externalId")}</th>
            <th className="px-4 py-2.5 font-medium">{t("lifecycle:assets.table.owner")}</th>
            <th className="px-4 py-2.5 font-medium">{t("lifecycle:assets.table.project")}</th>
            <th className="px-4 py-2.5 font-medium">{t("lifecycle:assets.table.provider")}</th>
          </tr>
        </thead>
        <tbody>
          {assets.map((asset) => {
            const Icon = resourceTypeIcon(asset.resource_type);
            return (
              <tr key={asset.id} className="border-b border-border/60 last:border-0">
                <td className="px-4 py-2.5">
                  <span className="flex items-center gap-2">
                    <Icon className="h-3.5 w-3.5 text-accent" />
                    {asset.resource_type}
                  </span>
                </td>
                <td className="px-4 py-2.5 font-mono text-xs text-muted-foreground">
                  {asset.external_id}
                </td>
                <td className="px-4 py-2.5 text-muted-foreground">
                  {ownerName(asset.owner_employee_id)}
                </td>
                <td className="px-4 py-2.5 font-mono text-xs text-muted-foreground">
                  {asset.project_id != null ? eid(asset.project_id) : "—"}
                </td>
                <td className="px-4 py-2.5 font-mono text-xs text-muted-foreground">
                  {asset.provider_key ?? "—"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
