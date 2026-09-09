import {
  AlertTriangle,
  ArrowRight,
  Check,
  Circle,
  FileCheck2,
  GitPullRequestArrow,
  ShieldCheck,
} from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import type { Employee, ProjectLifecycle, ProjectPhase, Task } from "../../types";
import { cn } from "../../utils/cn";
import { Button } from "../common/button";
import { Badge } from "../common/badge";

interface ProjectLifecycleBoardProps {
  lifecycle: ProjectLifecycle;
  onCompletePhase?: (phase: ProjectPhase) => void;
  completing?: boolean;
  tasks?: Task[];
  employees?: Employee[];
}

const phaseLabel: Record<string, string> = {
  initiation: "立项",
  requirements_analysis: "需求分析",
  requirements_review: "需求评审",
  system_design: "系统设计",
  system_design_review: "设计评审",
  development: "开发",
  internal_testing: "内部测试",
  user_acceptance_testing: "用户验收测试",
  acceptance_review: "验收评审",
  delivery: "交付",
  project_archive: "归档",
};

/**
 * 教程打光的锚点：phase_type -> target id。
 * internal_testing 与 user_acceptance_testing 共用一个 target，
 * registry 的"同 id 取第一个可见元素"策略保证选择结果稳定。
 */
const PHASE_TUTORIAL_TARGETS: Record<string, string | undefined> = {
  requirements_review: "review-requirements",
  system_design_review: "review-design",
  development: "phase-development",
  internal_testing: "phase-testing",
  user_acceptance_testing: "phase-testing",
  acceptance_review: "review-acceptance",
  delivery: "delivery-package",
};

function PhaseIcon({ phase }: { phase: ProjectPhase }) {
  if (phase.status === "completed") return <Check className="h-3.5 w-3.5" />;
  if (phase.status === "waiting_review") return <ShieldCheck className="h-3.5 w-3.5" />;
  if (phase.status === "changes_requested" || phase.status === "blocked") {
    return <AlertTriangle className="h-3.5 w-3.5" />;
  }
  return <Circle className="h-3.5 w-3.5" />;
}

