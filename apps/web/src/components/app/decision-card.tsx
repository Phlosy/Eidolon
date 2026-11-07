import { useState } from "react";
import { useTranslation } from "react-i18next";
import { decisions as decisionsApi } from "../../api";
import type { Decision } from "../../api/types";
import { useCommand } from "../../hooks/queries";
import { formatDateTime } from "../../utils/format";
import { Button } from "../common/button";
import { Input } from "../common/input";
import { ErrorNote, KeyValue, Mono, StatusBadge } from "./widgets";

/**
 * One decision waiting for a human. Signing restates the bound action the server
 * recorded, so what you approve is exactly what will happen.
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
      <div className="mt-3">
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
