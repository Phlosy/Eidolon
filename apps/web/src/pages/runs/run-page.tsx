import { Bot, Search } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router-dom";
import { runs as runsApi } from "../../api";
import type { RunEvent } from "../../api/types";
import { TraceDialog } from "../../components/app/trace-dialog";
import { ErrorNote, IdChip, KeyValue, Section, StatusBadge } from "../../components/app/widgets";
import { Button } from "../../components/common/button";
import { Input } from "../../components/common/input";
import { ErrorState, PageHeader } from "../../components/common/states";
import { Skeleton } from "../../components/common/skeleton";
import { useCurrentCompany } from "../../features/company/company-context";
import { useCommand, useCompanyQuery, useRun } from "../../hooks/queries";
import { formatDateTime, formatMicro } from "../../utils/format";

export function RunPage() {
  const { t } = useTranslation("app");
  const { id = "" } = useParams();
  const me = useCurrentCompany();
  const company = useCompanyQuery(me.company_id);
  const run = useRun(id);
  const [trace, setTrace] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const cancel = useCommand(() => runsApi.cancel(id, reason));
  if (run.isLoading) return <Skeleton className="h-64 w-full" />;
  if (run.error || !run.data)
    return <ErrorState error={run.error} onRetry={() => void run.refetch()} />;
  const r = run.data;
  const running = r.status === "running";
  return (
    <>
      <PageHeader
        title={`${t("run.title")} ${r.run_id.slice(0, 8)}`}
        description={`${r.person_id ?? "—"} · ${r.model ?? "—"}`}
        icon={Bot}
        actions={<StatusBadge status={r.status} />}
      />
      <div className="grid gap-6 xl:grid-cols-[2fr_3fr]">
        <div className="space-y-6">
          <Section title={t("run.status")}>
            <KeyValue
              items={[
                [
                  t("run.person"),
                  r.person_id ? (
                    <Link
                      key="p"
                      to={`/employees/${encodeURIComponent(r.person_id)}`}
                      className="hover:underline"
                    >
                      {r.person_id}
                    </Link>
                  ) : (
                    "—"
                  ),
                ],
                [t("run.task"), <IdChip key="t" id={r.task_id} />],
                [t("run.steps"), String(r.steps_completed)],
                [t("run.usage"), `${r.usage.input_tokens ?? 0} / ${r.usage.output_tokens ?? 0}`],
                [t("run.spend"), formatMicro(r.spend_micro, company.data?.currency)],
                [
                  t("common.at"),
                  `${formatDateTime(r.started_at)} → ${r.ended_at ? formatDateTime(r.ended_at) : "…"}`,
                ],
                [
                  t("run.cause"),
                  r.cause_fact_id ? (
                    <button
                      key="c"
                      type="button"
                      className="font-mono text-[11px] text-primary hover:underline"
                      onClick={() => setTrace(r.cause_fact_id)}
                    >
                      {r.cause_fact_id.slice(0, 8)} · {t("run.trace")}
                    </button>
                  ) : (
                    "—"
                  ),
                ],
                [t("run.error"), r.error ?? "—"],
              ]}
            />
          </Section>
          {running && me.role === "owner" ? (
            <Section title={t("run.cancel")}>
              <form
                className="flex gap-2"
                onSubmit={(event) => {
                  event.preventDefault();
                  cancel.mutate(undefined);
                }}
              >
                <Input
                  aria-label={t("run.cancelReason")}
                  placeholder={t("run.cancelReason")}
                  required
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                />
                <Button variant="destructive" type="submit" disabled={cancel.isPending}>
                  {t("run.cancel")}
                </Button>
              </form>
              <ErrorNote error={cancel.error} />
            </Section>
          ) : null}
        </div>
        <Section title={`${t("run.events")} (${r.events.length})`}>
          <ol className="scroll-area max-h-[70vh] space-y-2 overflow-auto pr-1">
            {r.events.map((e) => (
              <EventRow key={e.fact_id} event={e} onTrace={() => setTrace(e.fact_id)} />
            ))}
          </ol>
        </Section>
      </div>
      <TraceDialog factId={trace} onClose={() => setTrace(null)} />
    </>
  );
}

function summarize(e: RunEvent): string {
  const p = e.payload;
  switch (e.kind) {
    case "agent.step.requested":
      return `step ${p.step_no} attempt ${p.attempt}`;
    case "agent.step.completed":
      return `step ${p.step_no} ${p.stop_reason} tools=[${((p.tool_calls as Array<{ name: string }>) ?? []).map((c) => c.name).join(", ")}] ${String(p.text_preview ?? "").slice(0, 80)}`;
    case "agent.tool.requested":
      return `${p.name} ${p.call_id}`;
    case "agent.tool.completed":
      return `${p.name} ${p.is_error ? "ERR " : ""}${String(p.result_preview ?? "").slice(0, 80)}`;
    case "agent.model.called": {
      const usage = (p.usage as Record<string, number>) ?? {};
      const cost = (p.cost as { amount_micro?: number; currency?: string } | null) ?? null;
      return `${p.provider}/${p.model} ${p.stop_reason} in=${usage.input_tokens ?? 0} out=${usage.output_tokens ?? 0}${cost ? ` cost=${cost.amount_micro}µ${cost.currency}` : ""} mode=${p.mode}`;
    }
    case "money.cost.recorded":
      return p.posted
        ? `booked ${(p.booked as { amount_micro: number; currency: string }).amount_micro}µ`
        : `not booked: ${p.reason}`;
    case "money.entry.posted":
      return String(p.memo ?? "");
    case "agent.run.started":
      return `${p.person_id} · ${p.model}`;
    default:
      return JSON.stringify(
        Object.fromEntries(Object.entries(p).filter(([k]) => k !== "run_id")),
      ).slice(0, 120);
  }
}

function EventRow({ event, onTrace }: { event: RunEvent; onTrace: () => void }) {
  return (
    <li className="rounded-lg border border-border px-3 py-2 text-xs">
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium">{event.kind}</span>
        <span className="flex items-center gap-2 text-muted-foreground">
          {formatDateTime(event.at)}
          <button
            type="button"
            className="rounded p-1 hover:bg-muted"
            onClick={onTrace}
            aria-label="trace"
          >
            <Search className="h-3.5 w-3.5" />
          </button>
        </span>
      </div>
      <p className="mt-1 break-words font-mono text-[11px] text-muted-foreground">
        {summarize(event)}
      </p>
    </li>
  );
}
