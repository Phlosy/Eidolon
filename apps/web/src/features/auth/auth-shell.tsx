import { Info, Orbit, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { LanguageSelector } from "../../components/common/language-selector";
import { AuthOfficeCover } from "../auth-office-game/components/auth-office-cover";

export function AuthShell({ children }: { children: ReactNode }) {
  const { t } = useTranslation("auth");
  const [guideOpen, setGuideOpen] = useState(false);
  const noticeRef = useRef<HTMLButtonElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

  const closeGuide = useCallback(() => {
    setGuideOpen(false);
    window.requestAnimationFrame(() => noticeRef.current?.focus());
  }, []);

  useEffect(() => {
    if (!guideOpen) return;
    closeRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeGuide();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [closeGuide, guideOpen]);

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
            <h1 className="text-sm font-semibold tracking-[.22em]">EIDOLON</h1>
            <p className="text-[10px] uppercase tracking-[.18em] text-white/60">AI Company OS</p>
          </div>
        </div>
        <LanguageSelector />
      </header>

      <section className="auth-world-guide hidden lg:block">
        {guideOpen ? (
          <aside
            id="auth-office-guide"
            role="dialog"
            aria-label={t("shell.notice.label")}
            className="auth-world-guide-card"
          >
            <div className="auth-world-guide-heading">
              <p>{t("shell.notice.kicker")}</p>
              <button
                ref={closeRef}
                type="button"
                aria-label={t("shell.notice.close")}
                onClick={closeGuide}
              >
                <X aria-hidden="true" />
              </button>
            </div>
            <h2>{t("shell.notice.title")}</h2>
            <p className="auth-world-guide-copy">{t("shell.notice.description")}</p>
            <p className="auth-world-guide-tip">{t("shell.notice.detail")}</p>
          </aside>
        ) : null}
        <button
          ref={noticeRef}
          type="button"
          className="auth-world-notice"
          aria-label={t("shell.notice.open")}
          aria-expanded={guideOpen}
          aria-controls="auth-office-guide"
          onClick={() => setGuideOpen((open) => !open)}
        >
          <span className="auth-world-notice-icon" aria-hidden="true">
            <Info />
          </span>
          <span>
            <strong>{t("shell.notice.label")}</strong>
            <small>{t("shell.notice.hint")}</small>
          </span>
        </button>
      </section>

      <section className="auth-world-auth relative flex min-h-screen items-center justify-center px-4 py-24 sm:px-6 lg:ml-auto lg:w-[min(42vw,540px)] lg:justify-end lg:px-7 xl:px-10">
        <div className="auth-terminal-mount w-full max-w-[440px]">
          <div className="auth-terminal-heading" aria-hidden="true">
            <span />
            {t("shell.terminal")}
          </div>
          <div className="auth-card auth-control-panel p-6 sm:p-8">{children}</div>
        </div>
      </section>
    </main>
  );
}
