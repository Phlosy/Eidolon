import { useEffect } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  Building2,
  FolderKanban,
  LayoutDashboard,
  Moon,
  Settings,
  Sun,
  Users,
  FileStack,
} from "lucide-react";
import { cn } from "../utils/cn";
import { useCompany } from "../hooks/useSystem";
import { useEventStream } from "../hooks/useEventStream";
import { applyTheme, useThemeStore } from "../stores/theme";
import { useEventStreamStore } from "../stores/events";
import { Button } from "../components/common/button";
import { LanguageSelector } from "../components/common/language-selector";

import type { LucideIcon } from "lucide-react";

interface NavItem {
  to: string;
  navKey: string;
  icon: LucideIcon;
  end?: boolean;
}

const NAV_ITEMS: NavItem[] = [
  { to: "/", navKey: "dashboard", icon: LayoutDashboard, end: true },
  { to: "/office", navKey: "office", icon: Building2 },
  { to: "/employees", navKey: "employees", icon: Users },
  { to: "/projects", navKey: "projects", icon: FolderKanban },
  { to: "/artifacts", navKey: "artifacts", icon: FileStack },
  { to: "/settings", navKey: "settings", icon: Settings },
];

const CONNECTION_META = {
  open: { navKey: "connection.open", dot: "bg-emerald-500 status-pulse" },
  connecting: { navKey: "connection.connecting", dot: "bg-amber-500 status-pulse" },
  closed: { navKey: "connection.closed", dot: "bg-red-500" },
} as const;

export function AppLayout() {
  const { t } = useTranslation();
  const theme = useThemeStore((s) => s.theme);
  const toggleTheme = useThemeStore((s) => s.toggleTheme);
  const connection = useEventStreamStore((s) => s.connection);
  const companyQuery = useCompany();

  useEventStream();

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  const connectionMeta = CONNECTION_META[connection];

  return (
    <div className="flex min-h-screen">
      <aside className="flex w-56 shrink-0 flex-col border-r border-border bg-card/50">
        <div className="flex h-14 items-center gap-2 border-b border-border px-4">
          <div className="flex h-6 w-6 items-center justify-center rounded bg-foreground text-[11px] font-bold text-background">
            E
          </div>
          <span className="text-sm font-semibold tracking-tight">Eidolon</span>
        </div>
        <nav className="flex-1 space-y-0.5 p-2">
          {NAV_ITEMS.map(({ to, navKey, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-2.5 rounded-md px-3 py-2 text-sm text-muted-foreground transition-colors hover:bg-muted hover:text-foreground",
                  isActive && "bg-muted font-medium text-foreground",
                )
              }
            >
              <Icon className="h-4 w-4 shrink-0" />
              <span className="truncate">{t(`nav:${navKey}`)}</span>
            </NavLink>
          ))}
        </nav>
        <div className="border-t border-border p-3 text-[11px] text-muted-foreground">
          {t("nav:tagline")}
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center justify-between gap-4 border-b border-border px-6">
          <p className="min-w-0 truncate text-sm text-muted-foreground">
            {companyQuery.data?.name ?? t("nav:defaultCompany")}
            <span className="mx-2 text-border">/</span>
            <span className="text-foreground">{t("nav:headerSubtitle")}</span>
          </p>
          <div className="flex shrink-0 items-center gap-3">
            <span className="hidden items-center gap-1.5 text-xs text-muted-foreground sm:flex">
              <span className={cn("h-1.5 w-1.5 rounded-full", connectionMeta.dot)} />
              {t(`nav:${connectionMeta.navKey}`)}
            </span>
            <LanguageSelector compact />
            <Button
              variant="ghost"
              size="icon"
              aria-label={t("nav:toggleTheme")}
              onClick={toggleTheme}
            >
              {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            </Button>
          </div>
        </header>
        <main className="min-w-0 flex-1 p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
