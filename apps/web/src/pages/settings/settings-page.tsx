import { Settings } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { NavLink, useParams } from "react-router-dom";
import { auth as authApi, companies as companiesApi } from "../../api";
import { ErrorNote, KeyValue, Section } from "../../components/app/widgets";
import { Button } from "../../components/common/button";
import { Input, Textarea } from "../../components/common/input";
import { PageHeader } from "../../components/common/states";
import { Skeleton } from "../../components/common/skeleton";
import { useAuth } from "../../features/auth/auth-context";
import { useCurrentCompany } from "../../features/company/company-context";
import { useCommand, useInstitution } from "../../hooks/queries";
import { cn } from "../../utils/cn";
import { formatDateTime } from "../../utils/format";
import { PasskeysPanel } from "./passkeys-panel";
import { ProviderSection } from "./provider-section";

const SECTIONS = ["profile", "security", "institution", "provider", "about"] as const;
type SectionKey = (typeof SECTIONS)[number];

export function SettingsPage() {
  const { t } = useTranslation("app");
  const { section = "profile" } = useParams();
  const active = (SECTIONS as readonly string[]).includes(section)
    ? (section as SectionKey)
    : "profile";
  return (
    <>
      <PageHeader title={t("settings.title")} icon={Settings} />
      <div className="grid gap-6 md:grid-cols-[200px_1fr]">
        <nav className="flex gap-1 md:flex-col">
          {SECTIONS.map((key) => (
            <NavLink
              key={key}
              to={`/settings/${key}`}
              className={({ isActive }) =>
                cn(
                  "rounded-xl px-3 py-2 text-sm",
                  isActive || (key === "profile" && section === "profile")
                    ? "bg-primary/10 text-primary"
                    : "text-muted-foreground hover:bg-muted",
                )
              }
            >
              {t(`settings.${key}`)}
            </NavLink>
          ))}
        </nav>
        <div>
          {active === "profile" ? <ProfileSection /> : null}
          {active === "security" ? <SecuritySection /> : null}
          {active === "institution" ? <InstitutionSection /> : null}
          {active === "provider" ? <ProviderSection /> : null}
          {active === "about" ? <AboutSection /> : null}
        </div>
      </div>
    </>
  );
}

function ProfileSection() {
  const { t } = useTranslation("app");
  const { me } = useAuth();
  const company = useCurrentCompany();
  return (
    <Section title={t("settings.profile")}>
      <KeyValue
        items={[
          ["Email", me?.email],
          ["Name", me?.display_name],
          ["Persons", me?.person_ids.join(", ")],
          [t("employees.role"), `${company.person_id} · ${company.role}`],
        ]}
      />
    </Section>
  );
}

function SecuritySection() {
  const { t } = useTranslation("app");
  const sessions = useQuery({ queryKey: ["sessions"], queryFn: authApi.sessions });
  const revoke = useCommand((id: string) => authApi.revokeSession(id));
  const [oldPassword, setOld] = useState("");
  const [newPassword, setNew] = useState("");
  const change = useCommand(() => authApi.changePassword(oldPassword, newPassword));
  const submit = (event: FormEvent) => {
    event.preventDefault();
    change.mutate(undefined, {
      onSuccess: () => {
        setOld("");
        setNew("");
      },
    });
  };
  return (
    <div className="space-y-6">
      <Section title={t("settings.sessions")}>
        {sessions.isLoading ? <Skeleton className="h-16 w-full" /> : null}
        <ul className="space-y-2 text-xs">
          {sessions.data?.map((s) => (
            <li
              key={s.session_id}
              className="flex items-center justify-between gap-2 rounded-lg border border-border px-3 py-2"
            >
              <span>
                {s.current ? (
                  <span className="mr-2 rounded-full bg-primary/10 px-2 py-0.5 text-[10px] text-primary">
                    {t("settings.current")}
                  </span>
                ) : null}
                {formatDateTime(s.last_seen_at)} ·{" "}
                <span className="text-muted-foreground">{s.user_agent.slice(0, 60)}</span>
              </span>
              {!s.current ? (
                <Button size="sm" variant="ghost" onClick={() => revoke.mutate(s.session_id)}>
                  {t("settings.revoke")}
                </Button>
              ) : null}
            </li>
          ))}
        </ul>
      </Section>
      <PasskeysPanel />
      <Section title={t("settings.changePassword")}>
        <form onSubmit={submit} className="max-w-sm space-y-3">
          <Input
            aria-label={t("settings.oldPassword")}
            placeholder={t("settings.oldPassword")}
            type="password"
            autoComplete="current-password"
            required
            value={oldPassword}
            onChange={(e) => setOld(e.target.value)}
          />
          <Input
            aria-label={t("settings.newPassword")}
            placeholder={t("settings.newPassword")}
            type="password"
            autoComplete="new-password"
            minLength={8}
            required
            value={newPassword}
            onChange={(e) => setNew(e.target.value)}
          />
          <ErrorNote error={change.error} />
          {change.isSuccess ? (
            <p className="text-xs text-success">{t("settings.passwordChanged")}</p>
          ) : null}
          <Button type="submit" disabled={change.isPending}>
            {t("settings.changePassword")}
          </Button>
        </form>
      </Section>
    </div>
  );
}

