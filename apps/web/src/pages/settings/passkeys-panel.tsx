import { Fingerprint } from "lucide-react";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { auth as authApi } from "../../api";
import { ErrorNote, Section } from "../../components/app/widgets";
import { Button } from "../../components/common/button";
import { Input } from "../../components/common/input";
import { Skeleton } from "../../components/common/skeleton";
import { passkeysAvailable, registerPasskey } from "../../features/auth/webauthn";
import { useCommand } from "../../hooks/queries";
import { formatDateTime } from "../../utils/format";

export function PasskeysPanel() {
  const { t } = useTranslation("app");
  const passkeys = useQuery({ queryKey: ["passkeys"], queryFn: authApi.passkeys });
  const [name, setName] = useState("");
  const add = useCommand((label: string) => registerPasskey(label));
  const remove = useCommand((id: string) => authApi.removePasskey(id));
  const available = passkeysAvailable();
  return (
    <Section title={t("settings.passkeys")}>
      <p className="text-xs text-muted-foreground">{t("settings.passkeysHint")}</p>
      {passkeys.isLoading ? <Skeleton className="mt-3 h-12 w-full" /> : null}
      <ul className="mt-3 space-y-2 text-xs">
        {passkeys.data?.map((p) => (
          <li
            key={p.credential_id}
            className="flex items-center justify-between gap-2 rounded-lg border border-border px-3 py-2"
          >
            <span className="flex items-center gap-2">
              <Fingerprint className="h-4 w-4 text-primary" />
              <span className="font-medium">{p.name}</span>
              <span className="text-muted-foreground">
                {formatDateTime(p.created_at)}
                {p.last_used_at
                  ? ` · ${t("settings.lastUsed")} ${formatDateTime(p.last_used_at)}`
                  : ""}
              </span>
            </span>
            <Button size="sm" variant="ghost" onClick={() => remove.mutate(p.credential_id)}>
              {t("settings.revoke")}
            </Button>
          </li>
        ))}
        {passkeys.data && passkeys.data.length === 0 ? (
          <li className="text-muted-foreground">{t("settings.noPasskeys")}</li>
        ) : null}
      </ul>
      <form
        className="mt-3 flex gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          add.mutate(name || "Passkey", { onSuccess: () => setName("") });
        }}
      >
        <Input
          aria-label={t("settings.passkeyName")}
          placeholder={t("settings.passkeyName")}
          value={name}
          onChange={(e) => setName(e.target.value)}
          disabled={!available}
        />
        <Button type="submit" disabled={!available || add.isPending}>
          <Fingerprint className="h-4 w-4" />
          {t("settings.addPasskey")}
        </Button>
      </form>
      {!available ? (
        <p className="mt-2 text-[11px] text-warning">{t("settings.passkeysUnavailable")}</p>
      ) : null}
      <ErrorNote error={add.error ?? remove.error} />
    </Section>
  );
}
