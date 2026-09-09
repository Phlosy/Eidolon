import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Plus } from "lucide-react";
import {
  useCheckRuntimeImageUpdates,
  useEmployeeRuntime,
  useRuntimeAction,
  useRuntimeImages,
  useRuntimeTypes,
  useUpdateRuntimeProvider,
} from "../../hooks/useRuntimes";
import { useEmployeeProviders } from "../../hooks/useProviders";
import { useEmployee } from "../../hooks/useEmployees";
import { EmptyState, ErrorState } from "../common/states";
import { Skeleton } from "../common/skeleton";
import { Button } from "../common/button";
import { Dialog } from "../common/dialog";
import { RuntimeCard } from "../runtime/runtime-card";
import { RuntimeCapabilitiesGrid } from "../runtime/runtime-capabilities-grid";
import { RuntimeCreateWizard } from "../runtime/runtime-create-wizard";
import { RuntimeLogsViewer } from "../runtime/runtime-logs-viewer";
import { EmployeeProvidersSection } from "../provider/employee-providers-section";
import { ProviderSelector } from "../provider/provider-selector";
import { ProviderModelSelector } from "../provider/provider-model-selector";
import { enumLabel } from "../../utils/labels";
import type { RuntimeType } from "../../types";

/** Employee detail → Runtime tab: instance card, actions, logs, provider accounts, provider swap, create wizard. */
export function RuntimeTab({ employeeId }: { employeeId: number }) {
  const { t } = useTranslation();
  const runtimeQuery = useEmployeeRuntime(employeeId);
  const employeeQuery = useEmployee(employeeId);
  const typesQuery = useRuntimeTypes();
  const imagesQuery = useRuntimeImages();
  // v0.3: the employee's own provider accounts (+ company-shared) are the source.
  const providersQuery = useEmployeeProviders(employeeId);
  const action = useRuntimeAction(employeeId);
  const changeProvider = useUpdateRuntimeProvider(employeeId);
  const checkUpdates = useCheckRuntimeImageUpdates();

  const [wizardOpen, setWizardOpen] = useState(false);
  const [switchOpen, setSwitchOpen] = useState(false);
  // 从“登记类型 vs 实例类型不一致”横幅进入时，预选员工登记的类型
  const [switchType, setSwitchType] = useState<RuntimeType | null>(null);
  const [logsOpen, setLogsOpen] = useState(false);
  const [providerOpen, setProviderOpen] = useState(false);
  const [providerId, setProviderId] = useState<number | null>(null);
  const [model, setModel] = useState("");
  // 递增信号："更换服务商"弹窗里的新建入口 → 打开下方员工服务商表单
  const [createSignal, setCreateSignal] = useState(0);

  if (runtimeQuery.isLoading) {
    return <Skeleton className="h-48 w-full" />;
  }
  if (runtimeQuery.isError) {
    return <ErrorState error={runtimeQuery.error} onRetry={() => runtimeQuery.refetch()} />;
  }

  const instance = runtimeQuery.data ?? null;
  const typeInfo = (typesQuery.data ?? []).find((t) => t.type === (instance?.runtime_type ?? null));
  const image = (imagesQuery.data ?? []).find((img) => img.runtime_type === instance?.runtime_type);
  // 入职时选的运行时（employee.runtime_type）与实际实例可能不一致：
  // v0.2 的全局 mock 兜底会把 hermes 入职的人塞成 mock 实例。这里如实告知并给一键对齐。
  const preferredRuntime = employeeQuery.data?.runtime_type ?? null;
  const runtimeMismatch =
    instance != null && preferredRuntime != null && preferredRuntime !== instance.runtime_type;

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
      <div className="space-y-5">
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
        <EmployeeProvidersSection employeeId={employeeId} createSignal={createSignal} />
      </div>
    );
  }

  return (
    <div className="space-y-5">
      {runtimeMismatch ? (
        <div
          data-testid="runtime-mismatch"
          className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-500/35 bg-amber-500/10 px-3 py-2.5"
        >
          <p className="text-xs leading-5 text-amber-700 dark:text-amber-400">
            {t("runtime:tab.mismatch", {
              preferred: enumLabel(t, "runtime:type", preferredRuntime),
              current: enumLabel(t, "runtime:type", instance.runtime_type),
            })}
          </p>
          <Button
            size="sm"
            variant="outline"
            data-testid="runtime-mismatch-align"
            onClick={() => {
              setSwitchType(preferredRuntime);
              setSwitchOpen(true);
            }}
          >
            {t("runtime:tab.mismatchAction", {
              preferred: enumLabel(t, "runtime:type", preferredRuntime),
            })}
          </Button>
        </div>
      ) : null}
      {/* 教程在这里打光：Provider/Model 的换绑入口就在这张卡上 */}
      <div data-tutorial-target="employee-provider-bind">
        <RuntimeCard
          instance={instance}
          image={image ?? null}
          busy={action.isPending}
          checkingUpdates={checkUpdates.isPending}
          onStop={() => action.mutate({ id: instance.id, op: "stop" })}
          onRestart={() => action.mutate({ id: instance.id, op: "restart" })}
          onViewLogs={() => setLogsOpen(true)}
          onChangeProvider={openChangeProvider}
          onChangeRuntime={() => {
            setSwitchType(null);
            setSwitchOpen(true);
          }}
          onCheckUpdate={() => checkUpdates.mutate()}
        />
      </div>

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

      <EmployeeProvidersSection employeeId={employeeId} createSignal={createSignal} />

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
          {/* Mock 的 supported_providers 是空列表：这里必须说清楚，
              并给一条能走通的路（新建服务商），否则就是一个没有出口的弹窗。 */}
          {typeInfo && typeInfo.supported_providers.length === 0 ? (
            <p
              data-testid="no-supported-providers"
              className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-[11px] leading-5 text-amber-700 dark:text-amber-400"
            >
              {t("runtime:changeProvider.noSupportedProviders")}
            </p>
          ) : null}
          <ProviderModelSelector providerId={providerId} value={model} onChange={setModel} />
          {changeProvider.isError ? (
            <p className="text-xs text-red-600 dark:text-red-400">{changeProvider.error.message}</p>
          ) : null}
          <div className="flex items-center justify-between gap-2">
            <Button
              variant="ghost"
              size="sm"
              data-testid="change-provider-create"
              onClick={() => {
                setProviderOpen(false);
                setCreateSignal((n) => n + 1);
              }}
            >
              <Plus className="h-3.5 w-3.5" />
              {t("provider:employeeSection.add")}
            </Button>
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
        </div>
      </Dialog>

      <RuntimeCreateWizard
        open={wizardOpen}
        onOpenChange={setWizardOpen}
        employeeId={employeeId}
        runtimeTypes={typesQuery.data ?? []}
        providers={providersQuery.data ?? []}
      />

      <RuntimeCreateWizard
        open={switchOpen}
        onOpenChange={setSwitchOpen}
        employeeId={employeeId}
        runtimeTypes={typesQuery.data ?? []}
        providers={providersQuery.data ?? []}
        mode="change"
        initial={{ runtimeType: switchType ?? instance.runtime_type, model: instance.model ?? "" }}
      />
    </div>
  );
}
