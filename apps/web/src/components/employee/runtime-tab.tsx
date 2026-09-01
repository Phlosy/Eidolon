import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  useCheckRuntimeImageUpdates,
  useEmployeeRuntime,
  useRuntimeAction,
  useRuntimeImages,
  useRuntimeTypes,
  useUpdateRuntimeProvider,
} from "../../hooks/useRuntimes";
import { useProviders } from "../../hooks/useProviders";
import { EmptyState, ErrorState } from "../common/states";
import { Skeleton } from "../common/skeleton";
import { Button } from "../common/button";
import { Dialog } from "../common/dialog";
import { RuntimeCard } from "../runtime/runtime-card";
import { RuntimeCapabilitiesGrid } from "../runtime/runtime-capabilities-grid";
import { RuntimeCreateWizard } from "../runtime/runtime-create-wizard";
import { RuntimeLogsViewer } from "../runtime/runtime-logs-viewer";
import { ProviderSelector } from "../provider/provider-selector";
import { ProviderModelSelector } from "../provider/provider-model-selector";
import { enumLabel } from "../../utils/labels";

/** Employee detail → Runtime tab: instance card, actions, logs, provider swap, create wizard. */
export function RuntimeTab({ employeeId }: { employeeId: number }) {
  const { t } = useTranslation();
  const runtimeQuery = useEmployeeRuntime(employeeId);
  const typesQuery = useRuntimeTypes();
  const imagesQuery = useRuntimeImages();
  const providersQuery = useProviders(employeeId);
  const action = useRuntimeAction(employeeId);
  const changeProvider = useUpdateRuntimeProvider(employeeId);
  const checkUpdates = useCheckRuntimeImageUpdates();

  const [wizardOpen, setWizardOpen] = useState(false);
  const [logsOpen, setLogsOpen] = useState(false);
  const [providerOpen, setProviderOpen] = useState(false);
  const [providerId, setProviderId] = useState<number | null>(null);
  const [model, setModel] = useState("");

  if (runtimeQuery.isLoading) {
    return <Skeleton className="h-48 w-full" />;
  }
  if (runtimeQuery.isError) {
    return <ErrorState error={runtimeQuery.error} onRetry={() => runtimeQuery.refetch()} />;
  }

  const instance = runtimeQuery.data ?? null;
  const typeInfo = (typesQuery.data ?? []).find((t) => t.type === (instance?.runtime_type ?? null));
  const image = (imagesQuery.data ?? []).find((img) => img.runtime_type === instance?.runtime_type);

  const openChangeProvider = () => {
    setProviderId(null);
    setModel(instance?.model ?? "");
    setProviderOpen(true);
  };

  const submitProviderChange = () => {
    if (providerId == null || !model.trim()) return;
    changeProvider.mutate(
      { provider_id: providerId, model: model.trim() },
      { onSuccess: () => setProviderOpen(false) },
    );
  };

  if (!instance) {
    return (
      <div className="space-y-3">
        <EmptyState
          title={t("runtime:tab.noInstanceTitle")}
          hint={t("runtime:tab.noInstanceHint")}
        />
        <div>
          <Button size="sm" onClick={() => setWizardOpen(true)}>
            {t("runtime:tab.createRuntime")}
          </Button>
        </div>
        <RuntimeCreateWizard
          open={wizardOpen}
          onOpenChange={setWizardOpen}
          employeeId={employeeId}
          runtimeTypes={typesQuery.data ?? []}
          providers={providersQuery.data ?? []}
        />
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <RuntimeCard
        instance={instance}
        image={image ?? null}
        busy={action.isPending}
        checkingUpdates={checkUpdates.isPending}
        onStop={() => action.mutate({ id: instance.id, op: "stop" })}
        onRestart={() => action.mutate({ id: instance.id, op: "restart" })}
        onViewLogs={() => setLogsOpen(true)}
        onChangeProvider={openChangeProvider}
        onCheckUpdate={() => checkUpdates.mutate()}
      />

      {typeInfo ? (
        <div>
          <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            {t("runtime:tab.capabilitiesHeading", {
              type: enumLabel(t, "runtime:type", typeInfo.type),
            })}
          </h4>
          <RuntimeCapabilitiesGrid capabilities={typeInfo.capabilities} />
        </div>
      ) : null}

      <Dialog
        open={logsOpen}
        onOpenChange={setLogsOpen}
        title={t("runtime:logs.title")}
        className="max-w-2xl"
      >
        <RuntimeLogsViewer runtimeId={instance.id} />
      </Dialog>

      <Dialog
        open={providerOpen}
        onOpenChange={setProviderOpen}
        title={t("runtime:changeProvider.title")}
        description={t("runtime:changeProvider.description")}
      >
        <div className="space-y-3">
          <ProviderSelector
            providers={providersQuery.data ?? []}
            value={providerId}
            supportedTypes={typeInfo?.supported_providers}
            onChange={setProviderId}
          />
          <ProviderModelSelector providerId={providerId} value={model} onChange={setModel} />
          {changeProvider.isError ? (
            <p className="text-xs text-red-600 dark:text-red-400">{changeProvider.error.message}</p>
          ) : null}
          <div className="flex justify-end gap-2">
            <Button variant="outline" size="sm" onClick={() => setProviderOpen(false)}>
              {t("common:cancel")}
            </Button>
            <Button
              size="sm"
              disabled={providerId == null || !model.trim() || changeProvider.isPending}
              onClick={submitProviderChange}
            >
              {changeProvider.isPending
                ? t("runtime:changeProvider.applying")
                : t("runtime:changeProvider.apply")}
            </Button>
          </div>
        </div>
      </Dialog>

      <RuntimeCreateWizard
        open={wizardOpen}
        onOpenChange={setWizardOpen}
        employeeId={employeeId}
        runtimeTypes={typesQuery.data ?? []}
        providers={providersQuery.data ?? []}
      />
    </div>
  );
}
