import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { AlertTriangle, ArrowUpRight, Boxes, Flag, ListChecks, ShieldCheck, UserRound } from "lucide-react";
import { useProject } from "../../hooks/useProjects";
import type { Employee, Project } from "../../types";
import { deriveProjectProgress } from "../../utils/company-metrics";
import { enumLabel } from "../../utils/labels";
import { PROJECT_STATUS_VARIANT } from "../../utils/status";
import { Badge } from "../common/badge";
import { ProgressTrack } from "../game/progress-track";

export function ProjectCommandCard({ project, owner }: { project: Project; owner?: Employee }) {
  const { t } = useTranslation();
  const detail = useProject(project.id).data;
  const progress = deriveProjectProgress(detail?.tasks ?? []);
  const blockers = detail?.tasks.filter((task) => task.status === "failed" || task.status === "rejected").length ?? 0;
  const activeMilestone = detail?.milestones.find((milestone) => milestone.status === "in_progress") ?? detail?.milestones.find((milestone) => milestone.status === "pending");
  const health = Math.max(0, 100 - blockers * 30);
  return <Link to={`/projects/${project.id}`} className="group command-panel relative block overflow-hidden p-5 transition duration-200 hover:-translate-y-1 hover:border-border-active"><div className="absolute right-0 top-0 h-32 w-32 bg-[radial-gradient(circle_at_top_right,var(--status-researching-glow),transparent_68%)]" /><div className="relative flex items-start justify-between gap-4"><Badge variant={PROJECT_STATUS_VARIANT[project.status]}>{enumLabel(t, "project:status", project.status)}</Badge><ArrowUpRight className="h-4 w-4 text-muted-foreground opacity-0 transition group-hover:opacity-100" /></div><div className="relative mt-5"><p className="type-kicker text-primary">{t("project:command.mission", { id: project.id })}</p><h2 className="mt-2 line-clamp-2 text-xl font-semibold tracking-tight">{project.name}</h2><p className="mt-2 line-clamp-2 min-h-10 text-xs leading-5 text-muted-foreground">{project.goal ?? project.description ?? t("project:command.noBrief")}</p><div className="mt-5 flex items-center justify-between text-[10px] text-muted-foreground"><span>{t("project:command.missionProgress")}</span><span className="type-telemetry text-sm font-semibold text-primary">{progress.percent}%</span></div><ProgressTrack value={progress.percent} className="mt-2" /><div className="mt-5 grid grid-cols-2 gap-2"><Metric icon={UserRound} label={t("project:command.owner")} value={owner?.name ?? t("project:command.unassigned")} /><Metric icon={Flag} label={t("project:command.milestone")} value={activeMilestone?.name ?? t("project:command.noMilestone")} /><Metric icon={ListChecks} label={t("project:command.tasks")} value={`${progress.completed}/${progress.total}`} /><Metric icon={Boxes} label={t("project:command.artifacts")} value={String(detail?.artifacts.length ?? 0)} /></div><div className="mt-4 flex items-center justify-between rounded-xl border border-border bg-background/45 px-3 py-2.5"><span className="flex items-center gap-2 text-[10px] text-muted-foreground">{blockers > 0 ? <AlertTriangle className="h-3.5 w-3.5 text-danger" /> : <ShieldCheck className="h-3.5 w-3.5 text-success" />}{t("project:command.healthDerived")}</span><span className={blockers > 0 ? "type-telemetry text-xs font-semibold text-warning" : "type-telemetry text-xs font-semibold text-success"}>{health}%</span></div></div></Link>;
}

function Metric({ icon: Icon, label, value }: { icon: typeof UserRound; label: string; value: string }) {
  return <div className="min-w-0 rounded-xl border border-border bg-background/45 p-3"><Icon className="h-3.5 w-3.5 text-primary" /><p className="mt-3 text-[9px] text-muted-foreground">{label}</p><p className="mt-1 truncate text-xs font-medium" title={value}>{value}</p></div>;
}
