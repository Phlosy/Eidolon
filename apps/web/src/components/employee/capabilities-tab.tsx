import { useTranslation } from "react-i18next";
import {
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

function CompetencyRows({ t, items }: { t: T; items: EmployeeCompetencyView[] }) {
  if (items.length === 0) {
    return <p className="text-xs text-muted-foreground">{t("employee:capabilities.empty")}</p>;
  }
  return (
    <ul className="space-y-1.5">
      {items.map((item) => (
        <li
          key={item.competency_definition_id}
          className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border px-3 py-2"
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
        <CompetencyRows t={t} items={capabilities.general} />
      </section>

      <section className="rounded-md border border-border p-4">
        <h3 className="text-sm font-medium">{t("employee:capabilities.professionalTitle")}</h3>
        <p className="mt-1 mb-3 text-xs text-muted-foreground">
          {t("employee:capabilities.professionalHint")}
        </p>
        <CompetencyRows t={t} items={capabilities.professional} />
      </section>

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
