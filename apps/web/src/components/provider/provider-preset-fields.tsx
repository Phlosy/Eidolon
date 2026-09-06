import { useRef } from "react";
import { useTranslation } from "react-i18next";
import { useProbeProviderConfig, useProviderPresets } from "../../hooks/useProviders";
import { Input } from "../common/input";
import { enumLabel } from "../../utils/labels";
import { PROVIDER_TYPES } from "./constants";
import { ModelEntriesEditor } from "./model-entries-editor";
import type { ModelEntry, ProviderPreset, ProviderType } from "../../types";

/**
 * 厂商预设表单字段：选厂商 → base_url 与推荐模型由预设带出，用户只需填 key。
 *
 * 数据源是 GET /providers/presets（后端单一事实来源）；预设未加载时退化为
 * 静态类型列表 + 手填。base_url 只作占位提示，留空时后端自动用预设默认值。
 * 员工页"添加服务商"弹窗与招聘向导的"新服务商"模式共用这一组字段。
 */

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

export interface ProviderPresetValue {
  name: string;
  providerType: ProviderType;
  baseUrl: string;
  apiKey: string;
  /** single 模式用 */
  model: string;
  /** multi 模式用：条目列表 + 默认条目（primaryModel 是条目的真实模型名） */
  entries: ModelEntry[];
  primaryModel: string;
}

interface ProviderPresetFieldsProps {
  value: ProviderPresetValue;
  onChange: (patch: Partial<ProviderPresetValue>) => void;
  /** single：向导快速路径单模型；multi：独立配置多选 + 默认；none：不渲染模型字段 */
  modelMode?: "single" | "multi" | "none";
}

export function ProviderPresetFields({
  value,
  onChange,
  modelMode = "single",
}: ProviderPresetFieldsProps) {
  const { t } = useTranslation();
  const presetsQuery = useProviderPresets();
  const presets = presetsQuery.data;
  const probeMutation = useProbeProviderConfig();
  const lastAutoName = useRef("");

  const options: ProviderType[] = presets?.map((p) => p.provider_type) ?? PROVIDER_TYPES;
  const preset: ProviderPreset | undefined = presets?.find(
    (p) => p.provider_type === value.providerType,
  );

  const selectType = (type: ProviderType) => {
    const patch: Partial<ProviderPresetValue> = { providerType: type };
    // 名称没被用户改过（空、或还是上一个自动填的厂商名）→ 跟随厂商走
    const label = enumLabel(t, "provider:type", type);
    if (!value.name || value.name === lastAutoName.current) {
      patch.name = label;
      lastAutoName.current = label;
    }
    onChange(patch);
  };

  const keyMode = !preset
    ? "optional"
    : preset.requires_api_key
      ? "required"
      : value.providerType === "custom"
        ? "optional"
        : "none";

  return (
    <>
      <Field label={t("provider:form.providerType")}>
        <select
          className={selectClass}
          value={value.providerType}
          onChange={(e) => selectType(e.target.value as ProviderType)}
        >
          {options.map((type) => (
            <option key={type} value={type}>
              {enumLabel(t, "provider:type", type)}
            </option>
          ))}
        </select>
      </Field>
      <Field label={t("provider:form.name")}>
        <Input required value={value.name} onChange={(e) => onChange({ name: e.target.value })} />
      </Field>
      <Field
        label={
          value.providerType === "custom"
            ? t("provider:form.baseUrl")
            : t("provider:form.baseUrlOptional")
        }
      >
        <Input
          placeholder={preset?.default_base_url ?? t("provider:form.baseUrlPlaceholder")}
          value={value.baseUrl}
          onChange={(e) => onChange({ baseUrl: e.target.value })}
        />
      </Field>
      {keyMode === "none" ? (
        <p className="rounded-md bg-muted/60 p-3 text-xs text-muted-foreground">
          {t("provider:form.noKeyNeeded")}
        </p>
      ) : (
        <Field
          label={
            keyMode === "required" ? t("provider:form.apiKey") : t("provider:form.apiKeyOptional")
          }
        >
          <Input
            type="password"
            autoComplete="new-password"
            required={keyMode === "required"}
            value={value.apiKey}
            onChange={(e) => onChange({ apiKey: e.target.value })}
          />
        </Field>
      )}
      {modelMode === "single" ? (
        <Field label={t("provider:form.modelOptional")}>
          <Input
            placeholder={t("provider:modelSelector.placeholder")}
            value={value.model}
            onChange={(e) => onChange({ model: e.target.value })}
          />
        </Field>
      ) : null}
      {modelMode === "multi" ? (
        <ModelEntriesEditor
          value={{ entries: value.entries, primaryModel: value.primaryModel }}
          onChange={onChange}
          docsUrl={preset?.docs_url}
          // 未保存配置的探测：带上表单里当前填的厂商/base_url/key
          probe={() =>
            probeMutation.mutateAsync({
              provider_type: value.providerType,
              ...(value.baseUrl.trim() ? { base_url: value.baseUrl.trim() } : {}),
              ...(value.apiKey ? { api_key: value.apiKey } : {}),
            })
          }
          // 免 key 的厂商进来就自动探；要 key 的等 key 填上再探
          autoProbeSignature={
            preset && (!preset.requires_api_key || value.apiKey)
              ? `${value.providerType}|${value.baseUrl.trim()}|${value.apiKey}`
              : null
          }
        />
      ) : null}
    </>
  );
}
