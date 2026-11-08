import { useState } from "react";
import { useTranslation } from "react-i18next";
import { decisions as decisionsApi } from "../../api";
import type { Brief, Decision } from "../../api/types";
import { useCommand } from "../../hooks/queries";
import { formatDateTime } from "../../utils/format";
import { Button } from "../common/button";
import { Input } from "../common/input";
import { ErrorNote, KeyValue, Mono, StatusBadge } from "./widgets";

/**
 * One decision waiting for a human. The company's own recommendation comes first, in
 * plain words (R7-a); the mechanics — bound action, ids — fold under "Details". Signing
 * restates the bound action the server recorded, so what you approve is exactly what
 * will happen.
 */
export function DecisionCard({ decision }: { decision: Decision }) {
  const { t } = useTranslation("app");
  const [reason, setReason] = useState("");
  const sign = useCommand((verdict: string) =>
    decisionsApi.sign(decision.decision_id, {
      verdict,
      reason: reason || null,
      action: decision.subject.action,
    }),
  );
  const needsReason = decision.resolution.require_reason && !reason.trim();
  const subject = Object.fromEntries(
    Object.entries(decision.subject).filter(([k]) => !["action", "action_hash"].includes(k)),
  );
  return (
    <article className="command-panel p-4" data-testid="decision-card">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">{decision.gate}</h3>
        <StatusBadge status={decision.status} />
      </div>
      <BriefPanel brief={decision.brief ?? null} />
      <details className="mt-3 text-xs">
        <summary className="cursor-pointer text-muted-foreground">{t("decision.details")}</summary>
        <div className="mt-2">
          <KeyValue
            items={[
              [t("decision.requested"), formatDateTime(decision.requested_at)],
              [t("decision.proposer"), decision.proposer ?? "—"],
              [
                t("decision.action"),
                <Mono key="a">{JSON.stringify(decision.subject.action ?? subject)}</Mono>,
              ],
            ]}
          />
        </div>
      </details>
      {decision.signatures.length ? (
        <ul className="mt-3 space-y-1 text-xs text-muted-foreground">
          {decision.signatures.map((s) => (
            <li key={s.person_id}>
              {t("decision.signed")}: {s.person_id} ({s.role}) — {s.verdict}
              {s.reason ? ` · ${s.reason}` : ""}
            </li>
          ))}
        </ul>
      ) : null}
      {decision.status === "pending" ? (
        <form
          className="mt-4 space-y-2"
          onSubmit={(event) => {
            event.preventDefault();
            sign.mutate("approve");
          }}
        >
          <Input
            aria-label={t("decision.reason")}
            placeholder={t("decision.reason")}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
          {needsReason ? (
            <p className="text-[11px] text-warning">{t("decision.reasonRequired")}</p>
          ) : null}
          <div className="flex flex-wrap gap-2">
            <Button size="sm" type="submit" disabled={sign.isPending || needsReason}>
              {t("decision.approve")}
            </Button>
            <Button
              size="sm"
              type="button"
              variant="outline"
              disabled={sign.isPending || needsReason}
              onClick={() => sign.mutate("rework")}
            >
              {t("decision.rework")}
            </Button>
            <Button
              size="sm"
              type="button"
              variant="destructive"
              disabled={sign.isPending || needsReason}
              onClick={() => sign.mutate("reject")}
            >
              {t("decision.reject")}
            </Button>
          </div>
          <ErrorNote error={sign.error} />
        </form>
      ) : null}
      {sign.data?.applied ? (
        <p className="mt-3 text-xs text-success">
          {t("decision.applied", { what: sign.data.applied })}
        </p>
      ) : null}
    </article>
  );
}

const TONE: Record<string, string> = {
  approve: "border-success/40 bg-success/10 text-success",
  reject: "border-destructive/40 bg-destructive/10 text-destructive",
  rework: "border-warning/40 bg-warning/10 text-warning",
};

function BriefPanel({ brief }: { brief: Brief | null }) {
  const { t } = useTranslation("app");
  if (!brief) {
    return <p className="mt-3 text-xs text-muted-foreground">{t("decision.briefNone")}</p>;
  }
  const tone = TONE[brief.recommendation] ?? "border-border text-foreground";
  return (
    <section className="mt-3 space-y-2" data-testid="decision-brief">
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <span>
          {t("decision.briefBy", { who: brief.advisor ?? "—", role: brief.advisor_role ?? "—" })}
        </span>
        <span className={`rounded border px-2 py-0.5 text-[11px] font-semibold ${tone}`}>
          {t(`decision.recommend.${brief.recommendation}`, brief.recommendation)}
        </span>
      </div>
      <p className="text-sm">{brief.summary}</p>
      {brief.rationale ? (
        <p className="text-xs text-muted-foreground">
          <span className="font-medium text-foreground">{t("decision.briefWhy")}: </span>
          {brief.rationale}
        </p>
      ) : null}
      <BriefList label={t("decision.briefRisks")} items={brief.risks} />
      <BriefList label={t("decision.briefAlternatives")} items={brief.alternatives} />
      {brief.cost ? (
        <p className="text-xs text-muted-foreground">
          <span className="font-medium text-foreground">{t("decision.briefCost")}: </span>
          {brief.cost}
        </p>
      ) : null}
    </section>
  );
}

function BriefList({ label, items }: { label: string; items: string[] }) {
  if (!items.length) return null;
  return (
    <div className="text-xs text-muted-foreground">
      <span className="font-medium text-foreground">{label}: </span>
      <ul className="ml-4 list-disc">
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </div>
  );
}
