import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useCompetencyExplanation } from "../../hooks/useCompetencies";
import { Badge } from "../common/badge";
import type { FitRequirementEvaluation, PositionFitResult } from "../../types";

const STATUS_VARIANT: Record<string, "success" | "info" | "muted" | "warning" | "danger"> = {
  STRONG_MATCH: "success",
  PARTIAL_MATCH: "info",
  WEAK_MATCH: "warning",
  CRITICAL_GAP: "danger",
  INSUFFICIENT_DATA: "muted",
  NOT_EVALUABLE: "muted",
};

const EVAL_VARIANT: Record<string, "success" | "info" | "muted" | "warning" | "danger"> = {
  MEETS_TARGET: "success",
  MEETS_MINIMUM: "info",
  BELOW_MINIMUM: "warning",
  INSUFFICIENT_CONFIDENCE: "info",
  UNRATED: "muted",
};

/** 员工值 overlay 的迷你标尺（只出现在 Fit 分析里；P7 编辑器不显示员工值）。 */
function FitScale({ evaluation }: { evaluation: FitRequirementEvaluation }) {
  const positions = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100];
  const marker = evaluation.employee_score;
  return (
    <div className="relative mt-1 h-2 w-32 overflow-hidden rounded-full border border-border/60 bg-muted/40">
      {positions.map((position) => (
        <span
          key={position}
          className="absolute inset-y-0 w-px bg-border/40"
          style={{ left: `${position}%` }}
        />
      ))}
      {evaluation.minimum_score !== null ? (
        <span
          className="absolute inset-y-0 w-0.5 bg-amber-500/70"
          style={{ left: `${evaluation.minimum_score}%` }}
        />
      ) : null}
      {evaluation.target_score !== null ? (
        <span
          className="absolute inset-y-0 w-0.5 bg-emerald-500/70"
          style={{ left: `${evaluation.target_score}%` }}
        />
      ) : null}
      {marker !== null ? (
        <span
          className="absolute inset-y-0 w-1 bg-primary"
          style={{ left: `${Math.max(0, Math.min(100, marker))}%` }}
          title={`employee ${marker}`}
          data-testid="employee-marker"
        />
      ) : null}
    </div>
  );
}

function EvaluationRow({
  evaluation,
  onSelect,
}: {
  evaluation: FitRequirementEvaluation;
  onSelect: () => void;
}) {
  const { t } = useTranslation();
  return (
    <li
      role="button"
      tabIndex={0}
      onClick={onSelect}
      className="flex cursor-pointer flex-wrap items-center gap-x-4 gap-y-1 rounded-md border border-border px-3 py-2 hover:bg-muted/40"
      data-testid={`fit-row-${evaluation.code}`}
    >
      <div className="min-w-[9rem]">
        <span className="text-sm font-medium">{evaluation.name}</span>
        <span className="ml-2 font-mono text-[11px] text-muted-foreground">{evaluation.code}</span>
      </div>
      <FitScale evaluation={evaluation} />
      <div className="ml-auto flex flex-wrap items-center gap-2">
        <span className="font-mono text-xs">
          {evaluation.employee_score === null
            ? t("position:positions.unrated")
            : `${evaluation.employee_score}${evaluation.employee_confidence !== null ? ` (${Math.round(evaluation.employee_confidence * 100)}%)` : ""}`}
        </span>
        <Badge
          variant={EVAL_VARIANT[evaluation.evaluation_status] ?? "muted"}
          data-testid={`eval-${evaluation.evaluation_status}`}
        >
          {evaluation.evaluation_status}
        </Badge>
      </div>
    </li>
  );
}

function Drilldown({
  employeeId,
  evaluation,
}: {
  employeeId: number;
  evaluation: FitRequirementEvaluation;
}) {
  const { t } = useTranslation();
  const explanationQuery = useCompetencyExplanation(employeeId, evaluation.code);
  const explanation = explanationQuery.data;
  return (
    <div className="mt-1 rounded-md border border-primary/30 bg-primary/5 p-3 text-xs text-muted-foreground">
      <p>
        <span className="font-medium text-foreground">{evaluation.name}</span> ·{" "}
        {t("position:positions.minimum")} {evaluation.minimum_score ?? "—"} ·{" "}
        {t("position:positions.target")} {evaluation.target_score ?? "—"}
        {evaluation.minimum_confidence !== null
          ? ` · ${t("position:positions.minConfidence")} ${Math.round(evaluation.minimum_confidence * 100)}%`
          : ""}
      </p>
      {explanation ? (
        <p className="mt-1">
          {t("position:positions.score")}: {explanation.score ?? t("position:positions.unrated")} ·{" "}
          {t("position:positions.confidence")}:{" "}
          {explanation.confidence === null ? "—" : `${Math.round(explanation.confidence * 100)}%`} ·{" "}
          {t("position:positions.evidence")}: {explanation.evidence_count} ·{" "}
          {t("position:positions.history")}: {explanation.assessment_history.length}
        </p>
      ) : null}
    </div>
  );
}

