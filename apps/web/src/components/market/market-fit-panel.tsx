import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";
import { Badge } from "../common/badge";
import { useMarketFit } from "../../hooks/useMarket";
import { usePositionProfiles } from "../../hooks/usePositionProfiles";
import type { MarketFitEvaluation } from "../../api/market";

const selectClass =
  "w-full rounded-md border border-border bg-transparent px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

function pct(value: number | null | undefined): string {
  return value == null ? "—" : `${Math.round(value * 100)}%`;
}

function EvaluationRow({ item }: { item: MarketFitEvaluation }) {
  const { t } = useTranslation();
  return (
    <li
      data-testid={`fit-requirement-${item.code}`}
      className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-border px-3 py-2"
    >
      <div className="min-w-0">
        <span className="text-xs font-medium">{item.name}</span>
        {item.critical ? (
          <Badge variant="warning" className="ml-2">
            critical
          </Badge>
        ) : null}
        <p className="mt-0.5 text-[11px] text-muted-foreground">
          {t("market:fit.candidate")}:{" "}
          <span className="font-mono">
            {item.candidate_score ?? t("market:fit.notEvaluated")}
            {item.candidate_confidence != null ? ` · ${pct(item.candidate_confidence)}` : ""}
          </span>
          {item.minimum_score != null
            ? ` · ${t("market:fit.minimum", { value: item.minimum_score })}`
            : ""}
          {item.target_score != null
            ? ` · ${t("market:fit.target", { value: item.target_score })}`
            : ""}
        </p>
      </div>
      <Badge variant={item.is_unknown ? "muted" : item.is_strength ? "success" : "warning"}>
        {item.is_unknown ? t("market:fit.notEvaluated") : item.evaluation_status}
      </Badge>
    </li>
  );
}

/**
 * 候选人 × 职位 的 Fit 面板（T2.5 公开读面）：score 与 confidence **并列**，
 * 未评估如实显示"未评估"（不是 0）；未知项不判为不合格（Unknown ≠ Bad）。
 */
export function MarketFitPanel({
  listingId,
  positionDefinitionId,
  onPickPosition,
}: {
  listingId: number;
  positionDefinitionId: number | null;
  onPickPosition: (positionDefinitionId: number | null) => void;
}) {
  const { t } = useTranslation();
  const profiles = usePositionProfiles();
  const positions = (profiles.data ?? []).filter((profile) => profile.profile_status === "active");
  const fit = useMarketFit(listingId, positionDefinitionId);

  return (
    <section
      className="space-y-3 rounded-2xl border border-border bg-card p-4 shadow-card"
      data-testid="market-fit-panel"
    >
      <h3 className="text-sm font-semibold">{t("market:fit.title")}</h3>
      <select
        className={selectClass}
        data-testid="market-fit-position"
        value={positionDefinitionId ?? ""}
        onChange={(event) =>
          onPickPosition(event.target.value === "" ? null : Number(event.target.value))
        }
      >
        <option value="">{t("market:filters.positionAll")}</option>
        {positions.map((profile) => (
          <option key={profile.position_definition_id} value={profile.position_definition_id}>
            {profile.name} ({profile.code})
          </option>
        ))}
      </select>
      <p className="text-[11px] leading-4 text-muted-foreground">
        {t("market:filters.positionHint")}
      </p>

      {positionDefinitionId == null ? (
        <p className="text-xs text-muted-foreground">{t("market:fit.pickPosition")}</p>
      ) : fit.isLoading ? (
        <p className="flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          {t("common:loading")}
        </p>
      ) : fit.isError || !fit.data ? (
        <p className="text-xs text-danger">{t("market:fit.error")}</p>
      ) : (
        <div className="space-y-3">
          <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {[
              {
                label: t("market:fit.score"),
                value: pct(fit.data.known_fit_score),
              },
              {
                label: t("market:fit.confidence"),
                value: pct(fit.data.fit_confidence),
              },
              {
                label: t("market:fit.coverage"),
                value: pct(fit.data.requirement_coverage),
              },
              {
                label: t("market:fit.known", {
                  known: fit.data.known_count,
                  total: fit.data.total_count,
                }),
                value: fit.data.fit_status,
              },
            ].map((cell) => (
              <div key={cell.label} className="rounded-xl border border-border/60 px-3 py-2">
                <dt className="text-[10px] uppercase tracking-wide text-muted-foreground">
                  {cell.label}
                </dt>
                <dd className="mt-0.5 font-mono text-sm font-semibold">{cell.value}</dd>
              </div>
            ))}
          </dl>

          <p className="text-xs font-medium">
            <Badge
              variant={
                fit.data.fit_status === "STRONG_MATCH"
                  ? "success"
                  : fit.data.fit_status === "INSUFFICIENT_DATA" ||
                      fit.data.fit_status === "NOT_EVALUABLE"
                    ? "muted"
                    : "warning"
              }
            >
              {t(`market:fit.status.${fit.data.fit_status}`, {
                defaultValue: fit.data.fit_status,
              })}
            </Badge>
          </p>

          {fit.data.strengths.length > 0 ? (
            <div>
              <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                {t("market:fit.strengths")}
              </h4>
              <ul className="mt-1 space-y-1.5">
                {fit.data.strengths.map((item) => (
                  <EvaluationRow key={`s-${item.code}`} item={item} />
                ))}
              </ul>
            </div>
          ) : null}

          {fit.data.gaps.length > 0 ? (
            <div>
              <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                {t("market:fit.gaps")}
              </h4>
              <ul className="mt-1 space-y-1.5">
                {fit.data.gaps.map((item) => (
                  <EvaluationRow key={`g-${item.code}`} item={item} />
                ))}
              </ul>
            </div>
          ) : null}

          {fit.data.uncertainties.length > 0 ? (
            <div>
              <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                {t("market:fit.uncertainties")}
              </h4>
              <ul className="mt-1 space-y-1.5">
                {fit.data.uncertainties.map((item) => (
                  <EvaluationRow key={`u-${item.code}`} item={item} />
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      )}
    </section>
  );
}
