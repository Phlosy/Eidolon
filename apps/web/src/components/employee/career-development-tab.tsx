import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useCareerMutations, useCareerReadiness, useCareerOverview } from "../../hooks/useCareer";
import { useVacantSlots } from "../../hooks/useOrganizations";
import { Badge } from "../common/badge";
import { Skeleton } from "../common/skeleton";
import type { CareerNextPosition, CareerOverview } from "../../types";

const READINESS_VARIANT: Record<string, "success" | "info" | "warning" | "muted" | "danger"> = {
  READY: "success",
  NEAR_READY: "info",
  DEVELOPMENT_NEEDED: "warning",
  NEEDS_EVIDENCE: "muted",
  CRITICAL_GAPS: "danger",
};

/** Employee Detail → 职业发展 tab：Overview / Next Positions / Plans / Timeline / Promote。 */
export function CareerDevelopmentTab({ employeeId }: { employeeId: number }) {
  const { t } = useTranslation();
  const overviewQuery = useCareerOverview(employeeId);
  const mutations = useCareerMutations(employeeId);
  const [selectedNext, setSelectedNext] = useState<CareerNextPosition | null>(null);

  if (overviewQuery.isLoading) return <Skeleton className="h-40 w-full" />;
  if (overviewQuery.isError || !overviewQuery.data) {
    return <p className="text-sm text-destructive">{employeeId}</p>;
  }
  const overview: CareerOverview = overviewQuery.data;

  return (
    <div className="space-y-5" data-testid="career-development-tab">
      <header>
        <h3 className="text-sm font-semibold">{t("employee:career.careerTitle")}</h3>
        <p className="text-xs text-muted-foreground">
          {t("employee:career.currentPosition")}: {overview.current_position?.name ?? "—"}
        </p>
      </header>

      <section>
        <h4 className="mb-2 text-xs font-medium text-muted-foreground">
          {t("employee:career.nextPositions")}
        </h4>
        <ul className="space-y-2">
          {overview.next_positions.map((next) => (
            <li
              key={next.target_position.id}
              className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-border px-3 py-2"
              data-testid={`next-${next.target_position.code}`}
            >
              <span className="text-sm font-medium">{next.target_position.name}</span>
              <Badge variant={READINESS_VARIANT[next.readiness_status] ?? "muted"}>
                {next.readiness_status}
              </Badge>
              <span className="font-mono text-xs text-muted-foreground">
                {t("employee:career.fit")}:{" "}
                {next.position_fit.known_fit_score === null
                  ? "—"
                  : `${Math.round(next.position_fit.known_fit_score * 100)}%`}{" "}
                · {t("employee:career.confidence")}:{" "}
                {next.position_fit.fit_confidence === null
                  ? "—"
                  : `${Math.round(next.position_fit.fit_confidence * 100)}%`}
              </span>
              {next.required_gaps.length > 0 ? (
                <span className="text-[11px] text-warning">
                  {t("employee:career.requiredGaps")}: {next.required_gaps.join(", ")}
                </span>
              ) : null}
              {next.uncertainties.length > 0 ? (
                <span className="text-[11px] text-muted-foreground">
                  {t("employee:career.uncertainties")}: {next.uncertainties.join(", ")}
                </span>
              ) : null}
              <span className="ml-auto flex gap-2">
                <button
                  onClick={() => mutations.createPlan.mutate(next.target_position.id)}
                  className="rounded-md border border-border px-2 py-1 text-[11px] hover:bg-muted"
                >
                  {t("employee:career.createPlan")}
                </button>
                <button
                  onClick={() => setSelectedNext(next)}
                  className="rounded-md border border-primary/40 px-2 py-1 text-[11px] text-primary hover:bg-primary/5"
                >
                  {t("employee:career.promote")}
                </button>
              </span>
            </li>
          ))}
          {overview.next_positions.length === 0 ? (
            <p className="text-xs text-muted-foreground">—</p>
          ) : null}
        </ul>
      </section>

      <section>
        <h4 className="mb-2 text-xs font-medium text-muted-foreground">
          {t("employee:career.plansTitle")}
        </h4>
        <ul className="space-y-2">
          {overview.plans.map((plan) => (
            <li key={plan.id} className="rounded-md border border-border px-3 py-2">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm">{plan.title}</span>
                <Badge variant="muted">{plan.status}</Badge>
                <span className="ml-auto font-mono text-xs text-muted-foreground">
                  {plan.completed_count}/{plan.item_count}
                </span>
              </div>
            </li>
          ))}
          {overview.plans.length === 0 ? <p className="text-xs text-muted-foreground">—</p> : null}
        </ul>
      </section>

      <section>
        <h4 className="mb-2 text-xs font-medium text-muted-foreground">
          {t("employee:career.timelineTitle")}
        </h4>
        <ul className="space-y-1 text-xs text-muted-foreground">
          {overview.timeline.slice(0, 20).map((event, index) => (
            <li
              key={`${event.at}-${index}`}
              className="flex flex-wrap justify-between gap-2 border-b border-border/40 py-1 last:border-b-0"
            >
              <span>
                {event.type} · {event.title}
              </span>
              <span className="font-mono">
                {event.at ? new Date(event.at).toLocaleDateString() : "—"}
              </span>
            </li>
          ))}
        </ul>
      </section>

      {selectedNext ? (
        <PromoteModal
          employeeId={employeeId}
          next={selectedNext}
          onClose={() => setSelectedNext(null)}
        />
      ) : null}
    </div>
  );
}

