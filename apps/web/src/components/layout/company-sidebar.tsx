import { NavLink } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  Bot,
  Building2,
  ChevronLeft,
  CircleGauge,
  Cloud,
  FolderKanban,
  Users,
  X,
  type LucideIcon,
  ClipboardList,
} from "lucide-react";
import { cn } from "../../utils/cn";
import { useEmployees } from "../../hooks/useEmployees";
import { useProjects } from "../../hooks/useProjects";

interface NavigationItem {
  to: string;
  key: string;
  icon: LucideIcon;
  end?: boolean;
  badge?: number;
}

export function CompanySidebar({
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
  const employees = useEmployees().data ?? [];
  const projects = useProjects().data ?? [];
  const online = employees.filter((employee) => employee.status !== "offline").length;
  const activeProjects = projects.filter(
    (project) => !["completed", "cancelled", "rejected"].includes(project.status),
  ).length;
  const sections: Array<{ key: string; items: NavigationItem[] }> = [
    {
      key: "command",
      items: [
        { to: "/", key: "overview", icon: CircleGauge, end: true },
        { to: "/office", key: "office", icon: Building2, badge: online },
      ],
    },
    {
      key: "workforce",
      items: [
        { to: "/employees", key: "employees", icon: Users, badge: employees.length },
        { to: "/positions", key: "positions", icon: ClipboardList },
      ],
    },
    {
      key: "work",
      items: [{ to: "/projects", key: "projects", icon: FolderKanban, badge: activeProjects }],
    },
    { key: "assets", items: [{ to: "/drive", key: "cloudDocs", icon: Cloud }] },
    { key: "infrastructure", items: [{ to: "/runtime", key: "runtime", icon: Bot }] },
    // 设置统一收进右上角账号菜单（个人 + 系统），主导航不再放"系统设置"
  ];
  return (
    <aside
      className={cn(
        "fixed inset-y-0 left-0 z-50 flex flex-col border-r border-border bg-surface/96 shadow-2xl backdrop-blur-xl transition-[width,transform] duration-300 lg:sticky lg:top-0 lg:h-screen lg:translate-x-0 lg:shadow-none",
        collapsed ? "w-20" : "w-[272px]",
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
          <span className="relative flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-primary/35 bg-primary/10 text-sm font-bold text-primary shadow-[var(--glow-primary)]">
            E
            <span className="absolute right-1.5 top-1.5 h-1.5 w-1.5 rounded-full bg-success status-pulse" />
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
            className="flex h-10 w-10 items-center justify-center rounded-lg text-muted-foreground hover:bg-muted lg:hidden"
            aria-label={t("common:close")}
          >
            <X className="h-4 w-4" />
          </button>
        ) : null}
      </div>
      <nav
        className="scroll-area flex-1 space-y-5 overflow-y-auto px-3 py-5"
        aria-label={t("nav:primaryNavigation")}
      >
        {sections.map((section) => (
          <section key={section.key}>
            {!collapsed ? (
              <p className="mb-1.5 px-3 font-mono text-[9px] font-semibold uppercase tracking-[0.18em] text-muted-foreground/70">
                {t(`nav:sections.${section.key}`)}
              </p>
            ) : (
              <div className="mx-3 mb-2 h-px bg-border" />
            )}
            <div className="space-y-1">
              {section.items.map(({ to, key, icon: Icon, end, badge }) => (
                <NavLink
                  key={`${section.key}-${key}`}
                  to={to}
                  end={end}
                  onClick={onMobileClose}
                  title={collapsed ? t(`nav:${key}`) : undefined}
                  className={({ isActive }) =>
                    cn(
                      "group relative flex min-h-11 items-center rounded-xl text-sm transition-colors",
                      collapsed ? "justify-center px-2" : "gap-3 px-3",
                      isActive
                        ? "bg-primary/10 font-medium text-foreground"
                        : "text-muted-foreground hover:bg-surface-interactive hover:text-foreground",
                    )
                  }
                >
                  {({ isActive }) => (
                    <>
                      <span
                        className={cn(
                          "absolute inset-y-2 left-0 w-0.5 rounded-full bg-primary transition-opacity",
                          isActive ? "opacity-100" : "opacity-0",
                        )}
                      />
                      <Icon
                        className={cn(
                          "h-[18px] w-[18px] shrink-0",
                          isActive ? "text-primary" : "group-hover:text-foreground",
                        )}
                      />
                      {!collapsed ? (
                        <span className="min-w-0 flex-1 truncate">{t(`nav:${key}`)}</span>
                      ) : null}
                      {!collapsed && badge != null && badge > 0 ? (
                        <span className="type-telemetry rounded-md border border-border bg-background/70 px-1.5 py-0.5 text-[10px] text-muted-foreground">
                          {badge}
                        </span>
                      ) : null}
                    </>
                  )}
                </NavLink>
              ))}
            </div>
          </section>
        ))}
      </nav>
      <div className="border-t border-border p-3">
        {!collapsed ? (
          <div className="mb-2 rounded-xl border border-success/20 bg-success/5 p-3">
            <div className="flex items-center justify-between">
              <span className="type-kicker text-success">{t("nav:companyLive")}</span>
              <span className="h-2 w-2 rounded-full bg-success status-pulse" />
            </div>
            <div className="mt-2 flex items-end gap-2">
              <span className="type-telemetry text-xl font-semibold">
                {online}/{employees.length}
              </span>
              <span className="pb-0.5 text-[11px] text-muted-foreground">
                {t("nav:activeAgents")}
              </span>
            </div>
          </div>
        ) : null}
        <button
          type="button"
          onClick={onCollapse}
          className="hidden h-10 w-full items-center justify-center rounded-xl text-muted-foreground hover:bg-muted hover:text-foreground lg:flex"
          aria-label={t("nav:toggleSidebar")}
        >
          <ChevronLeft className={cn("h-4 w-4 transition-transform", collapsed && "rotate-180")} />
          {!collapsed ? <span className="ml-2 text-xs">{t("nav:collapse")}</span> : null}
        </button>
      </div>
    </aside>
  );
}
