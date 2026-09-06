import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Plus } from "lucide-react";
import { useCreateProvider, useProviders } from "../../hooks/useProviders";
import { useEmployees } from "../../hooks/useEmployees";
import { Badge } from "../common/badge";
import { Button } from "../common/button";
import { Card, CardContent, CardHeader, CardTitle } from "../common/card";
import { Dialog } from "../common/dialog";
import { EmptyState, ErrorState } from "../common/states";
import { Skeleton } from "../common/skeleton";
import { ProviderPresetFields, type ProviderPresetValue } from "./provider-preset-fields";
import { isValidModelName } from "./constants";
import { ProviderScopeBadge } from "./provider-scope-badge";
import { ProviderTestButton } from "./provider-test-button";
import { enumLabel } from "../../utils/labels";

const INITIAL_FORM: ProviderPresetValue = {
  name: "",
  providerType: "openai",
  baseUrl: "",
  apiKey: "",
  model: "",
  entries: [],
  primaryModel: "",
};

/**
 * Settings → Providers: 公司级服务商管理。新建走预设 + 条目式模型编辑器
 * （探测/手填/默认模型）；公司级模型目录存在 provider 上，员工的模型绑定
 * 在 Employee → Runtime 里管。测试连接只读不写。
 */
export function ProvidersOverviewTable() {
  const { t } = useTranslation();
  const providersQuery = useProviders();
  const employeesQuery = useEmployees();
  const create = useCreateProvider();

  const [addOpen, setAddOpen] = useState(false);
  const [formError, setFormError] = useState("");
  const [form, setForm] = useState<ProviderPresetValue>(INITIAL_FORM);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!form.name.trim()) return;
    const entries = form.entries.filter((entry) => entry.enabled && entry.model.trim());
    if (entries.some((entry) => !isValidModelName(entry.model.trim()))) {
      setFormError(t("provider:form.modelInvalid"));
      return;
    }
    setFormError("");
    create.mutate(
      {
        name: form.name.trim(),
        provider_type: form.providerType,
        scope: "company",
        ...(form.baseUrl.trim() ? { base_url: form.baseUrl.trim() } : {}),
        ...(form.apiKey ? { api_key: form.apiKey } : {}),
        ...(entries.length
          ? {
              models: entries.map((entry) => ({
                model: entry.model.trim(),
                alias: entry.alias.trim(),
              })),
              primary_model: form.primaryModel || undefined,
            }
          : {}),
      },
      {
        onSuccess: () => {
          setAddOpen(false);
          setForm(INITIAL_FORM);
        },
      },
    );
  };

  const employees = employeesQuery.data ?? [];
  const providers = providersQuery.data ?? [];

  const ownerName = (id: number | null) =>
    id == null ? "—" : (employees.find((e) => e.id === id)?.name ?? `#${id}`);

  return (
    <Card className="mb-6">
      <CardHeader>
        <div className="flex w-full items-start justify-between gap-3">
          <div>
            <CardTitle>{t("provider:overview.title")}</CardTitle>
            <p className="mt-1 text-xs text-muted-foreground">
              {t("provider:overview.description")}
            </p>
          </div>
          <Button size="sm" onClick={() => setAddOpen(true)}>
            <Plus className="h-3.5 w-3.5" />
            {t("provider:overview.add")}
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        {providersQuery.isLoading ? (
          <Skeleton className="h-32 w-full" />
        ) : providersQuery.isError ? (
          <ErrorState error={providersQuery.error} onRetry={() => providersQuery.refetch()} />
        ) : providers.length === 0 ? (
          <EmptyState title={t("provider:emptyTitle")} hint={t("provider:overview.emptyHint")} />
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
                  <th className="px-3 py-2 font-medium">
                    {t("provider:overview.columns.actions")}
                  </th>
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
                      {provider.available_models.length > 0 ? (
                        <p className="mt-0.5 text-[11px] text-muted-foreground">
                          {t("provider:overview.modelsSummary", {
                            count: provider.available_models.length,
                            default: provider.default_model ?? "—",
                          })}
                        </p>
                      ) : null}
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

      <Dialog
        open={addOpen}
        onOpenChange={setAddOpen}
        title={t("provider:overview.addTitle")}
        description={t("provider:overview.addDescription")}
        className="max-w-2xl"
      >
        <form onSubmit={submit} className="space-y-3">
          <ProviderPresetFields
            modelMode="multi"
            value={form}
            onChange={(patch) => setForm((current) => ({ ...current, ...patch }))}
          />
          {formError ? (
            <p className="text-xs text-red-600 dark:text-red-400" role="alert">
              {formError}
            </p>
          ) : null}
          {create.isError ? (
            <p className="text-xs text-red-600 dark:text-red-400" role="alert">
              {create.error.message}
            </p>
          ) : null}
          <div className="flex justify-end gap-2 pt-1">
            <Button type="button" variant="outline" size="sm" onClick={() => setAddOpen(false)}>
              {t("common:cancel")}
            </Button>
            <Button type="submit" size="sm" disabled={create.isPending || !form.name.trim()}>
              {create.isPending ? t("runtime:wizard.saving") : t("provider:form.addProvider")}
            </Button>
          </div>
        </form>
      </Dialog>
    </Card>
  );
}
