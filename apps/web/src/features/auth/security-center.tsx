import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Fingerprint,
  Laptop,
  LogOut,
  Pencil,
  Plus,
  ShieldCheck,
  Smartphone,
  Trash2,
} from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import {
  getPasskeys,
  getSessions,
  logoutAll,
  removePasskey,
  renamePasskey,
  revokeSession,
} from "../../api/auth";
import { Button } from "../../components/common/button";
import { Input } from "../../components/common/input";
import { useAuth } from "./auth-context";
import { passkeysAvailable, registerPasskey } from "./webauthn";

export function SecurityCenter() {
  const { t } = useTranslation("auth");
  const passkeys = useQuery({ queryKey: ["auth", "passkeys"], queryFn: getPasskeys });
  const sessions = useQuery({ queryKey: ["auth", "sessions"], queryFn: getSessions });
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const { auth, setAuth } = useAuth();
  const [name, setName] = useState("");
  const [message, setMessage] = useState("");
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["auth", "passkeys"] });
    void queryClient.invalidateQueries({ queryKey: ["auth", "sessions"] });
  };
  const add = useMutation({
    mutationFn: () => registerPasskey(name || "My Passkey"),
    onSuccess: () => {
      setName("");
      setMessage(t("security.added"));
      refresh();
    },
    onError: (error) => setMessage(error.message),
  });
  const endAll = async () => {
    if (!window.confirm(t("security.logoutAllConfirm"))) return;
    await logoutAll();
    setAuth(null);
    navigate("/auth/login", { replace: true });
  };

  return (
    <section id="security" className="command-panel p-5 md:p-6">
      <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
        <div>
          <p className="type-kicker text-primary">{t("security.kicker")}</p>
          <h2 className="mt-2 flex items-center gap-2 text-xl font-semibold">
            <ShieldCheck className="h-5 w-5 text-primary" />
            {t("security.title")}
          </h2>
          <p className="mt-2 max-w-2xl text-xs leading-5 text-muted-foreground">
            {t("security.description", { email: auth?.user.email })}
          </p>
        </div>
        <span className="rounded-xl border border-success/25 bg-success/8 px-3 py-2 text-[10px] font-medium uppercase tracking-[.12em] text-success">
          {auth?.membership.role} · {t("security.verified")}
        </span>
      </div>

      <div className="mt-6 grid gap-5 xl:grid-cols-2">
        <div className="rounded-2xl border border-border bg-background/35 p-4">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-sm font-semibold">{t("security.passkeys")}</h3>
              <p className="mt-1 text-xs text-muted-foreground">{t("security.passkeyHelp")}</p>
            </div>
            <Fingerprint className="h-5 w-5 text-primary" />
          </div>
          <div className="mt-4 flex gap-2">
            <Input
              aria-label={t("security.passkeyName")}
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder={t("security.passkeyPlaceholder")}
            />
            <Button
              size="sm"
              className="h-10 shrink-0"
              disabled={!passkeysAvailable() || add.isPending}
              onClick={() => add.mutate()}
            >
              <Plus className="h-4 w-4" />
              {t("security.add")}
            </Button>
          </div>
          <div className="mt-4 space-y-2">
            {passkeys.data?.length ? (
              passkeys.data.map((passkey) => (
                <PasskeyRow key={passkey.id} passkey={passkey} onChanged={refresh} />
              ))
            ) : (
              <Empty line={t("security.none")} />
            )}
          </div>
        </div>

        <div className="rounded-2xl border border-border bg-background/35 p-4">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-sm font-semibold">{t("security.sessions")}</h3>
              <p className="mt-1 text-xs text-muted-foreground">{t("security.sessionHelp")}</p>
            </div>
            <Laptop className="h-5 w-5 text-primary" />
          </div>
          <div className="mt-4 space-y-2">
            {sessions.data?.map((session) => (
              <div
                key={session.id}
                className="flex items-center gap-3 rounded-xl border border-border bg-surface/70 p-3"
              >
                <Smartphone className="h-4 w-4 shrink-0 text-muted-foreground" />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-xs font-medium">
                    {session.user_agent || t("security.unknownBrowser")}
                  </p>
                  <p className="mt-1 text-[10px] text-muted-foreground">
                    {session.ip_address || t("security.localDevice")} ·{" "}
                    {new Date(session.last_seen_at).toLocaleString()}
                  </p>
                </div>
                {session.current ? (
                  <span className="text-[10px] font-medium text-success">
                    {t("security.current")}
                  </span>
                ) : (
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label={t("security.revoke")}
                    onClick={async () => {
                      if (!window.confirm(t("security.revokeConfirm"))) return;
                      await revokeSession(session.id);
                      refresh();
                    }}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                )}
              </div>
            ))}
          </div>
          <Button className="mt-4 w-full" variant="outline" onClick={endAll}>
            <LogOut className="h-4 w-4" />
            {t("security.logoutAll")}
          </Button>
        </div>
      </div>
      {message ? (
        <p className="mt-4 text-xs text-muted-foreground" role="status">
          {message}
        </p>
      ) : null}
    </section>
  );
}

function PasskeyRow({
  passkey,
  onChanged,
}: {
  passkey: import("../../types").Passkey;
  onChanged: () => void;
}) {
  const { t } = useTranslation("auth");
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(passkey.name);
  return (
    <div className="flex items-center gap-3 rounded-xl border border-border bg-surface/70 p-3">
      <Fingerprint className="h-4 w-4 shrink-0 text-primary" />
      <div className="min-w-0 flex-1">
        {editing ? (
          <Input
            aria-label={t("security.rename")}
            className="h-8"
            value={value}
            onChange={(event) => setValue(event.target.value)}
            onKeyDown={async (event) => {
              if (event.key === "Enter") {
                await renamePasskey(passkey.id, value);
                setEditing(false);
                onChanged();
              }
            }}
          />
        ) : (
          <>
            <p className="truncate text-xs font-medium">{passkey.name}</p>
            <p className="mt-1 text-[10px] text-muted-foreground">
              {passkey.backed_up ? t("security.synced") : t("security.deviceBound")} ·{" "}
              {passkey.last_used_at
                ? t("security.used", {
                    date: new Date(passkey.last_used_at).toLocaleDateString(),
                  })
                : t("security.unused")}
            </p>
          </>
        )}{" "}
      </div>
      <Button
        variant="ghost"
        size="icon"
        aria-label={t("security.rename")}
        onClick={() => setEditing((open) => !open)}
      >
        <Pencil className="h-3.5 w-3.5" />
      </Button>
      <Button
        variant="ghost"
        size="icon"
        aria-label={t("security.remove")}
        onClick={async () => {
          if (!window.confirm(t("security.removeConfirm"))) return;
          await removePasskey(passkey.id);
          onChanged();
        }}
      >
        <Trash2 className="h-3.5 w-3.5 text-danger" />
      </Button>
    </div>
  );
}

function Empty({ line }: { line: string }) {
  return (
    <p className="rounded-xl border border-dashed border-border px-3 py-5 text-center text-xs text-muted-foreground">
      {line}
    </p>
  );
}
