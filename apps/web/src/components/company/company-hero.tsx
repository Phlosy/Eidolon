import { useTranslation } from "react-i18next";
import { Activity, Boxes, BriefcaseBusiness, CircleDollarSign, Radio, Users } from "lucide-react";
import type { Company } from "../../types";
import type { DashboardStats } from "../../hooks/useDashboardStats";
import { formatUsd } from "../../utils/format";
import { LevelBadge, ProgressTrack } from "../game/progress-track";
import { Panel } from "../shared/panel";

export function CompanyHero({ company, stats, runtimeHealth }: { company?: Company; stats: DashboardStats; runtimeHealth: number }) {
  const { t } = useTranslation();
  const p = stats.companyProgress;
  const metrics = [
    [Users, t("dashboard:stats.employeesOnline"), `${stats.employeesOnline}/${stats.employeesTotal}`],
    [BriefcaseBusiness, t("dashboard:stats.activeProjects"), stats.activeProjects],
    [Activity, t("dashboard:stats.tasksInProgress"), stats.tasksInProgress],
    [CircleDollarSign, t("dashboard:stats.runtimeCost"), formatUsd(stats.runtimeCostUsd)],
    [Boxes, t("dashboard:stats.artifacts"), stats.artifactsCount],
  ] as const;
  return <Panel className="panel-enter min-h-[340px] border-primary/20 p-6 md:p-8"><div className="absolute -right-24 -top-36 h-80 w-80 rounded-full bg-primary/10 blur-3xl" /><div className="absolute bottom-0 right-0 h-40 w-1/2 bg-[radial-gradient(circle_at_bottom_right,var(--status-working-glow),transparent_65%)]" /><div className="relative grid h-full gap-8 xl:grid-cols-[1.1fr_.9fr]">
    <div className="flex flex-col justify-between"><div><div className="flex flex-wrap items-center gap-3"><LevelBadge level={p.level} label={t("dashboard:hero.companyLevel")} /><span className="inline-flex items-center gap-2 rounded-lg border border-success/25 bg-success/8 px-2 py-1 font-mono text-[10px] font-semibold uppercase tracking-[0.12em] text-success"><Radio className="h-3 w-3" />{t("dashboard:hero.operational")}</span><span className="text-[10px] text-muted-foreground">{t("dashboard:hero.derived")}</span></div><p className="type-kicker mt-7 text-primary">{t("dashboard:hero.commandCenter")}</p><h1 className="type-display mt-3 max-w-3xl uppercase">{company?.name ?? t("nav:defaultCompany")}</h1><p className="mt-4 max-w-xl text-sm leading-6 text-muted-foreground">{company?.description ?? t("dashboard:hero.description")}</p></div><div className="mt-8 max-w-xl"><div className="mb-2 flex items-center justify-between text-[10px]"><span className="font-mono uppercase tracking-[0.12em] text-muted-foreground">{t("dashboard:hero.companyExperience")}</span><span className="type-telemetry">{p.currentLevelXp}/{p.nextLevelXp} XP</span></div><ProgressTrack value={p.percent} /><p className="mt-2 font-mono text-[9px] text-muted-foreground">{t("dashboard:hero.experienceSource", { xp: p.xp })}</p></div></div>
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-2">{metrics.map(([Icon, label, value], index) => <div key={label} className={index === 0 ? "col-span-2 rounded-2xl border border-success/20 bg-success/7 p-5 sm:col-span-1 xl:col-span-2" : "rounded-2xl border border-border bg-background/45 p-4"}><div className="flex items-center justify-between"><Icon className={index === 0 ? "h-4 w-4 text-success" : "h-4 w-4 text-primary"} />{index === 0 ? <span className="h-2 w-2 rounded-full bg-success status-pulse" /> : null}</div><p className={index === 0 ? "type-telemetry mt-7 text-4xl font-semibold" : "type-telemetry mt-5 text-2xl font-semibold"}>{value}</p><p className="mt-1 text-[10px] text-muted-foreground">{label}</p></div>)}<div className="rounded-2xl border border-border bg-background/45 p-4"><Activity className="h-4 w-4 text-primary" /><p className="type-telemetry mt-5 text-2xl font-semibold">{runtimeHealth}%</p><p className="mt-1 text-[10px] text-muted-foreground">{t("dashboard:hero.runtimeHealth")}</p></div></div>
  </div></Panel>;
}
