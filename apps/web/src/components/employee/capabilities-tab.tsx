import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  useCompetencyExplanation,
  useEmployeeCapabilities,
  useEmployeeCompetencyEvidence,
  useEmployeeTraits,
} from "../../hooks/useCompetencies";
import { Badge } from "../common/badge";
import { ErrorState } from "../common/states";
import { Skeleton } from "../common/skeleton";
import type { EmployeeCompetencyView, TraitView } from "../../types";

type T = ReturnType<typeof useTranslation>["t"];

function trendLabel(item: EmployeeCompetencyView): string {
  if (item.trend_direction === "unknown" || item.trend === null) return "";
  const arrow = item.trend_direction === "up" ? "↑" : item.trend_direction === "down" ? "↓" : "→";
  return `${arrow} ${item.trend > 0 ? "+" : ""}${item.trend}`;
}

function scoreDisplay(t: T, item: EmployeeCompetencyView) {
  if (item.score === null) {
    return <Badge variant="muted">{t("employee:capabilities.unrated")}</Badge>;
  }
  const confidence = item.confidence === null ? null : `${Math.round(item.confidence * 100)}%`;
  const trend = trendLabel(item);
  return (
    <span className="font-mono text-sm">
      {item.score}
      {confidence !== null ? (
        <span className="ml-2 text-xs text-muted-foreground">
          {t("employee:capabilities.confidence", { value: confidence })}
        </span>
      ) : null}
      {trend ? <span className="ml-2 text-xs text-muted-foreground">{trend}</span> : null}
      <span className="ml-2 text-xs text-muted-foreground">
        {t("employee:capabilities.evidenceCount", { count: item.evidence_count })}
      </span>
    </span>
  );
}

function CompetencyRows({
  t,
  items,
  onSelect,
  selectedCode,
}: {
  t: T;
  items: EmployeeCompetencyView[];
  onSelect: (item: EmployeeCompetencyView) => void;
  selectedCode: string | null;
}) {
  if (items.length === 0) {
    return <p className="text-xs text-muted-foreground">{t("employee:capabilities.empty")}</p>;
  }
  return (
    <ul className="space-y-1.5">
      {items.map((item) => (
        <li
          key={item.competency_definition_id}
          role="button"
          tabIndex={0}
          onClick={() => onSelect(item)}
          onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === " ") onSelect(item);
          }}
          data-testid={`competency-row-${item.code}`}
          className={[
            "flex cursor-pointer flex-wrap items-center justify-between gap-2 rounded-md border px-3 py-2 transition-colors",
            selectedCode === item.code
              ? "border-primary/40 bg-primary/5"
              : "border-border hover:bg-muted/50",
          ].join(" ")}
        >
          <div>
            <span className="text-sm font-medium">{item.name}</span>
            <span className="ml-2 font-mono text-[11px] text-muted-foreground">{item.code}</span>
          </div>
          {scoreDisplay(t, item)}
        </li>
      ))}
    </ul>
  );
}