function PromoteModal({
  employeeId,
  next,
  onClose,
}: {
  employeeId: number;
  next: CareerNextPosition;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const readinessQuery = useCareerReadiness(employeeId, next.target_position.id);
  const vacanciesQuery = useVacantSlots();
  const mutations = useCareerMutations(employeeId);
  const [slotId, setSlotId] = useState(0);
  const [reason, setReason] = useState("");
  const vacancies = (vacanciesQuery.data ?? ([] as Record<string, unknown>[])).filter(
    (slot) =>
      slot["position_definition_id"] === next.target_position.id &&
      slot["occupancy_status"] === "vacant",
  );
  const readiness = readinessQuery.data;
  const highRisk =
    readiness?.readiness_status === "CRITICAL_GAPS" ||
    readiness?.readiness_status === "NEEDS_EVIDENCE";

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md space-y-3 rounded-lg border border-border bg-background p-4 shadow-xl"
        onClick={(event) => event.stopPropagation()}
      >
        <h4 className="text-sm font-semibold">
          {t("employee:career.promotePreview")} · {next.target_position.name}
        </h4>
        {readiness ? (
          <div className="space-y-1 text-xs text-muted-foreground">
            <p>
              {t("employee:career.readiness")}:{" "}
              <Badge variant={READINESS_VARIANT[readiness.readiness_status] ?? "muted"}>
                {readiness.readiness_status}
              </Badge>
            </p>
            <p className="font-mono">
              {t("employee:career.fit")}:{" "}
              {readiness.position_fit.known_fit_score === null
                ? "—"
                : `${Math.round(readiness.position_fit.known_fit_score * 100)}%`}{" "}
              · {t("employee:career.confidence")}:{" "}
              {readiness.position_fit.fit_confidence === null
                ? "—"
                : `${Math.round(readiness.position_fit.fit_confidence * 100)}%`}
            </p>
            {readiness.required_gaps.length ? (
              <p>
                {t("employee:career.requiredGaps")}: {readiness.required_gaps.join(", ")}
              </p>
            ) : null}
          </div>
        ) : null}
        {highRisk ? (
          <p className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-600 dark:text-amber-400">
            {t("employee:career.highRisk")}
          </p>
        ) : null}
        <select
          value={slotId}
          onChange={(event) => setSlotId(Number(event.target.value))}
          className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-xs"
        >
          <option value={0}>—</option>
          {vacancies.map((slot) => (
            <option key={Number(slot["id"])} value={Number(slot["id"])}>
              {String(slot["slot_code"])}
            </option>
          ))}
        </select>
        <input
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          placeholder="reason"
          className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-xs"
        />
        <div className="flex justify-end gap-2">
          <button onClick={onClose} className="rounded-md border border-border px-3 py-1.5 text-xs">
            {t("employee:career.cancel")}
          </button>
          <button
            disabled={!slotId}
            onClick={() => {
              mutations.promote.mutate({
                target_definition_id: next.target_position.id,
                slot_id: slotId,
                reason,
              });
              onClose();
            }}
            className="rounded-md bg-primary px-3 py-1.5 text-xs text-primary-foreground disabled:opacity-40"
            data-testid="confirm-promote"
          >
            {t("employee:career.confirmPromote")}
          </button>
        </div>
      </div>
    </div>
  );
}