export function ProjectLifecycleBoard({
  lifecycle,
  onCompletePhase,
  completing = false,
  tasks = [],
  employees = [],
}: ProjectLifecycleBoardProps) {
  const { t } = useTranslation("project");
  const activePhase = lifecycle.phases.find((phase) =>
    ["in_progress", "changes_requested", "waiting_review"].includes(phase.status),
  );
  const completedDefaultPhase = lifecycle.phases.find((phase) => phase.phase_type === "delivery");
  const completed = lifecycle.phases.filter((phase) => phase.status === "completed").length;
  const progress = Math.round((completed / Math.max(lifecycle.phases.length, 1)) * 100);
  const [selectedPhaseId, setSelectedPhaseId] = useState(
    activePhase?.id ?? completedDefaultPhase?.id ?? lifecycle.phases[0]?.id,
  );
  useEffect(() => {
    if (activePhase?.id) setSelectedPhaseId(activePhase.id);
  }, [activePhase?.id]);
  const selectedPhase = lifecycle.phases.find((phase) => phase.id === selectedPhaseId);
  const selectedTasks = tasks.filter((task) => task.phase_id === selectedPhaseId);
  const selectedDocuments = lifecycle.documents.filter(
    (document) => document.phase_id === selectedPhaseId,
  );
  const selectedReview = lifecycle.reviews.find((review) => review.phase_id === selectedPhaseId);

  return (
    <div className="space-y-5">
      <div className="grid gap-3 md:grid-cols-[1.35fr_.65fr]">
        <section className="rounded-2xl border border-border bg-background/40 p-4 md:p-5">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <p className="type-kicker text-primary">{t("lifecycleBoard.kicker")}</p>
              <h2 className="mt-2 text-xl font-semibold">
                {activePhase ? phaseLabel[activePhase.phase_type] : t("lifecycleBoard.completed")}
              </h2>
              <p className="mt-1 text-xs leading-5 text-muted-foreground">
                {activePhase?.status === "changes_requested"
                  ? t("lifecycleBoard.changesRequestedHint")
                  : t("lifecycleBoard.defaultHint")}
              </p>
            </div>
            <div className="text-right">
              <p className="type-telemetry text-2xl font-semibold">{progress}%</p>
              <p className="text-[10px] text-muted-foreground">
                {t("lifecycleBoard.phaseProgress", {
                  done: completed,
                  total: lifecycle.phases.length,
                })}
              </p>
            </div>
          </div>
          <div className="mt-4 h-1.5 overflow-hidden rounded-full bg-muted">
            <div
              className="h-full rounded-full bg-primary transition-[width]"
              style={{ width: `${progress}%` }}
            />
          </div>
          {activePhase && !activePhase.gate_required && onCompletePhase ? (
            <div className="mt-4 flex justify-end">
              <Button onClick={() => onCompletePhase(activePhase)} disabled={completing}>
                {completing ? t("lifecycleBoard.completing") : t("lifecycleBoard.completePhase")}
                <ArrowRight className="h-4 w-4" />
              </Button>
            </div>
          ) : null}
        </section>

        {lifecycle.pending_user_action ? (
          <section className="rounded-2xl border border-warning/35 bg-warning/8 p-4 md:p-5">
            <div className="flex items-center gap-2 text-warning">
              <ShieldCheck className="h-4 w-4" />
              <p className="type-kicker">{t("lifecycleBoard.actionRequired")}</p>
            </div>
            <h3 className="mt-3 text-lg font-semibold">{lifecycle.pending_user_action.title}</h3>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">
              {t("lifecycleBoard.actionRequiredHint")}
            </p>
            <Link
              className="mt-4 inline-flex h-11 w-full items-center justify-center gap-2 rounded-xl bg-primary px-4 text-sm font-medium text-primary-foreground shadow-[var(--glow-primary)] transition hover:brightness-105 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              to={`/projects/${lifecycle.project.id}/reviews/${lifecycle.pending_user_action.review_id}`}
            >
              {t("lifecycleBoard.enterReview")}
              <ArrowRight className="h-4 w-4" />
            </Link>
          </section>
        ) : (
          <section className="rounded-2xl border border-border bg-background/40 p-4 md:p-5">
            <p className="type-kicker text-muted-foreground">{t("lifecycleBoard.nextGate")}</p>
            <h3 className="mt-3 text-base font-semibold">
              {lifecycle.project.status === "completed"
                ? t("lifecycleBoard.projectCompleted")
                : activePhase?.gate_required
                  ? phaseLabel[activePhase.phase_type]
                  : t("lifecycleBoard.waitingTeam")}
            </h3>
          </section>
        )}
      </div>

      {lifecycle.role_coverage_warning ? (
        <div className="flex items-start gap-3 rounded-xl border border-warning/25 bg-warning/6 px-4 py-3 text-xs">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
          <div>
            <p className="font-medium">{t("lifecycleBoard.roleCoverageWarning")}</p>
            <p className="mt-1 text-muted-foreground">{lifecycle.role_coverage_warning}</p>
          </div>
        </div>
      ) : null}

      <ol
        className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4"
        aria-label={t("lifecycleBoard.phasesLabel")}
      >
        {lifecycle.phases.map((phase) => (
          <li
            key={phase.id}
            data-tutorial-target={PHASE_TUTORIAL_TARGETS[phase.phase_type]}
            className={cn(
              "relative rounded-xl border p-3 transition-colors",
              phase.status === "completed" && "border-success/25 bg-success/6",
              phase.status === "in_progress" && "border-primary/35 bg-primary/8",
              phase.status === "waiting_review" && "border-warning/35 bg-warning/8",
              phase.status === "changes_requested" && "border-destructive/35 bg-destructive/8",
              phase.status === "pending" && "border-border bg-background/25 text-muted-foreground",
            )}
          >
            <button
              type="button"
              className="w-full text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              aria-pressed={selectedPhaseId === phase.id}
              onClick={() => setSelectedPhaseId(phase.id)}
            >
              <div className="flex items-center gap-2">
                <span className="flex h-7 w-7 items-center justify-center rounded-full border border-current/20">
                  <PhaseIcon phase={phase} />
                </span>
                <span className="type-telemetry text-[9px]">
                  {String(phase.order + 1).padStart(2, "0")}
                </span>
                {phase.gate_required ? (
                  <Badge variant="warning">{t("lifecycleBoard.userGate")}</Badge>
                ) : null}
              </div>
              <p className="mt-3 text-sm font-medium text-foreground">
                {phaseLabel[phase.phase_type] ?? phase.name}
              </p>
              <p className="mt-1 text-[10px] uppercase tracking-[0.14em]">
                {t(`lifecycleBoard.phaseStatus.${phase.status}`, {
                  defaultValue: phase.status.replaceAll("_", " "),
                })}
              </p>
            </button>
          </li>
        ))}
      </ol>

      {selectedPhase ? (
        <section className="rounded-2xl border border-border bg-background/30 p-4 md:p-5">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <p className="type-kicker text-primary">{t("lifecycleBoard.phaseWorkspace")}</p>
              <h3 className="mt-2 text-lg font-semibold">
                {phaseLabel[selectedPhase.phase_type] ?? selectedPhase.name}
              </h3>
            </div>
            <Badge variant={selectedPhase.gate_required ? "warning" : "muted"}>
              {selectedPhase.gate_required
                ? t("lifecycleBoard.userGate")
                : t(`lifecycleBoard.phaseStatus.${selectedPhase.status}`, {
                    defaultValue: selectedPhase.status.replaceAll("_", " "),
                  })}
            </Badge>
          </div>
          <div className="mt-4 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            <div>
              <p className="text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
                {t("lifecycleBoard.tasks")}
              </p>
              {selectedTasks.length ? (
                <ul className="mt-2 space-y-1 text-xs">
                  {selectedTasks.map((task) => (
                    <li key={task.id}>{task.title}</li>
                  ))}
                </ul>
              ) : (
                <p className="mt-2 text-xs text-muted-foreground">{t("lifecycleBoard.noTasks")}</p>
              )}
            </div>
            <div>
              <p className="text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
                {t("lifecycleBoard.employees")}
              </p>
              <p className="mt-2 text-xs">
                {employees.find((employee) => employee.id === selectedPhase.owner_employee_id)
                  ?.name ?? t("lifecycleBoard.unassigned")}
              </p>
            </div>
            <div>
              <p className="text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
                {t("lifecycleBoard.documents")}
              </p>
              {selectedDocuments.length ? (
                <ul className="mt-2 space-y-1 text-xs">
                  {selectedDocuments.slice(0, 4).map((document) => (
                    <li key={document.id}>
                      {document.title} · {document.version_label}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="mt-2 text-xs text-muted-foreground">
                  {t("lifecycleBoard.noDocuments")}
                </p>
              )}
            </div>
            <div>
              <p className="text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
                {t("lifecycleBoard.reviewChanges")}
              </p>
              {selectedReview ? (
                <Link
                  className="mt-2 inline-flex items-center gap-1 text-xs text-primary hover:underline"
                  to={`/projects/${lifecycle.project.id}/reviews/${selectedReview.id}`}
                >
                  {t("lifecycleBoard.openReview", { title: selectedReview.title })}
                  <ArrowRight className="h-3 w-3" />
                </Link>
              ) : (
                <p className="mt-2 text-xs text-muted-foreground">
                  {t("lifecycleBoard.changeRecords", {
                    count: lifecycle.change_requests.length,
                  })}
                </p>
              )}
            </div>
          </div>
        </section>
      ) : null}

      <div className="grid gap-2 sm:grid-cols-3 lg:grid-cols-5">
        {Object.entries(lifecycle.coverage).map(([key, value]) => (
          <div key={key} className="rounded-xl border border-border bg-background/30 p-3">
            <div className="flex items-center justify-between gap-2">
              <span className="text-[10px] capitalize text-muted-foreground">
                {t(`lifecycleBoard.coverage.${key}`, { defaultValue: key })}
              </span>
              <span className="type-telemetry text-xs">{value}%</span>
            </div>
            <div className="mt-2 h-1 overflow-hidden rounded-full bg-muted">
              <div className="h-full bg-primary" style={{ width: `${value}%` }} />
            </div>
          </div>
        ))}
      </div>

      <div className="flex flex-wrap gap-2 text-[10px] text-muted-foreground">
        <span className="inline-flex items-center gap-1.5">
          <FileCheck2 className="h-3.5 w-3.5" />
          {t("lifecycleBoard.documentsCount", { count: lifecycle.documents.length })}
        </span>
        <span className="inline-flex items-center gap-1.5">
          <ShieldCheck className="h-3.5 w-3.5" />
          {t("lifecycleBoard.baselinesCount", { count: lifecycle.baselines.length })}
        </span>
        <span className="inline-flex items-center gap-1.5">
          <GitPullRequestArrow className="h-3.5 w-3.5" />
          {t("lifecycleBoard.changesCount", { count: lifecycle.change_requests.length })}
        </span>
      </div>
    </div>
  );
}
