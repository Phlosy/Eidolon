import { Outlet } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { LanguageSelector } from "../../components/common/language-selector";

/** The frame around sign-in and registration. */
export function AuthShellLayout() {
  const { t } = useTranslation("auth");
  return (
    <div className="flex min-h-screen flex-col bg-background" data-testid="auth-shell">
      <header className="flex h-[72px] items-center justify-between px-6">
        <h1 className="flex items-center gap-3 text-sm font-semibold tracking-[0.16em]">
          <span className="flex h-10 w-10 items-center justify-center rounded-xl border border-primary/35 bg-primary/10 text-primary shadow-[var(--glow-primary)]">
            E
          </span>
          EIDOLON
        </h1>
        <LanguageSelector compact />
      </header>
      <main className="flex flex-1 items-center justify-center px-4 pb-16">
        <div className="w-full max-w-md">
          <p className="mb-6 text-center text-xs text-muted-foreground">{t("shell.tagline")}</p>
          <div className="command-panel p-6 md:p-8">
            <Outlet />
          </div>
        </div>
      </main>
    </div>
  );
}
