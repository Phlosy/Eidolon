import { Activity } from "lucide-react";
import { Trans, useTranslation } from "react-i18next";
import { API_BASE_URL } from "../../api/client";
import { Card, CardContent, CardHeader, CardTitle } from "../../components/common/card";
import { Skeleton } from "../../components/common/skeleton";
import { useSettings } from "../../hooks/useSystem";

/** 关于：API 文档入口与环境连接状态。 */
export function AboutSection() {
  const { t } = useTranslation();
  const settingsQuery = useSettings();

  const apiDocsUrl = API_BASE_URL
    ? `${API_BASE_URL}/docs`
    : `http://127.0.0.1:${settingsQuery.data?.api_port ?? 26881}/docs`;
  const apiDocsHref = `${API_BASE_URL || `http://127.0.0.1:${settingsQuery.data?.api_port ?? 26881}`}/docs`;

  return (
    <div className="space-y-5">
      <Card>
        <CardHeader>
          <CardTitle>{t("settings:apiDocs")}</CardTitle>
        </CardHeader>
        <CardContent>
          {settingsQuery.data ? (
            <p className="text-sm text-muted-foreground">
              <Trans
                i18nKey="settings:apiDocsText"
                values={{ url: apiDocsUrl.replace(/\/docs$/, "") }}
                components={{
                  link: (
                    <a
                      href={apiDocsHref}
                      target="_blank"
                      rel="noreferrer"
                      className="font-mono text-xs text-foreground underline"
                    />
                  ),
                }}
              />
            </p>
          ) : (
            <Skeleton className="h-10 w-full" />
          )}
        </CardContent>
      </Card>
      <div className="flex items-center gap-2 rounded-xl border border-border bg-surface/70 px-4 py-3 text-xs text-muted-foreground">
        <Activity className="h-4 w-4 text-success" />
        {t("settings:console.liveNote")}
      </div>
    </div>
  );
}
