import { Fingerprint, Globe2, Orbit } from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { LanguageSelector } from "../../components/common/language-selector";
import { AuthOfficeCover } from "../auth-office-game/components/auth-office-cover";

export function AuthShell({ children }: { children: ReactNode }) {
  const { t } = useTranslation("auth");
  return (
    <main className="auth-grid relative min-h-screen overflow-hidden bg-background px-4 py-5 sm:px-6 lg:grid lg:grid-cols-[1.1fr_.9fr] lg:px-8">
      <section className="auth-brand-panel relative hidden min-h-[calc(100vh-40px)] flex-col justify-between p-6 lg:flex xl:p-8">
        <div className="flex items-center gap-3">
          <span className="flex h-11 w-11 items-center justify-center rounded-2xl border border-primary/30 bg-primary/10 text-primary shadow-[var(--glow-primary)]">
            <Orbit className="h-5 w-5" />
          </span>
          <div>
            <p className="text-sm font-semibold tracking-[.22em]">EIDOLON</p>
            <p className="text-[10px] uppercase tracking-[.18em] text-muted-foreground">
              AI Company OS
            </p>
          </div>
        </div>
        <div className="auth-brand-content flex flex-1 flex-col justify-center gap-5 py-5 xl:gap-6">
          <AuthOfficeCover />
          <div className="auth-brand-copy max-w-2xl">
            <p className="type-kicker text-primary">{t("shell.kicker")}</p>
            <h1 className="mt-4 text-4xl font-semibold leading-[1.05] tracking-[-.05em] xl:text-5xl">
              {t("shell.title")}
            </h1>
            <p className="mt-4 max-w-xl text-sm leading-6 text-muted-foreground xl:text-base xl:leading-7">
              {t("shell.description")}
            </p>
          </div>
        </div>
        <p className="font-mono text-[10px] tracking-[.16em] text-muted-foreground">
          HUMAN COMMAND · AI WORKFORCE · VERIFIED DELIVERY
        </p>
      </section>

      <section className="relative flex min-h-[calc(100vh-40px)] items-center justify-center py-8">
        <div className="w-full max-w-[500px]">
          <div className="mb-6 flex items-center justify-between lg:hidden">
            <div className="flex items-center gap-2 text-sm font-semibold tracking-[.18em]">
              <Fingerprint className="h-5 w-5 text-primary" />
              EIDOLON
            </div>
            <LanguageSelector />
          </div>
          <div className="auth-card rounded-[28px] border border-border bg-surface/92 p-6 shadow-[var(--shadow-floating)] backdrop-blur-xl sm:p-8">
            {children}
          </div>
          <div className="mt-5 hidden justify-end lg:flex">
            <span className="sr-only">
              <Globe2 />
              Language
            </span>
            <LanguageSelector />
          </div>
        </div>
      </section>
    </main>
  );
}