/** 能力详情（解释层）：为什么是这个分。 */
function ExplanationPanel({
  employeeId,
  competency,
  onClose,
}: {
  employeeId: number;
  competency: string;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const explanationQuery = useCompetencyExplanation(employeeId, competency);

  if (explanationQuery.isLoading) {
    return <Skeleton className="h-40 w-full" />;
  }
  if (explanationQuery.isError || !explanationQuery.data) {
    return <ErrorState error={explanationQuery.error} onRetry={() => explanationQuery.refetch()} />;
  }
  const data = explanationQuery.data;
  const history = data.assessment_history;
  const trend =
    data.trend_direction === "unknown" || data.trend === null
      ? ""
      : `${data.trend_direction === "up" ? "↑" : data.trend_direction === "down" ? "↓" : "→"} ${
          data.trend > 0 ? "+" : ""
        }${data.trend}`;
  return (
    <div className="rounded-md border border-primary/30 bg-primary/5 p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h4 className="text-sm font-semibold">
            {t("employee:capabilities.detailTitle")} · {data.name}{" "}
            <span className="font-mono text-[11px] text-muted-foreground">{data.code}</span>
          </h4>
          <p className="mt-1 text-xs text-muted-foreground">
            {data.domain_name} · {data.assessment_history.length}{" "}
            {t("employee:capabilities.historyCount")} · {data.relevant_skills.length}{" "}
            {t("employee:capabilities.skillsTitle")}
          </p>
        </div>
        <button
          onClick={onClose}
          className="rounded-md border border-border px-2 py-1 text-xs text-muted-foreground hover:bg-muted"
        >
          {t("employee:capabilities.close")}
        </button>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
        <div className="rounded-md border border-border/60 px-3 py-2">
          <p className="text-[11px] uppercase tracking-wide text-muted-foreground">
            {t("employee:capabilities.score")}
          </p>
          <p className="mt-0.5 font-mono text-lg font-semibold">
            {data.score === null ? t("employee:capabilities.unrated") : data.score}
          </p>
        </div>
        <div className="rounded-md border border-border/60 px-3 py-2">
          <p className="text-[11px] uppercase tracking-wide text-muted-foreground">
            {t("employee:capabilities.confidenceShort")}
          </p>
          <p className="mt-0.5 font-mono text-lg font-semibold">
            {data.confidence === null ? "—" : `${Math.round(data.confidence * 100)}%`}
          </p>
        </div>
        <div className="rounded-md border border-border/60 px-3 py-2">
          <p className="text-[11px] uppercase tracking-wide text-muted-foreground">
            {t("employee:capabilities.trend")}
          </p>
          <p className="mt-0.5 font-mono text-lg font-semibold">{trend || "→"}</p>
        </div>
        <div className="rounded-md border border-border/60 px-3 py-2">
          <p className="text-[11px] uppercase tracking-wide text-muted-foreground">
            {t("employee:capabilities.evidenceShort")}
          </p>
          <p className="mt-0.5 font-mono text-lg font-semibold">{data.evidence_count}</p>
        </div>
      </div>

      {history.length > 0 ? (
        <div className="mt-4">
          <h5 className="text-xs font-medium text-muted-foreground">
            {t("employee:capabilities.assessmentHistory")}
          </h5>
          <ul className="mt-1 space-y-1 text-xs">
            {history.slice(0, 5).map((row) => (
              <li
                key={row.run_id}
                className="flex flex-wrap justify-between gap-2 border-b border-border/40 py-1 last:border-b-0"
              >
                <span className="text-muted-foreground">
                  {row.profile_code ?? "—"} v{row.profile_version ?? "?"} ·{" "}
                  {new Date(row.created_at).toLocaleDateString()}
                </span>
                <span className="font-mono">
                  {row.score === null ? "—" : row.score}
                  {row.trend != null
                    ? row.trend >= 0
                      ? ` (+${row.trend})`
                      : ` (${row.trend})`
                    : ""}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {data.source_distribution.length > 0 ? (
        <div className="mt-4">
          <h5 className="text-xs font-medium text-muted-foreground">
            {t("employee:capabilities.sources")}
          </h5>
          <p className="mt-1 font-mono text-xs">
            {data.source_distribution.map((s) => `${s.source_kind}=${s.count}`).join("  ")}
          </p>
        </div>
      ) : null}

      {data.recent_evidence.length > 0 ? (
        <div className="mt-4">
          <h5 className="text-xs font-medium text-muted-foreground">
            {t("employee:capabilities.recentEvidence")}
          </h5>
          <ul className="mt-1 space-y-1 text-xs text-muted-foreground">
            {data.recent_evidence.slice(0, 5).map((row) => (
              <li
                key={row.id}
                className="flex flex-wrap justify-between gap-2 border-b border-border/40 py-1 last:border-b-0"
              >
                <span>{row.source_ref}</span>
                <span className="font-mono">
                  {row.signal ?? "—"} · {row.source_kind}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

function TraitRows({ t, traits }: { t: T; traits: TraitView[] }) {
  return (
    <ul className="space-y-1.5">
      {traits.map((trait) => (
        <li
          key={trait.code}
          className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border px-3 py-2"
        >
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium">{trait.label}</span>
              <span className="font-mono text-[11px] text-muted-foreground">{trait.code}</span>
              {!trait.affects_execution ? (
                <Badge variant="muted">{t("employee:capabilities.noBehaviorEffect")}</Badge>
              ) : null}
            </div>
            <p className="mt-0.5 text-xs text-muted-foreground">{trait.description}</p>
          </div>
          <span className="font-mono text-sm">{trait.display}%</span>
        </li>
      ))}
    </ul>
  );
}

/** P5 人物面板基础三块：Traits（8 维）/ 通用能力 / 专业能力（读面，无写入口）。 */
export function CapabilitiesTab({ employeeId }: { employeeId: number }) {
  const { t } = useTranslation();
  const [selected, setSelected] = useState<{ code: string; name: string } | null>(null);
  const capabilitiesQuery = useEmployeeCapabilities(employeeId);
  const traitsQuery = useEmployeeTraits(employeeId);
  const evidenceQuery = useEmployeeCompetencyEvidence(employeeId);

  if (capabilitiesQuery.isLoading || traitsQuery.isLoading) {
    return <Skeleton className="h-64 w-full" />;
  }
  const failed = capabilitiesQuery.isError || traitsQuery.isError || evidenceQuery.isError;
  if (failed) {
    const error = capabilitiesQuery.error ?? traitsQuery.error ?? evidenceQuery.error;
    return <ErrorState error={error} onRetry={() => capabilitiesQuery.refetch()} />;
  }
  const capabilities = capabilitiesQuery.data;
  const traits = traitsQuery.data ?? [];
  if (!capabilities) {
    return <Skeleton className="h-32 w-full" />;
  }
  const evidenceRows = evidenceQuery.data ?? [];

  return (
    <div className="space-y-5" data-tutorial-target="employee-capabilities-tab">
      <section className="rounded-md border border-border p-4">
        <h3 className="text-sm font-medium">{t("employee:capabilities.traitsTitle")}</h3>
        <p className="mt-1 mb-3 text-xs text-muted-foreground">
          {t("employee:capabilities.traitsHint")}
        </p>
        <TraitRows t={t} traits={traits} />
      </section>

      <section className="rounded-md border border-border p-4">
        <h3 className="text-sm font-medium">{t("employee:capabilities.generalTitle")}</h3>
        <p className="mt-1 mb-3 text-xs text-muted-foreground">
          {t("employee:capabilities.generalHint")}
        </p>
        <CompetencyRows
          t={t}
          items={capabilities.general}
          onSelect={(item) => setSelected({ code: item.code, name: item.name })}
          selectedCode={selected?.code ?? null}
        />
      </section>

      <section className="rounded-md border border-border p-4">
        <h3 className="text-sm font-medium">{t("employee:capabilities.professionalTitle")}</h3>
        <p className="mt-1 mb-3 text-xs text-muted-foreground">
          {t("employee:capabilities.professionalHint")}
        </p>
        <CompetencyRows
          t={t}
          items={capabilities.professional}
          onSelect={(item) => setSelected({ code: item.code, name: item.name })}
          selectedCode={selected?.code ?? null}
        />
      </section>

      {selected ? (
        <ExplanationPanel
          employeeId={employeeId}
          competency={selected.code}
          onClose={() => setSelected(null)}
        />
      ) : null}

      {evidenceRows.length > 0 ? (
        <section className="rounded-md border border-border p-4">
          <h3 className="text-sm font-medium">{t("employee:capabilities.evidenceTitle")}</h3>
          <p className="mt-1 mb-3 text-xs text-muted-foreground">
            {t("employee:capabilities.evidenceHint")}
          </p>
          <ul className="space-y-1 text-xs text-muted-foreground">
            {evidenceRows.slice(0, 20).map((row) => (
              <li
                key={row.id}
                className="flex flex-wrap justify-between gap-2 border-b border-border/50 py-1 last:border-b-0"
              >
                <span>
                  {row.competency_name} · {row.source_ref}
                </span>
                <span className="font-mono">
                  {row.signal !== null ? `${row.signal}` : "—"} · {row.source_kind}
                </span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}
