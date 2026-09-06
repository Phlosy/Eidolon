import { useEffect, useState } from "react";
import { Loader2, RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useProviderModels } from "../../hooks/useProviders";
import { Button } from "../common/button";
import { Input } from "../common/input";

const selectClass =
  "w-full rounded-md border border-border bg-transparent px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50";

interface ProviderModelSelectorProps {
  providerId: number | null;
  value: string;
  onChange: (model: string) => void;
  disabled?: boolean;
}

/**
 * Model picker: manual input by default, with a "Discover" button that calls
 * GET /providers/{id}/models. When discovery succeeds the models are offered
 * as a select; when the source is "manual" the plain input stays.
 */
export function ProviderModelSelector({
  providerId,
  value,
  onChange,
  disabled,
}: ProviderModelSelectorProps) {
  const { t } = useTranslation();
  const [discovering, setDiscovering] = useState(false);
  // 选定 provider 就自动探测真实模型清单，不用用户再找"发现"按钮
  useEffect(() => {
    if (providerId != null) setDiscovering(true);
  }, [providerId]);
  const modelsQuery = useProviderModels(providerId, discovering);
  const discovered = modelsQuery.data?.models ?? [];

  return (
    <div className="space-y-2">
      <div className="flex gap-2">
        <Input
          data-testid="model-input"
          placeholder={t("provider:modelSelector.placeholder")}
          value={value}
          disabled={disabled || providerId == null}
          onChange={(e) => onChange(e.target.value)}
        />
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="shrink-0 self-center"
          disabled={disabled || providerId == null || modelsQuery.isFetching}
          onClick={() => setDiscovering(true)}
        >
          {modelsQuery.isFetching ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <RefreshCw className="h-3.5 w-3.5" />
          )}
          {t("provider:modelSelector.discover")}
        </Button>
      </div>
      {providerId == null ? (
        <p className="text-xs text-muted-foreground">
          {t("provider:modelSelector.selectProviderFirst")}
        </p>
      ) : null}
      {modelsQuery.isError ? (
        <p className="text-xs text-red-600 dark:text-red-400">
          {t("provider:modelSelector.discoveryFailedManual")}
        </p>
      ) : null}
      {discovered.length > 0 ? (
        <select
          data-testid="model-select"
          className={selectClass}
          value={discovered.includes(value) ? value : ""}
          onChange={(e) => onChange(e.target.value)}
        >
          <option value="">{t("provider:modelSelector.pickDiscovered")}</option>
          {discovered.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
      ) : null}
    </div>
  );
}
