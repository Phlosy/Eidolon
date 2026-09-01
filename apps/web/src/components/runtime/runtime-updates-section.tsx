import { useTranslation } from "react-i18next";
import {
  useCheckRuntimeImageUpdates,
  useRuntimeImages,
  useRuntimeTypes,
  useUpdateRuntimeImage,
} from "../../hooks/useRuntimes";
import { Card, CardContent, CardHeader, CardTitle } from "../common/card";
import { Badge } from "../common/badge";
import { Button } from "../common/button";
import { EmptyState, ErrorState } from "../common/states";
import { Skeleton } from "../common/skeleton";
import { RuntimeUpdateBadge } from "./runtime-update-badge";
import { enumLabel } from "../../utils/labels";
import { formatDateTime } from "../../utils/format";
import type { StatusVariant } from "../../utils/status";
import type { RuntimeImageUpdateStatus } from "../../types";

const UPDATE_STATUS_VARIANT: Record<RuntimeImageUpdateStatus, StatusVariant> = {
  idle: "muted",
  checking: "info",
  available: "info",
  downloading: "info",
  updating: "warning",
  verifying: "warning",
  completed: "success",
  failed: "danger",
  rolled_back: "warning",
};

/** Settings → Runtime Updates: image versions, compatibility, and update actions. */
export function RuntimeUpdatesSection() {
  const { t } = useTranslation();
  const imagesQuery = useRuntimeImages();
  const typesQuery = useRuntimeTypes();
  const checkUpdates = useCheckRuntimeImageUpdates();
  const updateImage = useUpdateRuntimeImage();

  const dockerUnavailable = new Set(
    (typesQuery.data ?? []).filter((t) => !t.docker_available).map((t) => t.type),
  );

  return (
    <Card className="mb-6">
      <CardHeader className="flex-row items-center justify-between">
        <div>
          <CardTitle>{t("runtime:updates.title")}</CardTitle>
          <p className="mt-1 text-xs text-muted-foreground">{t("runtime:updates.description")}</p>
        </div>
        <Button
          variant="outline"
          size="sm"
          disabled={checkUpdates.isPending}
          onClick={() => checkUpdates.mutate()}
        >
          {checkUpdates.isPending ? t("runtime:updates.checking") : t("runtime:updates.checkNow")}
        </Button>
      </CardHeader>
      <CardContent>
        {imagesQuery.isLoading ? (
          <Skeleton className="h-40 w-full" />
        ) : imagesQuery.isError ? (
          <ErrorState error={imagesQuery.error} onRetry={() => imagesQuery.refetch()} />
        ) : (imagesQuery.data ?? []).length === 0 ? (
          <EmptyState
            title={t("runtime:updates.emptyTitle")}
            hint={t("runtime:updates.emptyHint")}
          />
        ) : (
          <div className="overflow-x-auto rounded-lg border border-border">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-xs text-muted-foreground">
                  <th className="px-4 py-2.5 font-medium">{t("runtime:updates.table.runtime")}</th>
                  <th className="px-4 py-2.5 font-medium">{t("runtime:updates.table.installed")}</th>
                  <th className="px-4 py-2.5 font-medium">{t("runtime:updates.table.latest")}</th>
                  <th className="px-4 py-2.5 font-medium">{t("runtime:updates.table.status")}</th>
                  <th className="px-4 py-2.5 font-medium">{t("runtime:updates.table.compatibility")}</th>
                  <th className="px-4 py-2.5 font-medium">{t("runtime:updates.table.usedBy")}</th>
                  <th className="px-4 py-2.5 font-medium">{t("runtime:updates.table.checked")}</th>
                  <th className="px-4 py-2.5 font-medium" />
                </tr>
              </thead>
              <tbody>
                {imagesQuery.data!.map((image) => {
                  const updateDisabled =
                    image.compatibility_status === "unverified" ||
                    dockerUnavailable.has(image.runtime_type) ||
                    updateImage.isPending;
                  const disabledReason = dockerUnavailable.has(image.runtime_type)
                    ? t("runtime:updates.dockerUnavailable")
                    : image.compatibility_status === "unverified"
                      ? t("runtime:updates.compatibilityUnverified")
                      : undefined;
                  return (
                    <tr
                      key={image.runtime_type}
                      className="border-b border-border/60 last:border-0"
                    >
                      <td className="px-4 py-2.5">
                        <span className="font-medium">
                          {enumLabel(t, "runtime:type", image.runtime_type)}
                        </span>
                        <span className="ml-2 font-mono text-[11px] text-muted-foreground">
                          {image.repository}:{image.tag}
                        </span>
                      </td>
                      <td className="px-4 py-2.5 font-mono text-xs">
                        {image.installed_version ?? "—"}
                      </td>
                      <td className="px-4 py-2.5 font-mono text-xs">
                        {image.latest_version ?? "—"}
                      </td>
                      <td className="px-4 py-2.5">
                        <span className="inline-flex items-center gap-1.5">
                          <Badge variant={UPDATE_STATUS_VARIANT[image.update_status]}>
                            {enumLabel(t, "runtime:updateStatus", image.update_status)}
                          </Badge>
                          <RuntimeUpdateBadge
                            updateAvailable={image.update_available}
                            compatibilityStatus={image.compatibility_status}
                          />
                        </span>
                      </td>
                      <td className="px-4 py-2.5 text-xs text-muted-foreground">
                        {enumLabel(t, "runtime:compatibility", image.compatibility_status)}
                      </td>
                      <td className="px-4 py-2.5 text-xs text-muted-foreground">{image.used_by}</td>
                      <td className="px-4 py-2.5 font-mono text-[11px] text-muted-foreground">
                        {formatDateTime(image.last_checked_at)}
                      </td>
                      <td className="px-4 py-2.5 text-right">
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={updateDisabled}
                          title={disabledReason}
                          onClick={() => updateImage.mutate(image.runtime_type)}
                        >
                          {t("runtime:updates.update")}
                        </Button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
