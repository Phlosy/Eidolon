import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  useCreateProvider,
  useDeleteProvider,
  useProviders,
  useUpdateProvider,
} from "../../hooks/useProviders";
import { listProviderModels } from "../../api/providers";
import { Card, CardContent, CardHeader, CardTitle } from "../common/card";
import { Button } from "../common/button";
import { Dialog } from "../common/dialog";
import { EmptyState, ErrorState } from "../common/states";
import { Skeleton } from "../common/skeleton";
import { ProviderCard } from "./provider-card";
import { ProviderForm } from "./provider-form";
import type { CreateProviderInput, Provider } from "../../types";

/** Settings → Providers: manage company and employee-private LLM providers. */
export function ProvidersSection() {
  const { t } = useTranslation();
  const providersQuery = useProviders();
  const create = useCreateProvider();
  const update = useUpdateProvider();
  const remove = useDeleteProvider();

  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<Provider | null>(null);
  const [modelsFor, setModelsFor] = useState<Provider | null>(null);
  const [models, setModels] = useState<string[] | null>(null);
  const [modelsError, setModelsError] = useState<string | null>(null);

  const openCreate = () => {
    setEditing(null);
    setFormOpen(true);
  };
  const openEdit = (provider: Provider) => {
    setEditing(provider);
    setFormOpen(true);
  };

  const handleSubmit = (input: CreateProviderInput) => {
    if (editing) {
      update.mutate({ id: editing.id, body: input }, { onSuccess: () => setFormOpen(false) });
    } else {
      create.mutate(input, { onSuccess: () => setFormOpen(false) });
    }
  };

  const handleDiscoverModels = async (provider: Provider) => {
    setModelsFor(provider);
    setModels(null);
    setModelsError(null);
    try {
      const result = await listProviderModels(provider.id);
      setModels(result.models);
    } catch (error) {
      setModelsError(error instanceof Error ? error.message : t("provider:discoveryFailed"));
    }
  };

  const busy = create.isPending || update.isPending || remove.isPending;

  return (
    <Card className="mb-6">
      <CardHeader className="flex-row items-center justify-between">
        <div>
          <CardTitle>{t("provider:sectionTitle")}</CardTitle>
          <p className="mt-1 text-xs text-muted-foreground">{t("provider:sectionDescription")}</p>
        </div>
        <Button size="sm" onClick={openCreate}>
          {t("provider:add")}
        </Button>
      </CardHeader>
      <CardContent>
        {providersQuery.isLoading ? (
          <div className="space-y-3">
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-24 w-full" />
          </div>
        ) : providersQuery.isError ? (
          <ErrorState error={providersQuery.error} onRetry={() => providersQuery.refetch()} />
        ) : (providersQuery.data ?? []).length === 0 ? (
          <EmptyState title={t("provider:emptyTitle")} hint={t("provider:emptyHint")} />
        ) : (
          <div className="space-y-3">
            {providersQuery.data!.map((provider) => (
              <ProviderCard
                key={provider.id}
                provider={provider}
                busy={busy}
                onEdit={openEdit}
                onToggleEnabled={(p) => update.mutate({ id: p.id, body: { enabled: !p.enabled } })}
                onDelete={(p) => remove.mutate(p.id)}
                onDiscoverModels={handleDiscoverModels}
              />
            ))}
          </div>
        )}

        <Dialog
          open={formOpen}
          onOpenChange={setFormOpen}
          title={editing ? t("provider:editTitle", { name: editing.name }) : t("provider:add")}
        >
          <ProviderForm
            initial={editing ?? undefined}
            submitting={create.isPending || update.isPending}
            onSubmit={handleSubmit}
            onCancel={() => setFormOpen(false)}
          />
        </Dialog>

        <Dialog
          open={modelsFor != null}
          onOpenChange={(open) => !open && setModelsFor(null)}
          title={modelsFor ? t("provider:modelsTitle", { name: modelsFor.name }) : t("provider:modelsFallback")}
        >
          {modelsError ? (
            <p className="text-xs text-red-600 dark:text-red-400">{modelsError}</p>
          ) : models == null ? (
            <Skeleton className="h-24 w-full" />
          ) : models.length === 0 ? (
            <p className="text-xs text-muted-foreground">{t("provider:noModelsReturned")}</p>
          ) : (
            <ul className="max-h-64 space-y-1 overflow-auto font-mono text-xs">
              {models.map((m) => (
                <li key={m} className="rounded border border-border/60 px-2 py-1">
                  {m}
                </li>
              ))}
            </ul>
          )}
        </Dialog>
      </CardContent>
    </Card>
  );
}
