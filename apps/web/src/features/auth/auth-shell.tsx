import { Globe2, Orbit } from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { LanguageSelector } from "../../components/common/language-selector";
import { AuthOfficeCover } from "../auth-office-game/components/auth-office-cover";

export function AuthShell({ children }: { children: ReactNode }) {
  const { t } = useTranslation("auth");
  return (
    <main
      className="auth-world-shell relative min-h-screen overflow-x-hidden bg-background"
      data-testid="auth-shell"
    >
      <div className="auth-world-background">
        <AuthOfficeCover />
      </div>
      <div className="auth-world-scrim" aria-hidden="true" />

      <header className="auth-world-header">
        <div className="flex items-center gap-3">
          <span className="auth-world-mark flex h-11 w-11 items-center justify-center text-primary">
            <Orbit className="h-5 w-5" />
          </span>
          <div>
            <p className="text-sm font-semibold tracking-[.22em]">EIDOLON</p>
            <p className="text-[10px] uppercase tracking-[.18em] text-white/60">AI Company OS</p>
          </div>
        </div>
        <LanguageSelector />
      </header>

      <section className="auth-world-copy hidden lg:block">
        <p className="type-kicker text-primary">{t("shell.kicker")}</p>
        <h1 className="mt-3 max-w-xl text-4xl font-semibold leading-[1.08] tracking-[-.05em]">
          {t("shell.title")}
        </h1>
        <p className="mt-3 max-w-lg text-sm leading-6 text-white/72">{t("shell.description")}</p>
        <p className="mt-4 font-mono text-[9px] tracking-[.16em] text-white/50">
          HUMAN COMMAND · AI WORKFORCE · VERIFIED DELIVERY
        </p>
      </section>

      <section className="auth-world-auth relative flex min-h-screen items-center justify-center px-4 py-24 sm:px-6 lg:ml-auto lg:w-[min(44vw,580px)] lg:justify-end lg:px-8 xl:px-12">
        <div className="w-full max-w-[460px]">
          <div className="auth-card auth-control-panel p-6 sm:p-8">{children}</div>
          <div className="mt-4 hidden items-center justify-end gap-2 font-mono text-[9px] tracking-[.12em] text-white/50 lg:flex">
            <Globe2 className="h-3.5 w-3.5" aria-hidden="true" />
            SECURE COMPANY ACCESS
          </div>
        </div>
      </section>
    </main>
  );
}
