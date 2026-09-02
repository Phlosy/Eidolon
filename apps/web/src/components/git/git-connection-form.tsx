import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { useCreateGitConnection } from "../../hooks/useGit";
import { Button } from "../common/button";
import { Dialog } from "../common/dialog";
import { Input } from "../common/input";
import { enumLabel } from "../../utils/labels";
import type { GitPlatformType } from "../../types";

const GIT_PLATFORM_TYPES: GitPlatformType[] = ["gitlab", "gitea", "github", "custom"];

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

interface GitConnectionFormProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * Add Connection dialog. Creates an external connection only — the copy makes
 * clear Eidolon never installs external platforms. Token is write-only.
 */
export function GitConnectionForm({ open, onOpenChange }: GitConnectionFormProps) {
  const { t } = useTranslation();
  const create = useCreateGitConnection();

  const [name, setName] = useState("");
  const [platformType, setPlatformType] = useState<GitPlatformType>("gitlab");
  const [baseUrl, setBaseUrl] = useState("");
  const [token, setToken] = useState("");
  const [enabled, setEnabled] = useState(true);
  const [validationError, setValidationError] = useState(false);

  const resetForm = () => {
    setName("");
    setPlatformType("gitlab");
    setBaseUrl("");
    setToken("");
    setEnabled(true);
    setValidationError(false);
  };

  const valid = name.trim() !== "" && baseUrl.trim() !== "";

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!valid) {
      setValidationError(true);
      return;
    }
    create.mutate(
      {
        name: name.trim(),
        platform_type: platformType,
        base_url: baseUrl.trim(),
        ...(token ? { token } : {}),
        enabled,
      },
      {
        onSuccess: () => {
          onOpenChange(false);
          resetForm();
        },
      },
    );
  };

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={t("git:form.addTitle")}
      description={t("git:form.addDescription")}
    >
      <form onSubmit={submit} className="space-y-3">
        <Field label={t("git:form.name")}>
          <Input
            required
            placeholder={t("git:form.namePlaceholder")}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </Field>
        <Field label={t("git:form.platform")}>
          <select
            className={selectClass}
            value={platformType}
            onChange={(e) => setPlatformType(e.target.value as GitPlatformType)}
          >
            {GIT_PLATFORM_TYPES.map((type) => (
              <option key={type} value={type}>
                {enumLabel(t, "git:platform", type)}
              </option>
            ))}
          </select>
        </Field>
        <Field label={t("git:form.baseUrl")}>
          <Input
            required
            placeholder={t(`git:form.placeholders.${platformType}`)}
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
          />
        </Field>
        <Field label={t("git:form.token")}>
          <Input
            type="password"
            autoComplete="new-password"
            value={token}
            onChange={(e) => setToken(e.target.value)}
          />
          <span className="mt-1 block text-[11px] text-muted-foreground">
            {t("git:form.tokenHint")}
          </span>
        </Field>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
            className="h-4 w-4 rounded border-border"
          />
          {t("git:form.enabled")}
        </label>
        {validationError && !valid ? (
          <p className="text-xs text-red-600 dark:text-red-400" role="alert">
            {t("git:form.required")}
          </p>
        ) : null}
        {create.isError ? (
          <p className="text-xs text-red-600 dark:text-red-400" role="alert">
            {create.error.message}
          </p>
        ) : null}
        <div className="flex justify-end gap-2 pt-1">
          <Button type="button" variant="outline" size="sm" onClick={() => onOpenChange(false)}>
            {t("common:cancel")}
          </Button>
          <Button type="submit" size="sm" disabled={create.isPending || !valid}>
            {t("git:form.submit")}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
