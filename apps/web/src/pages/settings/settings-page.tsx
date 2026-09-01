import { Moon, Sun } from "lucide-react";
import { Trans, useTranslation } from "react-i18next";
import { useSettings } from "../../hooks/useSystem";
import { API_BASE_URL } from "../../api/client";
import { Card, CardContent, CardHeader, CardTitle } from "../../components/common/card";
import { Button } from "../../components/common/button";
import { ErrorState, PageHeader } from "../../components/common/states";
import { Skeleton } from "../../components/common/skeleton";
import { LanguageSelector } from "../../components/common/language-selector";
import { ProvidersSection } from "../../components/provider/providers-section";
import { RuntimeUpdatesSection } from "../../components/runtime/runtime-updates-section";
import { useThemeStore } from "../../stores/theme";

function Row({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="flex items-center justify-between gap-4 py-2">
      <span className="text-sm text-muted-foreground">{label}</span>
      <span className="font-mono text-xs">{value}</span>
    </div>
  );
}

export function SettingsPage() {
  const { t } = useTranslation();
  const settingsQuery = useSettings();
  const theme = useThemeStore((s) => s.theme);
  const toggleTheme = useThemeStore((s) => s.toggleTheme);

  const apiDocsUrl = API_BASE_URL
    ? `${API_BASE_URL}/docs`
    : `http://127.0.0.1:${settingsQuery.data?.api_port ?? 26881}/docs`;
  const apiDocsHref = `${API_BASE_URL || `http://127.0.0.1:${settingsQuery.data?.api_port ?? 26881}`}/docs`;

  return (
    <div className="max-w-4xl">
      <PageHeader title={t("settings:title")} description={t("settings:description")} />

      <ProvidersSection />

      <RuntimeUpdatesSection />

      <Card className="mb-6">
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

      <Card className="mb-6">
        <CardHeader>
          <CardTitle>{t("settings:appearance")}</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm text-muted-foreground">
            {t("settings:themeLine", { theme: t(`settings:theme.${theme}`) })}
          </p>
          <Button variant="outline" size="sm" onClick={toggleTheme}>
            {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            {t("settings:switchTheme", { theme: t(`settings:theme.${theme === "dark" ? "light" : "dark"}`) })}
          </Button>
        </CardContent>
      </Card>

      <Card className="mb-6">
        <CardHeader>
          <CardTitle>{t("settings:language")}</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm text-muted-foreground">{t("settings:languageHint")}</p>
          <LanguageSelector />
        </CardContent>
      </Card>

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
    </div>
  );
}