/** P8 Fit 分析（一对一，只读展示）。低覆盖度时以 INSUFFICIENT_DATA 为主状态。 */
export function FitAnalysis({
  result,
  employeeId,
}: {
  result: PositionFitResult;
  employeeId: number;
}) {
  const { t } = useTranslation();
  const [selected, setSelected] = useState<FitRequirementEvaluation | null>(null);
  const variants = STATUS_VARIANT[result.fit_status] ?? "muted";
  const lowCoverage = result.fit_status === "INSUFFICIENT_DATA";

  return (
    <div className="space-y-4" data-testid="fit-analysis">
      <header className="flex flex-wrap items-center gap-3">
        <h3 className="text-sm font-semibold">
          {t("position:positions.fitTitle")} · {result.position_code}{" "}
          {result.profile_version ? `v${result.profile_version}` : ""}
        </h3>
        <Badge variant={variants} data-testid="fit-status">
          {result.fit_status}
        </Badge>
        <Badge variant="muted" data-testid="qualification">
          {result.qualification_status}
        </Badge>
      </header>

      {lowCoverage ? (
        <p className="rounded-md border border-border bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
          {t("position:positions.insufficientData")}
        </p>
      ) : null}

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Metric
          label={t("position:positions.fit")}
          value={
            result.known_fit_score === null ? "—" : `${Math.round(result.known_fit_score * 100)}%`
          }
        />
        <Metric
          label={t("position:positions.fitConfidence")}
          value={
            result.fit_confidence === null ? "—" : `${Math.round(result.fit_confidence * 100)}%`
          }
        />
        <Metric
          label={t("position:positions.requiredCoverage")}
          value={`${Math.round(result.required_coverage * 100)}%`}
        />
        <Metric
          label={t("position:positions.knownCount")}
          value={`${result.known_count}/${result.total_count}`}
        />
      </div>

      <section>
        <h4 className="mb-2 text-xs font-medium text-muted-foreground">
          {t("position:positions.strength")} · {result.strengths.length}
        </h4>
        <ul className="space-y-1">
          {result.strengths.map((item) => (
            <li key={item.requirement_id}>
              <EvaluationRow evaluation={item} onSelect={() => setSelected(item)} />
            </li>
          ))}
          {result.strengths.length === 0 ? (
            <p className="text-xs text-muted-foreground">—</p>
          ) : null}
        </ul>
      </section>

      <section>
        <h4 className="mb-2 text-xs font-medium text-muted-foreground">
          {t("position:positions.gap")} · {result.gaps.length}
        </h4>
        <ul className="space-y-1">
          {result.gaps.map((item) => (
            <li key={item.requirement_id}>
              <EvaluationRow evaluation={item} onSelect={() => setSelected(item)} />
            </li>
          ))}
        </ul>
      </section>

      <section>
        <h4 className="mb-2 text-xs font-medium text-muted-foreground">
          {t("position:positions.uncertainty")} · {result.uncertainties.length}
        </h4>
        <ul className="space-y-1">
          {result.uncertainties.map((item) => (
            <li key={item.requirement_id}>
              <EvaluationRow evaluation={item} onSelect={() => setSelected(item)} />
            </li>
          ))}
          {result.uncertainties.length === 0 ? (
            <p className="text-xs text-muted-foreground">—</p>
          ) : null}
        </ul>
      </section>

      {result.development_opportunities.length > 0 ? (
        <section>
          <h4 className="mb-2 text-xs font-medium text-muted-foreground">
            {t("position:positions.developmentOpportunity")} ·{" "}
            {result.development_opportunities.length}
          </h4>
          <ul className="space-y-1">
            {result.development_opportunities.map((item) => (
              <li key={item.requirement_id}>
                <EvaluationRow evaluation={item} onSelect={() => setSelected(item)} />
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {selected ? <Drilldown employeeId={employeeId} evaluation={selected} /> : null}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-border/60 px-3 py-2">
      <p className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className="mt-0.5 font-mono text-lg font-semibold" data-testid="fit-metric">
        {value}
      </p>
    </div>
  );
}
