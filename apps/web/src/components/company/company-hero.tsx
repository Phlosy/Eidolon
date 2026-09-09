import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Activity, BriefcaseBusiness, Play, Plus, Radio, Users } from "lucide-react";
import type { Company } from "../../types";
import type { DashboardStats } from "../../hooks/useDashboardStats";
import { LevelBadge, ProgressTrack } from "../game/progress-track";
import { Panel } from "../shared/panel";
import { Body, Caption, Display, Overline, Telemetry } from "../typography/text";

/**
 * 公司舞台横幅：整个首页的视觉重心。
 *
 * 只回答三件事：我在经营一家什么公司、现在到哪一级了、下一步去哪。
 * 其余指标收进 3 个紧凑状态片，明细交给下方的系统状态抽屉。
 */
export function CompanyHero({
  company,
  stats,
  runtimeHealth,
}: {
  company?: Company;
  stats: DashboardStats;
  runtimeHealth: number;
}) {
  const { t } = useTranslation();
  const p = stats.companyProgress;
  const chips = [
    [
      Users,
      t("dashboard:stats.employeesOnline"),
      `${stats.employeesOnline}/${stats.employeesTotal}`,
    ],
    [BriefcaseBusiness, t("dashboard:stats.activeProjects"), String(stats.activeProjects)],
    [Activity, t("dashboard:stats.tasksInProgress"), String(stats.tasksInProgress)],
  ] as const;
  return (
    <Panel className="panel-enter min-h-[320px] border-primary/20 p-6 md:p-9">
      <div className="absolute -right-24 -top-36 h-80 w-80 rounded-full bg-primary/10 blur-3xl" />
      <div className="absolute bottom-0 right-0 h-40 w-1/2 bg-[radial-gradient(circle_at_bottom_right,var(--status-working-glow),transparent_65%)]" />
      <div className="relative flex h-full flex-col justify-between gap-8">
        <div className="flex flex-wrap items-center gap-3">
          <LevelBadge level={p.level} label={t("dashboard:hero.companyLevel")} />
          <span className="inline-flex items-center gap-2 rounded-lg border border-success/25 bg-success/8 px-2 py-1 font-mono text-[10px] font-semibold uppercase tracking-[0.12em] text-success">
            <Radio className="h-3 w-3" />
            {t("dashboard:hero.operational")}
          </span>
          <Caption tone="muted">{t("dashboard:hero.derived")}</Caption>
        </div>

        <div>
          <Overline tone="primary">{t("dashboard:hero.commandCenter")}</Overline>
          <Display className="mt-3 max-w-3xl uppercase">
            {company?.name ?? t("nav:defaultCompany")}
          </Display>
          <Body tone="muted" className="mt-4 max-w-xl">
            {company?.description ?? t("dashboard:hero.description")}
          </Body>
        </div>

        <div className="flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
          <div className="max-w-xl flex-1">
            <div className="mb-2 flex items-center justify-between">
              <Overline tone="muted">{t("dashboard:hero.companyExperience")}</Overline>
              <Telemetry>
                {p.currentLevelXp}/{p.nextLevelXp} XP
              </Telemetry>
            </div>
            <ProgressTrack value={p.percent} />
            <Caption tone="muted" className="mt-2">
              {t("dashboard:hero.experienceSource", { xp: p.xp })}
            </Caption>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            {chips.map(([Icon, label, value]) => (
              <div
                key={label}
                title={label}
                className="flex h-12 items-center gap-2.5 rounded-2xl border border-border bg-background/50 px-3.5"
              >
                <Icon className="h-4 w-4 text-primary" />
                <Telemetry className="text-lg font-semibold">{value}</Telemetry>
                <Caption tone="muted" className="hidden sm:block">
                  {label}
                </Caption>
              </div>
            ))}
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <Link
            to="/office"
            className="inline-flex h-11 items-center gap-2 rounded-xl bg-primary px-5 text-sm font-semibold text-primary-foreground shadow-[var(--glow-primary)] transition hover:brightness-105"
          >
            <Play className="h-4 w-4" />
            {t("dashboard:hero.enterOffice")}
          </Link>
          <Link
            to="/projects"
            className="inline-flex h-11 items-center gap-2 rounded-xl border border-border bg-background/50 px-4 text-sm font-medium text-foreground transition hover:bg-surface-interactive"
          >
            <Plus className="h-4 w-4" />
            {t("dashboard:hero.newProject")}
          </Link>
          <Caption tone="muted">
            {t("dashboard:hero.runtimeHealth")} · {runtimeHealth}%
          </Caption>
        </div>
      </div>
    </Panel>
  );
}
