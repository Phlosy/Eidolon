import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Plus } from "lucide-react";
import { useCreateEmployeeProvider, useEmployeeProviders } from "../../hooks/useProviders";
import { Button } from "../common/button";
import { Dialog } from "../common/dialog";
import { Input } from "../common/input";
import { EmptyState, ErrorState } from "../common/states";
import { Skeleton } from "../common/skeleton";
import { ProviderCard } from "./provider-card";
import { PROVIDER_TYPES } from "./constants";
import { enumLabel } from "../../utils/labels";
import type { ProviderType } from "../../types";

const selectClass =
  "w-full rounded-md border border-border bg-transparent px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50";

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-muted-foreground">{label}</span>
      {children}
    </label>
  );
}

/**
 * Employee → Runtime tab: manages THE EMPLOYEE's own provider accounts
 * (GET/POST /employees/{id}/providers). Company-shared accounts appear in the
 * list with a scope badge but are owned elsewhere. The API key field is
 * write-only; only the masked credential is ever rendered.
 */
export function EmployeeProvidersSection({ employeeId }: { employeeId: number }) {
  const { t } = useTranslation();
  const providersQuery = useEmployeeProviders(employeeId);
  const create = useCreateEmployeeProvider(employeeId);

  const [addOpen, setAddOpen] = useState(false);
  const [name, setName] = useState("");
  const [providerType, setProviderType] = useState<ProviderType>("openai");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("");

  const resetForm = () => {
    setName("");
    setProviderType("openai");
    setBaseUrl("");
    setApiKey("");
    setModel("");
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    create.mutate(
      {
        name: name.trim(),
        provider_type: providerType,
        ...(baseUrl.trim() ? { base_url: baseUrl.trim() } : {}),
        ...(apiKey ? { api_key: apiKey } : {}),
        ...(model.trim() ? { model: model.trim() } : {}),
      },
      {
        onSuccess: () => {
          setAddOpen(false);
          resetForm();
        },
      },
    );
  };

  const providers = providersQuery.data ?? [];

  return (
    <div>
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            {t("provider:employeeSection.title")}
          </h4>
          <p className="mt-1 text-xs text-muted-foreground">
            {t("provider:employeeSection.description")}
          </p>
        </div>
        <Button size="sm" onClick={() => setAddOpen(true)}>
          <Plus className="h-3.5 w-3.5" />
          {t("provider:employeeSection.add")}
        </Button>
      </div>

      {providersQuery.isLoading ? (
        <div className="space-y-3">
          <Skeleton className="h-24 w-full" />
        </div>
      ) : providersQuery.isError ? (
        <ErrorState error={providersQuery.error} onRetry={() => providersQuery.refetch()} />
      ) : providers.length === 0 ? (
        <EmptyState
          title={t("provider:employeeSection.emptyTitle")}
          hint={t("provider:employeeSection.emptyHint")}
        />
      ) : (
        <div className="space-y-3">
          {providers.map((provider) => (
            <ProviderCard key={provider.id} provider={provider} />
          ))}
        </div>
      )}

      <Dialog
        open={addOpen}
        onOpenChange={setAddOpen}
        title={t("provider:employeeSection.add")}
        description={t("provider:employeeSection.addDescription")}
      >
        <form onSubmit={submit} className="space-y-3">
          <Field label={t("provider:form.name")}>
            <Input required value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <Field label={t("provider:form.providerType")}>
            <select
              className={selectClass}
              value={providerType}
              onChange={(e) => setProviderType(e.target.value as ProviderType)}
            >
              {PROVIDER_TYPES.map((type) => (
                <option key={type} value={type}>
                  {enumLabel(t, "provider:type", type)}
                </option>
              ))}
            </select>
          </Field>
          <Field label={t("provider:form.baseUrlOptional")}>
            <Input
              placeholder={t("provider:form.baseUrlPlaceholder")}
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
            />
          </Field>
          <Field label={t("provider:form.apiKey")}>
            <Input
              type="password"
              autoComplete="new-password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
            />
          </Field>
          <Field label={t("provider:form.modelOptional")}>
            <Input
              placeholder={t("provider:modelSelector.placeholder")}
              value={model}
              onChange={(e) => setModel(e.target.value)}
            />
          </Field>
          {create.isError ? (
            <p className="text-xs text-red-600 dark:text-red-400" role="alert">
              {create.error.message}
            </p>
          ) : null}
          <div className="flex justify-end gap-2 pt-1">
            <Button type="button" variant="outline" size="sm" onClick={() => setAddOpen(false)}>
              {t("common:cancel")}
            </Button>
            <Button type="submit" size="sm" disabled={create.isPending || !name.trim()}>
              {create.isPending ? t("runtime:wizard.saving") : t("provider:form.addProvider")}
            </Button>
          </div>
        </form>
      </Dialog>
    </div>
  );
}
