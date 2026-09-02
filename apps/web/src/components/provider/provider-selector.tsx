import { useTranslation } from "react-i18next";
import type { Provider, ProviderType } from "../../types";
import { enumLabel } from "../../utils/labels";

const selectClass =
  "w-full rounded-md border border-border bg-transparent px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50";

interface ProviderSelectorProps {
  providers: Provider[];
  value: number | null;
  onChange: (providerId: number | null) => void;
  /** Restrict choices to providers of these types (e.g. what the runtime supports). */
  supportedTypes?: ProviderType[];
  disabled?: boolean;
  id?: string;
}

/** Native select over enabled providers, with the scope marked per option. Rendering is presentational; fetching is the caller's job. */
export function ProviderSelector({
  providers,
  value,
  onChange,
  supportedTypes,
  disabled,
  id,
}: ProviderSelectorProps) {
  const { t } = useTranslation();
  const choices = providers.filter(
    (p) => p.enabled && (!supportedTypes || supportedTypes.includes(p.provider_type)),
  );
  return (
    <select
      id={id}
      data-testid="provider-selector"
      className={selectClass}
      value={value ?? ""}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))}
    >
      <option value="">{t("provider:selector.selectProvider")}</option>
      {choices.map((p) => (
        <option key={p.id} value={p.id}>
          {p.name} ({enumLabel(t, "provider:type", p.provider_type)} ·{" "}
          {t(`provider:scope.${p.scope}`)})
        </option>
      ))}
    </select>
  );
}
