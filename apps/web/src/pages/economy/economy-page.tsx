import { Wallet } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { IdChip, KeyValue, Section, Stat } from "../../components/app/widgets";
import { Button } from "../../components/common/button";
import { Input } from "../../components/common/input";
import { ErrorState, PageHeader } from "../../components/common/states";
import { Skeleton } from "../../components/common/skeleton";
import { useCurrentCompany } from "../../features/company/company-context";
import {
  useCompanyQuery,
  useCostTrace,
  useLedger,
  useLedgerVerify,
  useProjectCosts,
  useProjects,
} from "../../hooks/queries";
import { formatDateTime, formatMicro } from "../../utils/format";

export function EconomyPage() {
  const { t } = useTranslation("app");
  const me = useCurrentCompany();
  const company = useCompanyQuery(me.company_id);
  const ledger = useLedger(me.company_id);
  const verify = useLedgerVerify(me.company_id);
  const projects = useProjects(me.company_id);
  const currency = company.data?.currency;
  const [ref, setRef] = useState("");
  const [traced, setTraced] = useState<string | null>(null);
  const trace = useCostTrace(traced);
  const [project, setProject] = useState<string>("");
  const costs = useProjectCosts(project || projects.data?.[0]?.project_id || "");
  return (
    <>
      <PageHeader title={t("economy.title")} description={t("economy.description")} icon={Wallet} />
      <div className="grid gap-4 sm:grid-cols-3">
        <Stat
          label={t("money.spend")}
          value={ledger.data ? formatMicro(ledger.data.spend_micro, currency) : "…"}
        />
        <Stat
          label={t("economy.verify")}
          value={verify.data ? (verify.data.ok ? t("economy.ok") : t("economy.broken")) : "…"}
          hint={
            verify.data
              ? `${verify.data.entries} entries · ${verify.data.costs} costs · ${t("economy.lag", { count: verify.data.projection_lag })}`
              : undefined
          }
        />
        <Stat label={t("economy.balances")} value={ledger.data?.accounts.length ?? "…"} />
      </div>
      {verify.data && !verify.data.ok ? (
        <ul className="mt-3 rounded-xl border border-danger/30 bg-danger/5 p-3 text-xs text-danger">
          {verify.data.problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      ) : null}
      <div className="mt-6 grid gap-6 xl:grid-cols-2">
        <Section title={t("economy.balances")}>
          {ledger.isLoading ? <Skeleton className="h-24 w-full" /> : null}
          {ledger.error ? <ErrorState error={ledger.error} /> : null}
          <table className="w-full text-xs">
            <thead className="text-left text-muted-foreground">
              <tr>
                <th className="py-1">{t("economy.account")}</th>
                <th className="py-1">{t("economy.kind")}</th>
                <th className="py-1 text-right">{t("economy.debit")}</th>
                <th className="py-1 text-right">{t("economy.credit")}</th>
                <th className="py-1 text-right">{t("economy.balance")}</th>
              </tr>
            </thead>
            <tbody>
              {ledger.data?.accounts.map((a) => (
                <tr key={a.code} className="border-t border-border">
                  <td className="py-1.5 font-mono">{a.code}</td>
                  <td className="py-1.5 text-muted-foreground">{a.kind}</td>
                  <td className="py-1.5 text-right font-mono">
                    {formatMicro(a.debit_total_micro, a.currency)}
                  </td>
                  <td className="py-1.5 text-right font-mono">
                    {formatMicro(a.credit_total_micro, a.currency)}
                  </td>
                  <td className="py-1.5 text-right font-mono">
                    {formatMicro(a.balance_micro, a.currency)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>
        <Section
          title={t("economy.projectCosts")}
          actions={
            <select
              aria-label={t("projects.title")}
              className="rounded-lg border border-border bg-transparent px-2 py-1 text-xs [&>option]:bg-card"
              value={project || projects.data?.[0]?.project_id || ""}
              onChange={(e) => setProject(e.target.value)}
            >
              {projects.data?.map((p) => (
                <option key={p.project_id} value={p.project_id}>
                  {p.title}
                </option>
              ))}
            </select>
          }
        >
          {costs.data ? (
            <>
              <KeyValue
                items={[
                  [t("economy.total"), formatMicro(costs.data.total_micro, currency)],
                  [t("projects.tasks"), formatMicro(costs.data.task_micro, currency)],
                  [t("projects.management"), formatMicro(costs.data.management_micro, currency)],
                ]}
              />
              <ul className="mt-3 space-y-1 text-xs">
                {costs.data.tasks.map((task) => (
                  <li key={task.task_id}>
                    <div className="flex justify-between gap-2">
                      <span>{task.title}</span>
                      <span className="font-mono">{formatMicro(task.micro, currency)}</span>
                    </div>
                    <ul className="pl-3 text-muted-foreground">
                      {task.runs.map((r) => (
                        <li key={r.run_id} className="flex justify-between gap-2">
                          <Link to={`/runs/${r.run_id}`} className="hover:underline">
                            {r.person_id} · {r.run_id.slice(0, 8)}
                          </Link>
                          <span className="font-mono">{formatMicro(r.micro, currency)}</span>
                        </li>
                      ))}
                    </ul>
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <p className="text-xs text-muted-foreground">—</p>
          )}
        </Section>
      </div>
      <Section title={t("economy.trace")} className="mt-6">
        <form
          className="flex gap-2"
          onSubmit={(event: FormEvent) => {
            event.preventDefault();
            setTraced(ref.trim() || null);
          }}
        >
          <Input
            aria-label={t("economy.trace")}
            placeholder={t("economy.traceHint")}
            className="font-mono"
            value={ref}
            onChange={(e) => setRef(e.target.value)}
          />
          <Button type="submit">{t("economy.traceSubmit")}</Button>
        </form>
        {trace.error ? (
          <div className="mt-3">
            <ErrorState error={trace.error} />
          </div>
        ) : null}
        {trace.data ? (
          <div className="mt-4 grid gap-4 md:grid-cols-2">
            <KeyValue
              items={[
                [t("run.model"), `${trace.data.provider}/${trace.data.model} (${trace.data.mode})`],
                [
                  t("economy.booked"),
                  trace.data.booked
                    ? formatMicro(trace.data.booked.amount_micro, trace.data.booked.currency)
                    : t("economy.unbooked", { reason: trace.data.reason }),
                ],
                [
                  t("run.usage"),
                  `${trace.data.usage.input_tokens ?? 0} / ${trace.data.usage.output_tokens ?? 0}`,
                ],
                [t("run.person"), trace.data.person_id ?? "—"],
                [
                  t("run.title"),
                  <IdChip
                    key="r"
                    id={trace.data.run_id}
                    to={trace.data.run_id ? `/runs/${trace.data.run_id}` : undefined}
                  />,
                ],
                [t("run.task"), trace.data.task_title ?? trace.data.task_id ?? "—"],
                [
                  t("projects.title"),
                  <IdChip
                    key="p"
                    id={trace.data.project_id}
                    to={trace.data.project_id ? `/projects/${trace.data.project_id}` : undefined}
                  />,
                ],
              ]}
            />
            <ol className="space-y-1 text-xs">
              {trace.data.chain.map((f) => (
                <li
                  key={f.fact_id}
                  className="flex justify-between gap-2 rounded-lg border border-border px-3 py-1.5"
                >
                  <span className="font-medium">{f.kind}</span>
                  <span className="text-muted-foreground">{formatDateTime(f.at)}</span>
                </li>
              ))}
              {trace.data.missing.map((m) => (
                <li key={m} className="text-warning">
                  {m}
                </li>
              ))}
            </ol>
          </div>
        ) : null}
      </Section>
    </>
  );
}
