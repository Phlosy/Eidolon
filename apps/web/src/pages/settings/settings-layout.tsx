import { NavLink, Outlet } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  BookOpen,
  Cpu,
  GitBranch,
  Info,
  KeyRound,
  Palette,
  Settings,
  ShieldCheck,
  UserRound,
  type LucideIcon,
} from "lucide-react";
import { PageHeader } from "../../components/common/states";
import { cn } from "../../utils/cn";

/**
 * 设置中心布局：左侧分区导航 + 右侧内容。
 *
 * 两级分组对应"谁的设置"：
 * - personal：跟着账号走（资料、安全、界面偏好）；
 * - system：跟着公司/运行时走（教程、服务商、Git、Runtime、关于）。
 * 每个分区一个子路由，不再把全部内容堆在一个长页面上。
 */

interface SettingsNavItem {
  to: string;
  key: string;
  icon: LucideIcon;
}

const NAV_GROUPS: Array<{ key: "personal" | "system"; items: SettingsNavItem[] }> = [
  {
    key: "personal",
    items: [
      { to: "/settings/profile", key: "profile", icon: UserRound },
      { to: "/settings/security", key: "security", icon: ShieldCheck },
      { to: "/settings/preferences", key: "preferences", icon: Palette },
    ],
  },
  {
    key: "system",
    items: [
      { to: "/settings/tutorial", key: "tutorial", icon: BookOpen },
      { to: "/settings/providers", key: "providers", icon: KeyRound },
      { to: "/settings/git", key: "git", icon: GitBranch },
      { to: "/settings/runtime", key: "runtime", icon: Cpu },
      { to: "/settings/about", key: "about", icon: Info },
    ],
  },
];

export function SettingsLayout() {
  const { t } = useTranslation();
  return (
    <div className="space-y-5 panel-enter">
      <PageHeader
        icon={Settings}
        title={t("settings:title")}
        description={t("settings:description")}
      />
      <div className="flex flex-col gap-5 lg:flex-row lg:items-start">
        <nav
          aria-label={t("settings:nav.aria")}
          className="command-panel flex shrink-0 gap-4 overflow-x-auto p-3 lg:sticky lg:top-24 lg:w-60 lg:flex-col lg:gap-5 lg:overflow-visible"
        >
          {NAV_GROUPS.map((group) => (
            <section key={group.key} className="min-w-40">
              <p className="mb-1.5 px-3 font-mono text-[9px] font-semibold uppercase tracking-[0.18em] text-muted-foreground/70">
                {t(`settings:nav.groups.${group.key}`)}
              </p>
              <div className="flex gap-1 lg:flex-col">
                {group.items.map(({ to, key, icon: Icon }) => (
                  <NavLink
                    key={key}
                    to={to}
                    className={({ isActive }) =>
                      cn(
                        "flex min-h-10 shrink-0 items-center gap-2.5 rounded-xl px-3 text-xs transition-colors",
                        isActive
                          ? "bg-primary/10 font-medium text-foreground"
                          : "text-muted-foreground hover:bg-surface-interactive hover:text-foreground",
                      )
                    }
                  >
                    <Icon className="h-4 w-4 shrink-0" />
                    <span className="whitespace-nowrap">{t(`settings:nav.${key}`)}</span>
                  </NavLink>
                ))}
              </div>
            </section>
          ))}
        </nav>
        <div className="min-w-0 flex-1">
          <Outlet />
        </div>
      </div>
    </div>
  );
}
