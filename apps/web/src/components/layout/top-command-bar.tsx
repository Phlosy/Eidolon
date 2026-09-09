import { useEffect, useRef } from "react";
import { Link, useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Bell, Menu, Moon, Plus, Search, Sun } from "lucide-react";
import { useCompany } from "../../hooks/useSystem";
import { useEmployees } from "../../hooks/useEmployees";
import { useProjects } from "../../hooks/useProjects";
import { useEventStreamStore } from "../../stores/events";
import { applyTheme, useThemeStore } from "../../stores/theme";
import { LanguageSelector } from "../common/language-selector";
import { UserMenu } from "./user-menu";
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

/**
 * 顶部 HUD：只放「现在该关心什么」，不放导航。
 *
 * 左：当前位置（公司 / 页面）
 * 右：三个状态点（连接、在线员工、进行中项目）+ 图标动作
 *
 * 文字尽量少：动作一律图标 + aria-label/title，指标只留数字。
 */
export function TopCommandBar({ onOpenNavigation }: { onOpenNavigation: () => void }) {
  const { t } = useTranslation();
  const location = useLocation();
  const company = useCompany().data;
  const employees = useEmployees().data ?? [];
  const projects = useProjects().data ?? [];
  const connection = useEventStreamStore((state) => state.connection);
  const theme = useThemeStore((state) => state.theme);
  const toggleTheme = useThemeStore((state) => state.toggleTheme);
  const searchRef = useRef<HTMLInputElement>(null);
  const routeKey =
    ROUTE_LABELS.find(([pattern]) => pattern.test(location.pathname))?.[1] ?? "overview";
  const activeEmployees = employees.filter((employee) => employee.status !== "offline").length;
  const activeProjects = projects.filter(
    (project) => !["completed", "cancelled", "rejected"].includes(project.status),
  ).length;
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
    <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-border/70 bg-background/72 px-3 backdrop-blur-xl md:px-5">
      <button
        type="button"
        onClick={onOpenNavigation}
        className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-border bg-surface text-muted-foreground hover:text-foreground lg:hidden"
        aria-label={t("nav:openNavigation")}
      >
        <Menu className="h-4 w-4" />
      </button>

      {/* 当前位置：公司 / 页面 */}
      <div className="min-w-0 flex-1">
        <p className="truncate text-[11px] text-muted-foreground">
          <span className="font-medium text-foreground">
            {company?.name ?? t("nav:defaultCompany")}
          </span>
          <span className="mx-1.5 text-border-active">/</span>
          {t(`nav:${routeKey}`)}
        </p>
      </div>

      {/* HUD 指标：连接 + 在线 + 项目 */}
      <div className="hidden items-center gap-1.5 sm:flex">
        <div
          data-testid="connection-pill"
          title={t(`nav:connection.${connection}`)}
          className={cn(
            "flex h-8 items-center gap-1.5 rounded-lg border px-2.5",
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
          <span className="font-mono text-[10px] font-semibold uppercase tracking-[0.1em]">
            {t(`nav:connection.${connection}`)}
          </span>
        </div>
        <div
          className="flex h-8 items-center gap-1.5 rounded-lg border border-border bg-surface px-2.5"
          title={t("nav:activeAgents")}
        >
          <span className="h-1.5 w-1.5 rounded-full bg-success" />
          <span className="type-telemetry text-[11px]">
            {activeEmployees}/{employees.length}
          </span>
        </div>
        <div
          className="hidden h-8 items-center gap-1.5 rounded-lg border border-border bg-surface px-2.5 md:flex"
          title={t("nav:projects")}
        >
          <span className="h-1.5 w-1.5 rounded-full bg-primary" />
          <span className="type-telemetry text-[11px]">{activeProjects}</span>
        </div>
      </div>

      <label className="relative hidden min-w-44 max-w-xs flex-1 lg:block">
        <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
        <input
          ref={searchRef}
          type="search"
          aria-label={t("nav:globalSearch")}
          placeholder={t("nav:searchPlaceholder")}
          className="h-9 w-full rounded-lg border border-border bg-surface/70 pl-8 pr-12 text-xs outline-none transition focus:border-border-active focus:ring-2 focus:ring-primary/15"
        />
        <span className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 rounded border border-border px-1.5 py-0.5 font-mono text-[9px] text-muted-foreground">
          ⌘K
        </span>
      </label>

      {/* 图标动作区 */}
      <Link
        to="/projects"
        aria-label={t("nav:create")}
        title={t("nav:create")}
        className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary text-primary-foreground shadow-[var(--glow-primary)] transition hover:brightness-105"
      >
        <Plus className="h-4 w-4" />
      </Link>
      <button
        type="button"
        className="flex h-9 w-9 items-center justify-center rounded-xl text-muted-foreground hover:bg-muted hover:text-foreground"
        aria-label={t("nav:notifications")}
        title={t("nav:notifications")}
      >
        <Bell className="h-4 w-4" />
      </button>
      <LanguageSelector compact />
      <button
        type="button"
        onClick={toggleTheme}
        className="flex h-9 w-9 items-center justify-center rounded-xl text-muted-foreground hover:bg-muted hover:text-foreground"
        aria-label={t("nav:toggleTheme")}
        title={t("nav:toggleTheme")}
      >
        {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
      </button>
      <UserMenu />
    </header>
  );
}
