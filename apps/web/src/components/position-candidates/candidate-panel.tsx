import { useState } from "react";
import { useTranslation } from "react-i18next";
import { usePositionCandidates, useAssignEmployee } from "../../hooks/useTalentRoster";
import { useEmployeePositionFit } from "../../hooks/usePositionFit";
import { useVacantSlots } from "../../hooks/useOrganizations";
import { Badge } from "../common/badge";
import { Skeleton } from "../common/skeleton";
import type { CandidateItem } from "../../types";

const BAND_VARIANT: Record<string, "success" | "info" | "warning" | "muted" | "danger"> = {
  RECOMMENDED: "success",
  VIABLE: "info",
  DEVELOPMENTAL: "warning",
  NEEDS_EVIDENCE: "muted",
  CRITICAL_GAP: "danger",
};

/**
 * Position Detail → 候选人才：band 分组、候选卡、比较（≤4）、分配预览（仍由人决定）。
 */
export function CandidatePanel({
  positionId,
  positionName,
}: {
  positionId: number;
  positionName: string;
}) {
  const { t } = useTranslation();
  const [includeAssigned, setIncludeAssigned] = useState(false);
  const [compareIds, setCompareIds] = useState<number[]>([]);
  const [assignTarget, setAssignTarget] = useState<CandidateItem | null>(null);
  const candidatesQuery = usePositionCandidates(positionId, includeAssigned);
  const vacanciesQuery = useVacantSlots();
  const assignMutation = useAssignEmployee();

  if (candidatesQuery.isLoading) return <Skeleton className="h-40 w-full" />;
  if (candidatesQuery.isError || !candidatesQuery.data) {
    return <p className="text-sm text-destructive">{t("position:positions.unavailable")}</p>;
  }
  const data = candidatesQuery.data;
  const vacancies = (vacanciesQuery.data ?? ([] as Record<string, unknown>[])).filter(
    (slot) =>
      slot["position_definition_id"] === positionId && slot["occupancy_status"] === "vacant",
  );

  const toggleCompare = (employeeId: number) => {
    setCompareIds((current) =>
      current.includes(employeeId)
        ? current.filter((id) => id !== employeeId)
        : current.length >= 4
          ? current
          : [...current, employeeId],
    );
  };

  return (
    <div className="space-y-4" data-testid="candidate-panel">
      <header className="flex flex-wrap items-center gap-3">
        <h3 className="text-sm font-semibold">{t("position:positions.candidates")}</h3>
        <label className="flex items-center gap-1 text-xs">
          <input
            type="checkbox"
            checked={includeAssigned}
            onChange={(event) => setIncludeAssigned(event.target.checked)}
          />
          {t("position:positions.includeAssigned")}
        </label>
      </header>

      {!data.evaluable ? (
        <p className="text-xs text-muted-foreground">{t("position:positions.notConfigured")}</p>
      ) : null}

      {data.bands.map((group) => (
        <section key={group.band}>
          <h4 className="mb-2 flex items-center gap-2 text-xs font-medium text-muted-foreground">
            <Badge variant={BAND_VARIANT[group.band] ?? "muted"} data-testid={`band-${group.band}`}>
              {group.band}
            </Badge>
            <span>{group.count}</span>
          </h4>
          <ul className="space-y-1.5">
            {group.candidates.map((item) => (
              <li
                key={item.employee.employee_id}
                className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-border px-3 py-2"
              >
                <input
                  type="checkbox"
                  checked={compareIds.includes(item.employee.employee_id)}
                  onChange={() => toggleCompare(item.employee.employee_id)}
                  aria-label={`compare ${item.employee.name}`}
                />
                <span className="text-sm font-medium">{item.employee.name}</span>
                <span className="text-[11px] text-muted-foreground">
                  {item.employee.current_position?.name ?? t("employee:roster.statusAvailable")}
                </span>
                <span className="ml-auto flex flex-wrap items-center gap-2 font-mono text-xs">
                  <span>
                    Fit:{" "}
                    {item.fit.known_fit_score === null
                      ? "—"
                      : `${Math.round(item.fit.known_fit_score * 100)}%`}
                  </span>
                  <span>
                    Conf:{" "}
                    {item.fit.fit_confidence === null
                      ? "—"
                      : `${Math.round(item.fit.fit_confidence * 100)}%`}
                  </span>
                  <span>Coverage: {Math.round(item.fit.required_coverage * 100)}%</span>
                </span>
                <button
                  onClick={() => setAssignTarget(item)}
                  className="rounded-md border border-border px-2 py-1 text-[11px] hover:bg-muted"
                  data-testid={`assign-${item.employee.slug}`}
                >
                  {t("position:positions.assign")}
                </button>
              </li>
            ))}
          </ul>
        </section>
      ))}

      {compareIds.length >= 2 ? (
        <CandidateCompare positionId={positionId} employeeIds={compareIds} />
      ) : null}

      {assignTarget && vacancies.length > 0 ? (
        <AssignModal
          candidate={assignTarget}
          positionName={positionName}
          slots={vacancies}
          onClose={() => setAssignTarget(null)}
          onAssign={(slotId) =>
            assignMutation.mutate({
              employeeId: assignTarget.employee.employee_id,
              slotId,
            })
          }
        />
      ) : null}
    </div>
  );
}

