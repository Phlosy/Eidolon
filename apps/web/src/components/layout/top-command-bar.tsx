import { useEffect, useRef } from "react";
import { Link, useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Bell, Menu, Moon, Plus, Search, Sun, UserRound } from "lucide-react";
import { useCompany } from "../../hooks/useSystem";
import { useEmployees } from "../../hooks/useEmployees";
import { useEventStreamStore } from "../../stores/events";
import { applyTheme, useThemeStore } from "../../stores/theme";
import { LanguageSelector } from "../common/language-selector";
import { cn } from "../../utils/cn";

const ROUTE_LABELS: Array<[RegExp, string]> = [
  [/^\/$/, "overview"],
  [/^\/office/, "office"],
  [/^\/employees\/\d+/, "employeeWorkbench"],
  [/^\/employees/, "employees"],
  [/^\/projects\/\d+/, "missionControl"],
  [/^\/projects/, "projects"],
  [/^\/drive/, "cloudDocs"],
  [/^\/runtime/, "runtime"],
  [/^\/settings/, "settings"],
];

export function TopCommandBar({ onOpenNavigation }: { onOpenNavigation: () => void }) {
  const { t } = useTranslation();
  const location = useLocation();
  const company = useCompany().data;
  const employees = useEmployees().data ?? [];
  const connection = useEventStreamStore((state) => state.connection);
  const theme = useThemeStore((state) => state.theme);
  const toggleTheme = useThemeStore((state) => state.toggleTheme);
  const searchRef = useRef<HTMLInputElement>(null);
  const routeKey =
    ROUTE_LABELS.find(([pattern]) => pattern.test(location.pathname))?.[1] ?? "overview";
  const activeEmployees = employees.filter((employee) => employee.status !== "offline").length;
  useEffect(() => applyTheme(theme), [theme]);
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        searchRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);
  return (
    <header className="sticky top-0 z-30 flex h-[72px] items-center gap-3 border-b border-border bg-background/82 px-4 backdrop-blur-xl md:px-6">
      <button
        type="button"
        onClick={onOpenNavigation}
        className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border border-border bg-surface text-muted-foreground hover:text-foreground lg:hidden"
        aria-label={t("nav:openNavigation")}
      >
        <Menu className="h-4 w-4" />
      </button>
      <div className="min-w-0 flex-1">
        <p className="truncate text-[11px] text-muted-foreground">
          <span className="font-medium text-foreground">
            {company?.name ?? t("nav:defaultCompany")}
          </span>
          <span className="mx-2 text-border-active">/</span>
          {t(`nav:${routeKey}`)}
        </p>
        <p className="hidden truncate text-[10px] uppercase tracking-[0.16em] text-muted-foreground/60 md:block">
          {t("nav:headerSubtitle")}
        </p>
      </div>
      <label className="relative hidden min-w-52 max-w-sm flex-1 lg:block">
        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
        <input
          ref={searchRef}
          type="search"
          aria-label={t("nav:globalSearch")}
          placeholder={t("nav:searchPlaceholder")}
          className="h-10 w-full rounded-xl border border-border bg-surface/80 pl-9 pr-14 text-xs outline-none transition focus:border-border-active focus:ring-2 focus:ring-primary/15"
        />
        <span className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 rounded border border-border px-1.5 py-0.5 font-mono text-[9px] text-muted-foreground">
          ⌘K
        </span>
      </label>
      <div
        data-testid="connection-pill"
        className={cn(
          "hidden h-9 items-center gap-2 rounded-xl border px-3 text-[10px] font-semibold uppercase tracking-[0.12em] sm:flex",
          connection === "open"
            ? "border-success/25 bg-success/8 text-success"
            : connection === "connecting"
              ? "border-warning/25 bg-warning/8 text-warning"
              : "border-danger/25 bg-danger/8 text-danger",
        )}
      >
        <span
          className={cn(
            "h-1.5 w-1.5 rounded-full bg-current",
            connection !== "closed" && "status-pulse",
          )}
        />
        {t(`nav:connection.${connection}`)}
      </div>
      <div className="hidden items-center gap-2 rounded-xl border border-border bg-surface px-3 py-2 xl:flex">
        <span className="h-2 w-2 rounded-full bg-success" />
        <span className="type-telemetry text-xs">
          {activeEmployees}/{employees.length}
        </span>
        <span className="text-[10px] text-muted-foreground">{t("nav:activeAgents")}</span>
      </div>
      <Link
        to="/projects"
        className="hidden h-10 items-center gap-2 rounded-xl bg-primary px-3 text-xs font-semibold text-primary-foreground shadow-[var(--glow-primary)] transition hover:brightness-105 md:flex"
      >
        <Plus className="h-4 w-4" />
        {t("nav:create")}
      </Link>
      <button
        type="button"
        className="flex h-10 w-10 items-center justify-center rounded-xl text-muted-foreground hover:bg-muted hover:text-foreground"
        aria-label={t("nav:notifications")}
      >
        <Bell className="h-4 w-4" />
      </button>
      <LanguageSelector compact />
      <button
        type="button"
        onClick={toggleTheme}
        className="flex h-10 w-10 items-center justify-center rounded-xl text-muted-foreground hover:bg-muted hover:text-foreground"
        aria-label={t("nav:toggleTheme")}
      >
        {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
      </button>
      <Link
        to="/settings#security"
        className="flex h-10 w-10 items-center justify-center rounded-xl border border-border bg-surface text-muted-foreground hover:border-border-active hover:text-foreground"
        aria-label="Account security"
      >
        <UserRound className="h-4 w-4" />
      </Link>
    </header>
  );
}
