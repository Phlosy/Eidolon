import { UserRound } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router-dom";
import { IdChip, KeyValue, Section, StatusBadge } from "../../components/app/widgets";
import { ErrorState, PageHeader } from "../../components/common/states";
import { Skeleton } from "../../components/common/skeleton";
import { useCurrentCompany } from "../../features/company/company-context";
import { useCompanyQuery, usePerson, useRuns } from "../../hooks/queries";
import { formatDateTime, formatMicro, formatRelativeTime } from "../../utils/format";

export function PersonPage() {
  const { t } = useTranslation("app");
  const { personId = "" } = useParams();
  const me = useCurrentCompany();
  const company = useCompanyQuery(me.company_id);
  const person = usePerson(personId);
  const runs = useRuns(me.company_id, personId);
  if (person.isLoading) return <Skeleton className="h-64 w-full" />;
  if (person.error || !person.data)
    return <ErrorState error={person.error} onRetry={() => void person.refetch()} />;
  const p = person.data;
  const currency = company.data?.currency;
  return (
    <>
      <PageHeader
        title={p.name}
        description={`${p.person_id} · ${t(`employees.${p.kind}`)} · ${t("employees.evidence", { count: p.evidence_count })} · ${t("employees.spend")}: ${formatMicro(p.spend_micro, currency)}`}
        icon={UserRound}
      />
      <div className="grid gap-6 xl:grid-cols-[2fr_3fr]">
        <div className="space-y-6">
          <Section title={t("employees.competencies")}>
            {p.competencies.length === 0 ? (
              <p className="text-xs text-muted-foreground">{t("employees.unrated")}</p>
            ) : (
              <ul className="space-y-2">
                {p.competencies.map((c) => (
                  <li key={c.area}>
                    <div className="flex items-center justify-between text-xs">
                      <span className="font-medium">{c.area}</span>
                      <span className="font-mono">
                        {c.rated ? c.score : t("employees.unrated")} · +{c.positive ?? 0}/−
                        {c.negative ?? 0} · {c.evidence_count}
                      </span>
                    </div>
                    <div className="mt-1 h-1.5 w-full rounded-full bg-muted">
                      <div
                        className="h-1.5 rounded-full bg-primary"
                        style={{ width: `${c.score}%` }}
                      />
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </Section>
          {Object.keys(p.persona).length ? (
            <Section title="Persona">
              <pre className="overflow-auto rounded-lg bg-muted/40 p-2 font-mono text-[11px]">
                {JSON.stringify(p.persona, null, 2)}
              </pre>
            </Section>
          ) : null}
          <Section title={t("employees.runs")}>
            {runs.data && runs.data.length === 0 ? (
              <p className="text-xs text-muted-foreground">{t("employees.noRuns")}</p>
            ) : null}
            <ul className="space-y-1 text-xs">
              {runs.data?.map((r) => (
                <li key={r.run_id} className="flex items-center justify-between gap-2">
                  <Link to={`/runs/${r.run_id}`} className="font-mono hover:underline">
                    {r.run_id.slice(0, 8)}
                  </Link>
                  <span className="text-muted-foreground">{r.model ?? ""}</span>
                  <span className="text-muted-foreground">{formatRelativeTime(r.started_at)}</span>
                  <StatusBadge status={r.status} />
                </li>
              ))}
            </ul>
          </Section>
        </div>
        <Section title={t("employees.resume")}>
          <ol className="space-y-2 text-xs">
            {p.resume?.map((entry, i) => (
              <li
                key={i}
                className="flex flex-wrap items-center gap-2 rounded-lg border border-border px-3 py-2"
              >
                <span className="w-32 shrink-0 text-muted-foreground">
                  {formatDateTime(entry.at)}
                </span>
                <span className="font-medium">{entry.kind}</span>
                {entry.area ? <span className="text-muted-foreground">{entry.area}</span> : null}
                {typeof entry.signal === "number" && entry.signal !== 0 ? (
                  <span className={entry.signal > 0 ? "text-success" : "text-danger"}>
                    {entry.signal > 0 ? "+1" : "−1"}
                  </span>
                ) : null}
                {typeof entry.run_id === "string" ? (
                  <IdChip id={entry.run_id} to={`/runs/${entry.run_id}`} />
                ) : null}
                {typeof entry.project_id === "string" ? (
                  <IdChip id={entry.project_id} to={`/projects/${entry.project_id}`} />
                ) : null}
              </li>
            ))}
          </ol>
          <div className="mt-4">
            <KeyValue
              items={p.employments.map((e) => [
                e.role,
                `${formatDateTime(e.started_at)} → ${e.ended_at ? formatDateTime(e.ended_at) : "…"} (${e.status})`,
              ])}
            />
          </div>
        </Section>
      </div>
    </>
  );
}