function CandidateCompare({
  positionId,
  employeeIds,
}: {
  positionId: number;
  employeeIds: number[];
}) {
  const { t } = useTranslation();
  const details = employeeIds.map((employeeId) => ({
    employeeId,
    query: useEmployeePositionFit(employeeId, positionId),
  }));
  const first = details.find((d) => d.query.data)?.query.data;
  if (!first) return <Skeleton className="h-32 w-full" />;
  const codes = first.requirement_evaluations.map((item) => item.code);
  return (
    <section
      className="overflow-x-auto rounded-md border border-border p-3"
      data-testid="candidate-compare"
    >
      <h4 className="mb-2 text-xs font-medium">{t("position:positions.compareTitle")}</h4>
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-border text-muted-foreground">
            <th className="py-1 text-left">{t("position:positions.requirement")}</th>
            {details.map((d) => (
              <th key={d.employeeId} className="py-1 text-right">
                {d.query.data ? String(d.employeeId) : "—"}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {codes.map((code) => (
            <tr key={code} className="border-b border-border/40">
              <td className="py-1 font-mono">{code}</td>
              {details.map((d) => {
                const item = d.query.data?.requirement_evaluations.find((x) => x.code === code);
                const score = item?.employee_score ?? null;
                const confidence = item?.employee_confidence ?? null;
                return (
                  <td
                    key={d.employeeId}
                    className="py-1 text-right font-mono"
                    data-testid={`cell-${code}`}
                  >
                    {score === null
                      ? "—"
                      : `${score}${confidence !== null ? ` (${Math.round(confidence * 100)}%)` : ""}`}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function AssignModal({
  candidate,
  positionName,
  slots,
  onClose,
  onAssign,
}: {
  candidate: CandidateItem;
  positionName: string;
  slots: Array<Record<string, unknown>>;
  onClose: () => void;
  onAssign: (slotId: number) => void;
}) {
  const { t } = useTranslation();
  const [slotId, setSlotId] = useState(slots[0]?.["id"] ? Number(slots[0]["id"]) : 0);
  const fit = candidate.fit;
  const warning =
    fit.qualification_status === "NOT_QUALIFIED"
      ? t("position:positions.assignWarningCritical")
      : fit.qualification_status === "INSUFFICIENT_DATA"
        ? t("position:positions.assignWarningEvidence")
        : fit.fit_status === "INSUFFICIENT_DATA"
          ? t("position:positions.assignWarningEvidence")
          : null;
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
          {t("position:positions.assignPreview")} · {candidate.employee.name} → {positionName}
        </h4>
        <p className="text-xs text-muted-foreground">
          {t("position:positions.fit")}:{" "}
          {fit.known_fit_score === null ? "—" : `${Math.round(fit.known_fit_score * 100)}%`} ·{" "}
          {t("position:positions.fitConfidence")}:{" "}
          {fit.fit_confidence === null ? "—" : `${Math.round(fit.fit_confidence * 100)}%`}
        </p>
        {warning ? (
          <p className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-600 dark:text-amber-400">
            {warning}
          </p>
        ) : null}
        <select
          value={slotId}
          onChange={(event) => setSlotId(Number(event.target.value))}
          className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-xs"
        >
          {slots.map((slot) => (
            <option key={Number(slot["id"])} value={Number(slot["id"])}>
              {String(slot["slot_code"])}
            </option>
          ))}
        </select>
        <div className="flex justify-end gap-2">
          <button onClick={onClose} className="rounded-md border border-border px-3 py-1.5 text-xs">
            {t("position:positions.cancel")}
          </button>
          <button
            onClick={() => {
              onAssign(slotId);
              onClose();
            }}
            className="rounded-md bg-primary px-3 py-1.5 text-xs text-primary-foreground"
            data-testid="confirm-assign"
          >
            {t("position:positions.stillAssign")}
          </button>
        </div>
      </div>
    </div>
  );
}
