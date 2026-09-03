import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useCreateEmployeeRuntime } from "../../hooks/useRuntimes";
import { useCreateEmployeeProvider } from "../../hooks/useProviders";
import { Button } from "../common/button";
import { Dialog } from "../common/dialog";
import { Input } from "../common/input";
import { ProviderSelector } from "../provider/provider-selector";
import { ProviderModelSelector } from "../provider/provider-model-selector";
import { PROVIDER_TYPES } from "../provider/constants";
import { RuntimeCapabilitiesGrid } from "./runtime-capabilities-grid";
import { RuntimeResourceConfig } from "./runtime-resource-config";
import {
  canAdvance,
  INITIAL_WIZARD_STATE,
  WIZARD_STEPS,
  type WizardState,
  type WizardStep,
} from "./wizard-steps";
import { cn } from "../../utils/cn";
import { enumLabel } from "../../utils/labels";
import type {
  CreateEmployeeRuntimeInput,
  Provider,
  ProviderType,
  RuntimeTypeInfo,
} from "../../types";

const STEP_TITLE_KEY: Record<WizardStep, string> = {
  type: "wizard.steps.type",
  deployment: "wizard.steps.deployment",
  provider: "wizard.steps.provider",
  model: "wizard.steps.model",
  resources: "wizard.steps.resources",
  confirm: "wizard.steps.confirm",
};

const selectClass =
  "w-full rounded-md border border-border bg-transparent px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50";

interface RuntimeCreateWizardProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  employeeId: number;
  runtimeTypes: RuntimeTypeInfo[];
  providers: Provider[];
}

/** Multi-step dialog that creates a runtime instance for an employee. */
export function RuntimeCreateWizard({
  open,
  onOpenChange,
  employeeId,
  runtimeTypes,
  providers,
}: RuntimeCreateWizardProps) {
  const { t } = useTranslation();
  const [stepIndex, setStepIndex] = useState(0);
  const [state, setState] = useState<WizardState>(INITIAL_WIZARD_STATE);
  const [creatingProvider, setCreatingProvider] = useState(false);
  const createRuntime = useCreateEmployeeRuntime(employeeId);

  const step = WIZARD_STEPS[stepIndex];
  const selectedType = useMemo(
    () => runtimeTypes.find((t) => t.type === state.runtimeType) ?? null,
    [runtimeTypes, state.runtimeType],
  );
  const dockerBlocked =
    selectedType != null &&
    (!selectedType.docker_available || !selectedType.deployment_modes.includes("docker"));
  const advanceable = canAdvance(step, state, runtimeTypes);

  const close = () => {
    onOpenChange(false);
    setStepIndex(0);
    setState(INITIAL_WIZARD_STATE);
    setCreatingProvider(false);
    createRuntime.reset();
  };

  const submit = () => {
    if (state.providerId == null || state.runtimeType == null) return;
    const input: CreateEmployeeRuntimeInput = {
      runtime_type: state.runtimeType,
      deployment_mode: "docker",
      provider_id: state.providerId,
      model: state.model.trim(),
      cpu_limit: state.cpuLimit,
      memory_limit_mb: state.memoryLimitMb,
    };
    createRuntime.mutate(input, { onSuccess: close });
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => (next ? onOpenChange(true) : close())}
      title={t("runtime:wizard.title")}
      description={t("runtime:wizard.stepOf", {
        current: stepIndex + 1,
        total: WIZARD_STEPS.length,
        step: t(`runtime:${STEP_TITLE_KEY[step]}`),
      })}
      className="max-w-lg"
    >
      {step === "type" ? (
        <div className="space-y-2">
          {runtimeTypes.map((typeInfo) => (
            <button
              key={typeInfo.type}
              type="button"
              aria-pressed={state.runtimeType === typeInfo.type}
              disabled={!typeInfo.implemented}
              onClick={() => setState((s) => ({ ...s, runtimeType: typeInfo.type }))}
              className={cn(
                "w-full rounded-md border border-border px-3 py-2 text-left text-sm transition-colors disabled:opacity-50",
                state.runtimeType === typeInfo.type
                  ? "border-foreground/40 bg-muted"
                  : "hover:bg-muted/50",
              )}
            >
              <span className="font-medium">{enumLabel(t, "runtime:type", typeInfo.type)}</span>
              {!typeInfo.implemented ? (
                <span className="ml-2 text-xs text-muted-foreground">
                  {t("runtime:wizard.notImplemented")}
                </span>
              ) : null}
            </button>
          ))}
          {selectedType ? (
            <div className="pt-2">
              <RuntimeCapabilitiesGrid capabilities={selectedType.capabilities} />
            </div>
          ) : null}
        </div>
      ) : null}

      {step === "deployment" ? (
        <div className="space-y-2">
          <p className="text-sm text-muted-foreground">
            {t("runtime:wizard.deploymentMode")} <span className="font-mono text-xs">docker</span>
          </p>
          {dockerBlocked ? (
            <p
              data-testid="docker-unavailable"
              className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-600 dark:text-amber-400"
            >
              {t("runtime:wizard.dockerUnavailable")}
            </p>
          ) : null}
        </div>
      ) : null}

      {step === "provider" ? (
        <div className="space-y-3">
          <ProviderSelector
            providers={providers}
            value={state.providerId}
            supportedTypes={selectedType?.supported_providers}
            onChange={(id) => setState((s) => ({ ...s, providerId: id }))}
          />
          {creatingProvider ? (
            <InlineProviderCreate
              employeeId={employeeId}
              supportedTypes={selectedType?.supported_providers}
              onCancel={() => setCreatingProvider(false)}
              onCreated={(id) => {
                setState((s) => ({ ...s, providerId: id }));
                setCreatingProvider(false);
              }}
            />
          ) : (
            <Button variant="ghost" size="sm" onClick={() => setCreatingProvider(true)}>
              {t("runtime:wizard.createNewProvider")}
            </Button>
          )}
        </div>
      ) : null}

      {step === "model" ? (
        <ProviderModelSelector
          providerId={state.providerId}
          value={state.model}
          onChange={(model) => setState((s) => ({ ...s, model }))}
        />
      ) : null}

      {step === "resources" ? (
        <RuntimeResourceConfig
          cpuLimit={state.cpuLimit}
          memoryLimitMb={state.memoryLimitMb}
          onChange={({ cpuLimit, memoryLimitMb }) =>
            setState((s) => ({ ...s, cpuLimit, memoryLimitMb }))
          }
        />
      ) : null}

      {step === "confirm" ? (
        <dl className="space-y-1.5 text-sm">
          <SummaryRow
            label={t("runtime:wizard.summary.runtime")}
            value={state.runtimeType ? enumLabel(t, "runtime:type", state.runtimeType) : "—"}
          />
          <SummaryRow
            label={t("runtime:wizard.summary.deployment")}
            value={state.deploymentMode}
            mono
          />
          <SummaryRow
            label={t("runtime:wizard.summary.provider")}
            value={providers.find((p) => p.id === state.providerId)?.name ?? "—"}
          />
          <SummaryRow label={t("runtime:wizard.summary.model")} value={state.model || "—"} mono />
          <SummaryRow
            label={t("runtime:wizard.summary.cpuLimit")}
            value={t("runtime:wizard.cpuValue", { count: state.cpuLimit })}
            mono
          />
          <SummaryRow
            label={t("runtime:wizard.summary.memoryLimit")}
            value={t("runtime:wizard.memoryValue", { count: state.memoryLimitMb })}
            mono
          />
        </dl>
      ) : null}

      {createRuntime.isError ? (
        <p className="mt-3 text-xs text-red-600 dark:text-red-400">{createRuntime.error.message}</p>
      ) : null}

      <div className="mt-5 flex justify-between">
        <Button
          variant="outline"
          size="sm"
          disabled={stepIndex === 0 || createRuntime.isPending}
          onClick={() => setStepIndex((i) => Math.max(0, i - 1))}
        >
          {t("common:back")}
        </Button>
        {step === "confirm" ? (
          <Button
            size="sm"
            disabled={createRuntime.isPending}
            onClick={submit}
            data-testid="wizard-create"
          >
            {createRuntime.isPending ? t("runtime:wizard.creating") : t("runtime:wizard.create")}
          </Button>
        ) : (
          <Button
            size="sm"
            disabled={!advanceable}
            data-testid="wizard-next"
            onClick={() => setStepIndex((i) => Math.min(WIZARD_STEPS.length - 1, i + 1))}
          >
            {t("common:next")}
          </Button>
        )}
      </div>
    </Dialog>
  );
}