function InstitutionSection() {
  const { t } = useTranslation("app");
  const me = useCurrentCompany();
  const institution = useInstitution(me.company_id);
  const [changes, setChanges] = useState("");
  const [reason, setReason] = useState("");
  const [parseError, setParseError] = useState<string | null>(null);
  const customize = useCommand((parsed: Record<string, unknown>) =>
    companiesApi.customize(me.company_id, parsed, reason),
  );
  const submit = (event: FormEvent) => {
    event.preventDefault();
    try {
      const parsed = JSON.parse(changes) as Record<string, unknown>;
      setParseError(null);
      customize.mutate(parsed, {
        onSuccess: () => {
          setChanges("");
          setReason("");
        },
      });
    } catch (e) {
      setParseError(e instanceof Error ? e.message : String(e));
    }
  };
  if (institution.isLoading) return <Skeleton className="h-40 w-full" />;
  const tpl = institution.data?.template;
  return (
    <div className="space-y-6">
      <Section
        title={
          tpl
            ? `${tpl.name} (${tpl.id}) · ${t("settings.version", { version: tpl.version })}`
            : t("settings.institution")
        }
      >
        <p className="text-xs text-muted-foreground">{t("settings.institutionHint")}</p>
        {tpl ? (
          <div className="mt-4 grid gap-4 md:grid-cols-2">
            <div>
              <p className="text-xs font-semibold">{t("settings.roles")}</p>
              <ul className="mt-1 space-y-0.5 text-xs">
                {tpl.roles.map((r) => (
                  <li key={r.code}>
                    <span className="font-mono">{r.code}</span> · {r.title} · {r.kind}
                    {r.reports_to ? (
                      <span className="text-muted-foreground"> → {r.reports_to}</span>
                    ) : null}
                  </li>
                ))}
              </ul>
            </div>
            <div>
              <p className="text-xs font-semibold">{t("settings.gates")}</p>
              <ul className="mt-1 space-y-0.5 text-xs">
                {Object.entries(tpl.gates).map(([gate, rule]) => (
                  <li key={gate}>
                    <span className="font-mono">{gate}</span>{" "}
                    <span className="text-muted-foreground">
                      {t("settings.decider")}: {String(rule.decider)}
                      {rule.role ? ` / ${String(rule.role)}` : ""}
                      {Number(rule.quorum) > 1 ? ` ×${String(rule.quorum)}` : ""}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        ) : null}
      </Section>
      {me.role === "owner" ? (
        <Section title={t("settings.customize")}>
          <form onSubmit={submit} className="space-y-3">
            <Textarea
              aria-label={t("settings.changes")}
              placeholder='{"review_policy": {"rework_limit": 1}}'
              className="font-mono text-xs"
              value={changes}
              onChange={(e) => setChanges(e.target.value)}
            />
            <Input
              aria-label={t("settings.reason")}
              placeholder={t("settings.reason")}
              required
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
            <ErrorNote error={parseError ?? customize.error} />
            <Button type="submit" disabled={customize.isPending}>
              {t("settings.customize")}
            </Button>
          </form>
        </Section>
      ) : null}
    </div>
  );
}

function AboutSection() {
  const { t } = useTranslation("app");
  return (
    <Section title={t("settings.about")}>
      <p className="text-sm text-muted-foreground">{t("settings.aboutText")}</p>
      <p className="mt-2 text-xs">
        {t("settings.api")}: <span className="font-mono">/api/v2</span>
      </p>
    </Section>
  );
}
