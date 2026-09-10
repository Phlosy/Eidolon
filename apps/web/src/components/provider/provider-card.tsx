import { useState } from "react";
import { useTranslation } from "react-i18next";
import { enumLabel } from "../../utils/labels";
import { Badge } from "../common/badge";
import { Button } from "../common/button";
import { ProviderScopeBadge } from "./provider-scope-badge";
import { ProviderTestButton } from "./provider-test-button";
import type { Provider, ProviderTestResult } from "../../types";

const TYPE_VARIANT: Record<Provider["provider_type"], "default" | "info" | "violet" | "muted"> = {
  openai: "info",
  anthropic: "violet",
  openrouter: "info",
  deepseek: "info",
  moonshot: "violet",
  zhipu: "info",
  qwen: "info",
  groq: "info",
  mistral: "info",
  gemini: "info",
  ollama: "muted",
  custom: "muted",
};

interface ProviderCardProps {
  provider: Provider;
  /** 所属员工页的员工 id：员工级账号的测试/探测需要它作为作用域。 */
  employeeId?: number;
  onEdit?: (provider: Provider) => void;
  onToggleEnabled?: (provider: Provider) => void;
  onDelete?: (provider: Provider) => void;
  onDiscoverModels?: (provider: Provider) => void;
  busy?: boolean;
}

/**
 * Provider summary card. Never renders key material — only the server-side
 * `credential_mask`. Delete is disabled while `in_use_by > 0`.
 */
export function ProviderCard({
  provider,
  employeeId,
  onEdit,
  onToggleEnabled,
  onDelete,
  onDiscoverModels,
  busy,
}: ProviderCardProps) {
  const { t } = useTranslation();
  const [testResult, setTestResult] = useState<ProviderTestResult | null>(null);
  const inUse = provider.in_use_by > 0;

  return (
    <div
      data-testid={`provider-card-${provider.id}`}
      className="rounded-lg border border-border bg-card p-4 shadow-card transition-shadow duration-200 hover:shadow-lift"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <p className="truncate text-sm font-semibold">{provider.name}</p>
            <Badge variant={TYPE_VARIANT[provider.provider_type]} className="normal-case">
              {enumLabel(t, "provider:type", provider.provider_type)}
            </Badge>
            <ProviderScopeBadge scope={provider.scope} />
            {!provider.enabled ? <Badge variant="muted">{t("provider:disabled")}</Badge> : null}
          </div>
          <p className="mt-1 font-mono text-[11px] text-muted-foreground">
            {provider.credential_mask ?? t("provider:noCredential")}
            {provider.base_url ? ` · ${provider.base_url}` : ""}
          </p>
        </div>
        <p className="shrink-0 text-xs text-muted-foreground">
          {t("provider:inUse", { count: provider.in_use_by })}
        </p>
      </div>

      {testResult ? (
        <p
          data-testid="provider-test-result"
          className={
            testResult.ok
              ? "mt-2 text-xs text-emerald-600 dark:text-emerald-400"
              : "mt-2 text-xs text-red-600 dark:text-red-400"
          }
        >
          {testResult.ok
            ? t("provider:connected") +
              (testResult.latency_ms != null
                ? ` ${t("provider:connectedIn", { ms: testResult.latency_ms })}`
                : "") +
              (testResult.models_count != null
                ? ` · ${t("provider:modelsCount", { count: testResult.models_count })}`
                : "")
            : t("provider:testFailed", {
                error: testResult.error ?? t("provider:unknownError"),
              })}
        </p>
      ) : null}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <ProviderTestButton
          providerId={provider.id}
          employeeId={employeeId ?? provider.owner_employee_id ?? undefined}
          onResult={setTestResult}
        />
        {onDiscoverModels ? (
          <Button variant="outline" size="sm" onClick={() => onDiscoverModels(provider)}>
            {t("provider:actions.discoverModels")}
          </Button>
        ) : null}
        {onEdit ? (
          <Button variant="outline" size="sm" onClick={() => onEdit(provider)}>
            {t("provider:actions.edit")}
          </Button>
        ) : null}
        {onToggleEnabled ? (
          <Button
            variant="outline"
            size="sm"
            disabled={busy}
            onClick={() => onToggleEnabled(provider)}
          >
            {provider.enabled ? t("provider:actions.disable") : t("provider:actions.enable")}
          </Button>
        ) : null}
        {onDelete ? (
          <Button
            variant="destructive"
            size="sm"
            disabled={busy || inUse}
            title={inUse ? t("provider:inUseTitle", { count: provider.in_use_by }) : undefined}
            onClick={() => onDelete(provider)}
          >
            {t("provider:actions.delete")}
          </Button>
        ) : null}
      </div>
    </div>
  );
}