function SummaryRow({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex justify-between gap-4">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className={mono ? "font-mono text-xs" : ""}>{value}</dd>
    </div>
  );
}

/** Minimal inline create-new-provider form for the wizard's provider step. */
function InlineProviderCreate({
  employeeId,
  supportedTypes,
  onCreated,
  onCancel,
}: {
  employeeId: number;
  supportedTypes?: ProviderType[];
  onCreated: (id: number) => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  // v0.3: provider accounts belong to the employee (POST /employees/{id}/providers).
  const createProvider = useCreateEmployeeProvider(employeeId);
  const busy = createProvider.isPending;
  const [name, setName] = useState("");
  const [providerType, setProviderType] = useState<ProviderType>(supportedTypes?.[0] ?? "openai");
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("");

  const types = supportedTypes && supportedTypes.length > 0 ? supportedTypes : PROVIDER_TYPES;

  const submit = () => {
    createProvider.mutate(
      {
        name: name.trim(),
        provider_type: providerType,
        ...(apiKey ? { api_key: apiKey } : {}),
        ...(baseUrl.trim() ? { base_url: baseUrl.trim() } : {}),
      },
      { onSuccess: (provider) => onCreated(provider.id) },
    );
  };

  return (
    <div className="space-y-2 rounded-md border border-border p-3">
      <Input
        placeholder={t("runtime:wizard.providerNamePlaceholder")}
        value={name}
        onChange={(e) => setName(e.target.value)}
      />
      <select
        className={selectClass}
        value={providerType}
        onChange={(e) => setProviderType(e.target.value as ProviderType)}
      >
        {types.map((type) => (
          <option key={type} value={type}>
            {enumLabel(t, "provider:type", type)}
          </option>
        ))}
      </select>
      <Input
        type="password"
        autoComplete="new-password"
        placeholder={t("runtime:wizard.apiKeyPlaceholder")}
        value={apiKey}
        onChange={(e) => setApiKey(e.target.value)}
      />
      <Input
        placeholder={t("runtime:wizard.baseUrlPlaceholder")}
        value={baseUrl}
        onChange={(e) => setBaseUrl(e.target.value)}
      />
      <div className="flex justify-end gap-2">
        <Button variant="ghost" size="sm" onClick={onCancel}>
          {t("common:cancel")}
        </Button>
        <Button size="sm" disabled={busy || !name.trim()} onClick={submit}>
          {busy ? t("runtime:wizard.saving") : t("runtime:wizard.saveProvider")}
        </Button>
      </div>
    </div>
  );
}
