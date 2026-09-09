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

/**
 * 游戏化导航栏：默认收成图标轨道（76px），把视觉重心让给中央舞台。
 *
 * - 图标优先：常态只显示图标，hover/focus 时浮出名称（带 title 兜底）
 * - 状态可见：活跃项有发光侧条，待办数量用小圆点/数字提示
 * - 可展开：需要浏览时切到 236px 带文字的形态
 */
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
        { to: "/talent-roster", key: "talentRoster", icon: Users, badge: employees.length },
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
      data-tutorial-protected="true"
      className={cn(
        "fixed inset-y-0 left-0 z-50 flex flex-col border-r border-border/70 bg-surface/92 backdrop-blur-xl transition-[width,transform] duration-300 lg:sticky lg:top-0 lg:h-screen lg:translate-x-0",
        collapsed ? "w-[76px]" : "w-[236px]",
        mobileOpen ? "translate-x-0" : "-translate-x-full",
      )}
    >
      <div
        className={cn(
          "flex h-14 items-center border-b border-border/70 px-3",
          collapsed ? "justify-center" : "justify-between",
        )}
      >
        <NavLink to="/" className="group flex min-w-0 items-center gap-2.5" onClick={onMobileClose}>
          <span className="relative flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-primary/35 bg-primary/10 text-sm font-bold text-primary shadow-[var(--glow-primary)]">
            E
            <span className="absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full border border-surface bg-success status-pulse" />
          </span>
          {!collapsed ? (
            <span className="min-w-0">
              <span className="block truncate text-[13px] font-semibold tracking-[0.14em]">
                EIDOLON
              </span>
              <span className="block truncate text-[10px] text-muted-foreground">
                {t("nav:productType")}
              </span>
            </span>
          ) : null}
        </NavLink>
        {!collapsed ? (
          <button
            type="button"
            onClick={onMobileClose}
            className="flex h-9 w-9 items-center justify-center rounded-lg text-muted-foreground hover:bg-muted lg:hidden"
            aria-label={t("common:close")}
          >
            <X className="h-4 w-4" />
          </button>
        ) : null}
      </div>

      <nav
        className="scroll-area flex-1 space-y-1 overflow-y-auto px-2.5 py-3"
        aria-label={t("nav:primaryNavigation")}
      >
        {sections.map((section, sectionIndex) => (
          <div key={section.key}>
            {sectionIndex > 0 ? <div className="mx-2 my-2 h-px bg-border/60" /> : null}
            {!collapsed ? (
              <p className="mb-1 px-2 font-mono text-[10px] uppercase tracking-[0.16em] text-muted-foreground/60">
                {t(`nav:sections.${section.key}`)}
              </p>
            ) : null}
            <div className="space-y-0.5">
              {section.items.map(({ to, key, icon: Icon, end, badge }) => (
                <NavLink
                  key={`${section.key}-${key}`}
                  to={to}
                  end={end}
                  onClick={onMobileClose}
                  title={collapsed ? t(`nav:${key}`) : undefined}
                  className={({ isActive }) =>
                    cn(
                      "group relative flex h-11 items-center rounded-xl transition-colors",
                      collapsed ? "justify-center" : "gap-3 px-3",
                      isActive
                        ? "bg-primary/12 text-primary"
                        : "text-muted-foreground hover:bg-surface-interactive hover:text-foreground",
                    )
                  }
                >
                  {({ isActive }) => (
                    <>
                      <span
                        className={cn(
                          "absolute left-0 top-1/2 h-5 w-0.5 -translate-y-1/2 rounded-full bg-primary transition-opacity",
                          isActive ? "opacity-100" : "opacity-0",
                        )}
                      />
                      <Icon
                        className={cn(
                          "h-[19px] w-[19px] shrink-0",
                          isActive ? "text-primary" : "group-hover:text-foreground",
                        )}
                      />
                      {!collapsed ? (
                        <span className="min-w-0 flex-1 truncate text-[13px]">
                          {t(`nav:${key}`)}
                        </span>
                      ) : null}
                      {badge != null && badge > 0 ? (
                        collapsed ? (
                          <span className="absolute right-1.5 top-1.5 h-1.5 w-1.5 rounded-full bg-primary" />
                        ) : (
                          <span className="type-telemetry rounded-md bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                            {badge}
                          </span>
                        )
                      ) : null}
                      {collapsed ? (
                        <span className="pointer-events-none absolute left-full top-1/2 z-50 ml-3 -translate-y-1/2 whitespace-nowrap rounded-lg border border-border bg-card px-2.5 py-1 text-xs opacity-0 shadow-[var(--shadow-floating)] transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100">
                          {t(`nav:${key}`)}
                        </span>
                      ) : null}
                    </>
                  )}
                </NavLink>
              ))}
            </div>
          </div>
        ))}
      </nav>

      <div className="border-t border-border/70 p-2.5">
        {collapsed ? (
          <div
            className="flex flex-col items-center gap-1 rounded-xl border border-success/20 bg-success/5 py-2"
            title={`${online}/${employees.length} ${t("nav:activeAgents")}`}
          >
            <span className="h-2 w-2 rounded-full bg-success status-pulse" />
            <span className="type-telemetry text-[10px] text-success">{online}</span>
          </div>
        ) : (
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
        )}
        <button
          type="button"
          onClick={onCollapse}
          className="hidden h-9 w-full items-center justify-center rounded-xl text-muted-foreground hover:bg-muted hover:text-foreground lg:flex"
          aria-label={t("nav:toggleSidebar")}
          title={t("nav:toggleSidebar")}
        >
          <ChevronLeft className={cn("h-4 w-4 transition-transform", collapsed && "rotate-180")} />
          {!collapsed ? <span className="ml-2 text-xs">{t("nav:collapse")}</span> : null}
        </button>
      </div>
    </aside>
  );
}
