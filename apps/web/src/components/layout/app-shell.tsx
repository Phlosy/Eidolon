import { useEffect, useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  Building2,
  ChevronLeft,
  CircleGauge,
  Cloud,
  FolderKanban,
  LogOut,
  Menu,
  Moon,
  Settings,
  Sun,
  Users,
  Wallet,
  X,
  type LucideIcon,
} from "lucide-react";
import { auth as authApi } from "../../api";
import { useAuth } from "../../features/auth/auth-context";
import { useCompany } from "../../features/company/company-context";
import { applyTheme, useThemeStore } from "../../stores/theme";
import { cn } from "../../utils/cn";
import { LanguageSelector } from "../common/language-selector";

interface Item {
  to: string;
  key: string;
  icon: LucideIcon;
  end?: boolean;
}

const ITEMS: Item[] = [
  { to: "/", key: "overview", icon: CircleGauge, end: true },
  { to: "/projects", key: "projects", icon: FolderKanban },
  { to: "/employees", key: "employees", icon: Users },
  { to: "/drive", key: "drive", icon: Cloud },
  { to: "/economy", key: "economy", icon: Wallet },
  { to: "/settings", key: "settings", icon: Settings },
];

export function AppShell() {
  const { t } = useTranslation();
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  return (
    <div className="min-h-screen bg-background lg:flex">
      {mobileOpen ? (
        <button
          type="button"
          className="fixed inset-0 z-40 bg-black/50 backdrop-blur-sm lg:hidden"
          onClick={() => setMobileOpen(false)}
          aria-label={t("nav:closeNavigation")}
        />
      ) : null}
      <Sidebar
        collapsed={collapsed}
        mobileOpen={mobileOpen}
        onCollapse={() => setCollapsed((v) => !v)}
        onMobileClose={() => setMobileOpen(false)}
      />
      <div className="min-w-0 flex-1">
        <TopBar onOpenNavigation={() => setMobileOpen(true)} />
        <main className="app-grid min-h-[calc(100vh-72px)] min-w-0 p-4 pb-16 md:p-6 xl:p-8">
          <div className="mx-auto max-w-[1600px]">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  );
}

function Sidebar({
  collapsed,
  mobileOpen,
  onCollapse,
  onMobileClose,
}: {
  collapsed: boolean;
  mobileOpen: boolean;
  onCollapse: () => void;
  onMobileClose: () => void;
}) {
  const { t } = useTranslation();
  return (
    <aside
      className={cn(
        "fixed inset-y-0 left-0 z-50 flex flex-col border-r border-border bg-surface/96 shadow-[var(--shadow-floating)] backdrop-blur-xl transition-[width,transform] duration-300 lg:sticky lg:top-0 lg:h-screen lg:translate-x-0 lg:shadow-none",
        collapsed ? "w-20" : "w-[248px]",
        mobileOpen ? "translate-x-0" : "-translate-x-full",
      )}
    >
      <div
        className={cn(
          "flex h-[72px] items-center border-b border-border px-4",
          collapsed ? "justify-center" : "justify-between",
        )}
      >
        <NavLink to="/" className="flex min-w-0 items-center gap-3" onClick={onMobileClose}>
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-primary/35 bg-primary/10 text-sm font-bold text-primary shadow-[var(--glow-primary)]">
            E
          </span>
          {!collapsed ? (
            <span className="min-w-0">
              <span className="block truncate text-sm font-semibold tracking-[0.16em]">
                EIDOLON
              </span>
              <span className="block truncate text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
                {t("nav:productType")}
              </span>
            </span>
          ) : null}
        </NavLink>
        {!collapsed ? (
          <button
            type="button"
            onClick={onMobileClose}
            className="flex h-9 w-9 items-center justify-center rounded-xl text-muted-foreground hover:bg-muted lg:hidden"
            aria-label={t("nav:closeNavigation")}
          >
            <X className="h-4 w-4" />
          </button>
        ) : null}
      </div>
      <nav className="flex-1 space-y-1 p-3" aria-label={t("nav:primaryNavigation")}>
        {ITEMS.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            onClick={onMobileClose}
            className={({ isActive }) =>
              cn(
                "flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm transition-colors",
                isActive
                  ? "bg-primary/10 font-medium text-primary"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground",
                collapsed && "justify-center px-0",
              )
            }
            title={t(`nav:${item.key}`)}
          >
            <item.icon className="h-4 w-4 shrink-0" />
            {!collapsed ? <span className="truncate">{t(`nav:${item.key}`)}</span> : null}
          </NavLink>
        ))}
      </nav>
      <button
        type="button"
        onClick={onCollapse}
        className="m-3 hidden h-10 items-center justify-center gap-2 rounded-xl border border-border text-xs text-muted-foreground hover:bg-muted lg:flex"
        aria-label={t("nav:toggleSidebar")}
      >
        <ChevronLeft className={cn("h-4 w-4 transition-transform", collapsed && "rotate-180")} />
        {!collapsed ? t("nav:collapse") : null}
      </button>
    </aside>
  );
}

function TopBar({ onOpenNavigation }: { onOpenNavigation: () => void }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { me, setMe } = useAuth();
  const { memberships, current, select } = useCompany();
  const theme = useThemeStore((state) => state.theme);
  const toggleTheme = useThemeStore((state) => state.toggleTheme);
  useEffect(() => applyTheme(theme), [theme]);
  const logout = async () => {
    try {
      await authApi.logout();
    } finally {
      setMe(null);
      navigate("/auth/login", { replace: true });
    }
  };
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
      <div className="flex min-w-0 flex-1 items-center gap-2">
        <Building2 className="h-4 w-4 shrink-0 text-muted-foreground" />
        {memberships.length > 1 ? (
          <select
            aria-label={t("nav:switchCompany")}
            value={current?.company_id ?? ""}
            onChange={(e) => select(e.target.value)}
            className="max-w-xs truncate rounded-lg border border-border bg-transparent px-2 py-1 text-xs [&>option]:bg-card"
          >
            {memberships.map((m) => (
              <option key={m.company_id} value={m.company_id}>
                {m.company_id.slice(0, 8)} · {m.role}
              </option>
            ))}
          </select>
        ) : (
          <span className="truncate text-xs text-muted-foreground">
            {current ? `${current.person_id} · ${current.role}` : t("nav:noCompany")}
          </span>
        )}
      </div>
      <LanguageSelector compact />
      <button
        type="button"
        onClick={toggleTheme}
        className="flex h-10 w-10 items-center justify-center rounded-xl text-muted-foreground hover:bg-muted hover:text-foreground"
        aria-label={t("nav:toggleTheme")}
      >
        {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
      </button>
      <span className="hidden max-w-40 truncate text-xs font-medium md:block" title={me?.email}>
        {me?.display_name}
      </span>
      <button
        type="button"
        onClick={() => void logout()}
        className="flex h-10 w-10 items-center justify-center rounded-xl text-muted-foreground hover:bg-muted hover:text-foreground"
        aria-label={t("nav:userMenu.logout")}
      >
        <LogOut className="h-4 w-4" />
      </button>
    </header>
  );
}
