import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { useEmployees } from "../../hooks/useEmployees";
import { Button } from "../common/button";
import { Input } from "../common/input";
import { PROVIDER_TYPES } from "./constants";
import { enumLabel } from "../../utils/labels";
import type { CreateProviderInput, Provider, ProviderScope, ProviderType } from "../../types";

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

interface ProviderFormProps {
  /** When set, the form edits this provider; otherwise it creates a new one. */
  initial?: Provider;
  submitting: boolean;
  onSubmit: (input: CreateProviderInput) => void;
  onCancel: () => void;
}

/**
 * Create/edit provider form. The API key field is write-only: it is never
 * populated from the provider, and when editing an empty value means
 * "leave empty to keep" the stored credential.
 */
export function ProviderForm({ initial, submitting, onSubmit, onCancel }: ProviderFormProps) {
  const { t } = useTranslation();
  const employeesQuery = useEmployees();
  const [name, setName] = useState(initial?.name ?? "");
  const [providerType, setProviderType] = useState<ProviderType>(
    initial?.provider_type ?? "openai",
  );
  const [baseUrl, setBaseUrl] = useState(initial?.base_url ?? "");
  const [scope, setScope] = useState<ProviderScope>(initial?.scope ?? "company");
  const [ownerId, setOwnerId] = useState<number | null>(initial?.owner_employee_id ?? null);
  const [apiKey, setApiKey] = useState("");

  const editing = initial != null;
  const needsBaseUrl = providerType === "ollama" || providerType === "custom";

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    const input: CreateProviderInput = {
      name: name.trim(),
      provider_type: providerType,
      scope,
      ...(baseUrl.trim() ? { base_url: baseUrl.trim() } : {}),
      ...(scope === "employee" && ownerId != null ? { owner_employee_id: ownerId } : {}),
      ...(apiKey ? { api_key: apiKey } : {}),
      ...(initial ? { metadata: initial.metadata } : {}),
    };
    onSubmit(input);
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
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
      <Field label={needsBaseUrl ? t("provider:form.baseUrl") : t("provider:form.baseUrlOptional")}>
        <Input
          required={needsBaseUrl}
          placeholder={t("provider:form.baseUrlPlaceholder")}
          value={baseUrl}
          onChange={(e) => setBaseUrl(e.target.value)}
        />
      </Field>
      <Field label={t("provider:form.scope")}>
        <select
          className={selectClass}
          value={scope}
          onChange={(e) => setScope(e.target.value as ProviderScope)}
        >
          <option value="company">{t("provider:form.scopeCompany")}</option>
          <option value="employee">{t("provider:form.scopeEmployee")}</option>
        </select>
      </Field>
      {scope === "employee" ? (
        <Field label={t("provider:form.ownerEmployee")}>
          <select
            className={selectClass}
            required
            value={ownerId ?? ""}
            onChange={(e) => setOwnerId(e.target.value === "" ? null : Number(e.target.value))}
          >
            <option value="">{t("provider:form.selectEmployee")}</option>
            {(employeesQuery.data ?? []).map((emp) => (
              <option key={emp.id} value={emp.id}>
                {emp.name}
              </option>
            ))}
          </select>
        </Field>
      ) : null}
      <Field label={editing ? t("provider:form.apiKeyKeep") : t("provider:form.apiKey")}>
        <Input
          type="password"
          autoComplete="new-password"
          placeholder={editing && initial.has_credential ? (initial.credential_mask ?? "••••") : ""}
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
        />
      </Field>
      <div className="flex justify-end gap-2 pt-1">
        <Button type="button" variant="outline" size="sm" onClick={onCancel}>
          {t("common:cancel")}
        </Button>
        <Button type="submit" size="sm" disabled={submitting || !name.trim()}>
          {editing ? t("provider:form.saveChanges") : t("provider:form.addProvider")}
        </Button>
      </div>
    </form>
  );
}
