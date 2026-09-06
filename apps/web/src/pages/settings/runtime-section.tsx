import { useTranslation } from "react-i18next";
import { API_BASE_URL } from "../../api/client";
import { Card, CardContent, CardHeader, CardTitle } from "../../components/common/card";
import { ErrorState } from "../../components/common/states";
import { Skeleton } from "../../components/common/skeleton";
import { RuntimeUpdatesSection } from "../../components/runtime/runtime-updates-section";
import { useSettings } from "../../hooks/useSystem";

function Row({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="flex items-center justify-between gap-4 py-2">
      <span className="text-sm text-muted-foreground">{label}</span>
      <span className="font-mono text-xs">{value}</span>
    </div>
  );
}

/** Runtime：员工执行环境的更新通道，以及当前运行时的只读信息。 */
export function RuntimeSection() {
  const { t } = useTranslation();
  const settingsQuery = useSettings();

  return (
    <div className="space-y-5">
      <RuntimeUpdatesSection />
      <Card>
        <CardHeader>
          <CardTitle>{t("settings:runtimeSection")}</CardTitle>
        </CardHeader>
        <CardContent>
          {settingsQuery.isLoading ? (
            <Skeleton className="h-40 w-full" />
          ) : settingsQuery.isError ? (
            <ErrorState error={settingsQuery.error} onRetry={() => settingsQuery.refetch()} />
          ) : settingsQuery.data ? (
            <div className="divide-y divide-border/60">
              <Row label={t("settings:rows.companyName")} value={settingsQuery.data.company_name} />
              <Row label={t("settings:rows.runtimeMode")} value={settingsQuery.data.runtime_mode} />
              <Row
                label={t("settings:rows.workspaceRoot")}
                value={settingsQuery.data.workspace_root}
              />
              <Row label={t("settings:rows.apiPort")} value={settingsQuery.data.api_port} />
              <Row label={t("settings:rows.webPort")} value={settingsQuery.data.web_port} />
              <Row
                label={t("settings:rows.apiBaseUrl")}
                value={API_BASE_URL || t("settings:apiBaseUrlProxy")}
              />
            </div>
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
}
