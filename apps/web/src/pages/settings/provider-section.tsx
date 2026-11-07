import { KeyRound } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { companies as companiesApi } from "../../api";
import { ErrorNote, KeyValue, Section } from "../../components/app/widgets";
import { Button } from "../../components/common/button";
import { Input } from "../../components/common/input";
import { Skeleton } from "../../components/common/skeleton";
import { useCurrentCompany } from "../../features/company/company-context";
import { useCommand } from "../../hooks/queries";
import { formatDateTime } from "../../utils/format";

/** The company's own model provider key: written once, shown only by its tail. */
export function ProviderSection() {
  const { t } = useTranslation("app");
  const me = useCurrentCompany();
  const current = useQuery({
    queryKey: ["provider", me.company_id],
    queryFn: () => companiesApi.provider(me.company_id),
  });
  const [kind, setKind] = useState<"openai_compatible" | "anthropic">("openai_compatible");
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const save = useCommand(() =>
    companiesApi.setProvider(me.company_id, {
      kind,
      api_key: apiKey,
      base_url: baseUrl || null,
      model: model || null,
    }),
  );
  const clear = useCommand(() => companiesApi.clearProvider(me.company_id));
  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate(undefined, { onSuccess: () => setApiKey("") });
  };
  if (me.role !== "owner") {
    return (
      <Section title={t("settings.provider")}>
        <p className="text-xs text-muted-foreground">{t("settings.providerOwnerOnly")}</p>
      </Section>
    );
  }
  return (
    <Section title={t("settings.provider")}>
      <p className="text-xs text-muted-foreground">{t("settings.providerHint")}</p>
      {current.isLoading ? <Skeleton className="mt-3 h-16 w-full" /> : null}
      {current.data?.configured ? (
        <div className="mt-3">
          <KeyValue
            items={[
              [t("settings.providerKind"), current.data.kind],
              [t("settings.providerModel"), current.data.model ?? "—"],
              [t("settings.providerBaseUrl"), current.data.base_url ?? "—"],
              [t("settings.providerKey"), `••••${current.data.key_tail}`],
              [
                t("common.at"),
                `${formatDateTime(current.data.updated_at)} · ${current.data.updated_by}`,
              ],
            ]}
          />
          <Button
            className="mt-3"
            size="sm"
            variant="ghost"
            onClick={() => clear.mutate(undefined)}
          >
            {t("settings.providerClear")}
          </Button>
        </div>
      ) : current.data ? (
        <p className="mt-3 text-xs text-muted-foreground">{t("settings.providerNone")}</p>
      ) : null}
      <form onSubmit={submit} className="mt-4 max-w-md space-y-3">
        <select
          aria-label={t("settings.providerKind")}
          className="h-10 w-full rounded-md border border-border bg-transparent px-3 text-sm [&>option]:bg-card"
          value={kind}
          onChange={(e) => setKind(e.target.value as "openai_compatible" | "anthropic")}
        >
          <option value="openai_compatible">
            openai_compatible (DeepSeek, Qwen, vLLM, Ollama…)
          </option>
          <option value="anthropic">anthropic</option>
        </select>
        <Input
          aria-label={t("settings.providerKey")}
          placeholder={t("settings.providerKey")}
          type="password"
          autoComplete="off"
          required
          minLength={8}
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
        />
        <Input
          aria-label={t("settings.providerBaseUrl")}
          placeholder={t("settings.providerBaseUrl")}
          value={baseUrl}
          onChange={(e) => setBaseUrl(e.target.value)}
        />
        <Input
          aria-label={t("settings.providerModel")}
          placeholder={t("settings.providerModel")}
          value={model}
          onChange={(e) => setModel(e.target.value)}
        />
        <ErrorNote error={save.error ?? clear.error} />
        <Button type="submit" disabled={save.isPending}>
          <KeyRound className="h-4 w-4" />
          {t("settings.providerSave")}
        </Button>
      </form>
    </Section>
  );
}
