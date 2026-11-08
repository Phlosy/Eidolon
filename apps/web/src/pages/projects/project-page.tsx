import { FolderKanban, PackageCheck } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router-dom";
import { work as workApi } from "../../api";
import type { Task } from "../../api/types";
import { ReportView } from "../../components/app/report-view";
import { ErrorNote, IdChip, KeyValue, Section, StatusBadge } from "../../components/app/widgets";
import { Button } from "../../components/common/button";
import { Input } from "../../components/common/input";
import { ErrorState, PageHeader } from "../../components/common/states";
import { Skeleton } from "../../components/common/skeleton";
import { useCurrentCompany } from "../../features/company/company-context";
import {
  useCommand,
  useCompanyQuery,
  useProject,
  useProjectCosts,
  useProjectReport,
} from "../../hooks/queries";
import { formatBytes, formatDateTime, formatMicro, formatUnits } from "../../utils/format";

export function ProjectPage() {
  const { t, i18n } = useTranslation("app");
  const { id = "" } = useParams();
  const me = useCurrentCompany();
  const company = useCompanyQuery(me.company_id);
  const project = useProject(id);
  const costs = useProjectCosts(id);
  const [tab, setTab] = useState<"tasks" | "report">("tasks");
  const report = useProjectReport(id, i18n.language, tab === "report");
  const [note, setNote] = useState("");
  const deliver = useCommand(() => workApi.deliver(id, note || null));
  if (project.isLoading) return <Skeleton className="h-64 w-full" />;
  if (project.error || !project.data)
    return <ErrorState error={project.error} onRetry={() => void project.refetch()} />;
  const p = project.data;
  const currency = company.data?.currency;
  const titles = Object.fromEntries(p.tasks.map((task) => [task.task_id, task.title]));
  const allDone = p.tasks.length > 0 && p.tasks.every((task) => task.status === "done");
  const canDeliver = p.status === "active" && allDone && me.role === "owner";
  return (
    <>
      <PageHeader
        title={p.title}
        description={`${t("projects.manager")}: ${p.manager ?? "—"} · ${t("projects.budget")}: ${formatUnits(p.budget, currency)} · ${t("money.spend")}: ${formatMicro(p.spend_micro, currency)}`}
        icon={FolderKanban}
        actions={<StatusBadge status={p.status} />}
      />
      {canDeliver ? (
        <section className="command-panel mb-6 border-success/40 p-4">
          <div className="flex flex-wrap items-center gap-3">
            <PackageCheck className="h-5 w-5 text-success" />
            <p className="flex-1 text-sm">{t("projects.deliverHint")}</p>
            <Input
              className="max-w-xs"
              aria-label={t("projects.deliverReason")}
              placeholder={t("projects.deliverReason")}
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />
            <Button onClick={() => deliver.mutate(undefined)} disabled={deliver.isPending}>
              {t("projects.deliver")}
            </Button>
          </div>
          <ErrorNote error={deliver.error} />
        </section>
      ) : null}
      <div className="grid gap-6 xl:grid-cols-[3fr_2fr]">
        <div>
          <div role="tablist" className="mb-3 flex gap-2">
            {(["tasks", "report"] as const).map((key) => (
              <Button
                key={key}
                role="tab"
                aria-selected={tab === key}
                size="sm"
                variant={tab === key ? "default" : "outline"}
                onClick={() => setTab(key)}
              >
                {key === "tasks"
                  ? `${t("projects.tasks")} (${p.tasks.length})`
                  : t("projects.report")}
              </Button>
            ))}
          </div>
          {tab === "tasks" ? (
            <Section title={`${t("projects.tasks")} (${p.tasks.length})`}>
              <ol className="space-y-3">
                {p.tasks.map((task) => (
                  <TaskCard key={task.task_id} task={task} titles={titles} />
                ))}
              </ol>
            </Section>
          ) : (
            <Section title={t("projects.report")}>
              {report.data ? (
                <ReportView report={report.data} />
              ) : report.error ? (
                <ErrorState error={report.error} onRetry={() => void report.refetch()} />
              ) : (
                <Skeleton className="h-40 w-full" />
              )}
            </Section>
          )}
        </div>
        <div className="space-y-6">
          <Section title={`${t("projects.artifacts")} (${p.artifacts.length})`}>
            <ul className="space-y-2 text-xs">
              {p.artifacts.map((a) => (
                <li
                  key={a.artifact_id}
                  className="flex items-center justify-between gap-2 rounded-lg border border-border px-3 py-2"
                >
                  <span className="min-w-0 truncate font-mono">{a.path}</span>
                  <span className="shrink-0 text-muted-foreground">
                    {titles[a.task_id] ?? a.task_id.slice(0, 8)} · {formatBytes(a.size)}
                  </span>
                </li>
              ))}
              {p.artifacts.length === 0 ? (
                <li className="text-muted-foreground">{t("drive.empty")}</li>
              ) : null}
            </ul>
          </Section>
          <Section title={t("projects.costs")}>
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
                    <li key={task.task_id} className="flex justify-between gap-2">
                      <span className="truncate">{task.title}</span>
                      <span className="font-mono">{formatMicro(task.micro, currency)}</span>
                    </li>
                  ))}
                  {costs.data.management.map((r) => (
                    <li key={r.run_id} className="flex justify-between gap-2 text-muted-foreground">
                      <Link to={`/runs/${r.run_id}`} className="truncate hover:underline">
                        {r.person_id} · {r.run_id.slice(0, 8)}
                      </Link>
                      <span className="font-mono">{formatMicro(r.micro, currency)}</span>
                    </li>
                  ))}
                </ul>
              </>
            ) : (
              <Skeleton className="h-16 w-full" />
            )}
          </Section>
        </div>
      </div>
    </>
  );
}

function TaskCard({ task, titles }: { task: Task; titles: Record<string, string> }) {
  const { t } = useTranslation("app");
  const [open, setOpen] = useState(false);
  return (
    <li className="rounded-xl border border-border p-3" data-testid="task-card">
      <button
        type="button"
        className="flex w-full items-start justify-between gap-3 text-left"
        onClick={() => setOpen((v) => !v)}
      >
        <div className="min-w-0">
          <p className="text-sm font-medium">{task.title}</p>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {task.role ?? task.kind} · {task.assignee ?? "—"}
            {task.rework_count ? ` · ${t("projects.rework", { count: task.rework_count })}` : ""}
            {task.depends_on.length
              ? ` · ${t("projects.dependsOn")}: ${task.depends_on.map((d) => titles[d] ?? d.slice(0, 8)).join(", ")}`
              : ""}
          </p>
        </div>
        <StatusBadge status={task.status} />
      </button>
      {open ? (
        <div className="mt-3 space-y-3 border-t border-border pt-3 text-xs">
          <p className="whitespace-pre-wrap text-muted-foreground">{task.description}</p>
          {task.acceptance_criteria.length ? (
            <div>
              <p className="font-medium">{t("projects.criteria")}</p>
              <ul className="mt-1 list-disc space-y-0.5 pl-4 text-muted-foreground">
                {task.acceptance_criteria.map((c) => (
                  <li key={c}>{c}</li>
                ))}
              </ul>
            </div>
          ) : null}
          <div>
            <p className="font-medium">{t("projects.reviews")}</p>
            {task.reviews.length === 0 ? (
              <p className="text-muted-foreground">{t("projects.noReviews")}</p>
            ) : (
              <ul className="mt-1 space-y-1">
                {task.reviews.map((r, i) => (
                  <li key={i} className="flex flex-wrap items-center gap-2">
                    <StatusBadge status={r.verdict} />
                    <span>{r.reviewer}</span>
                    {r.reason ? <span className="text-muted-foreground">— {r.reason}</span> : null}
                  </li>
                ))}
              </ul>
            )}
          </div>
          <KeyValue
            items={[
              [
                t("projects.run"),
                <IdChip
                  key="run"
                  id={task.current_run_id}
                  to={task.current_run_id ? `/runs/${task.current_run_id}` : undefined}
                />,
              ],
              [t("common.at"), formatDateTime(task.updated_at)],
            ]}
          />
        </div>
      ) : null}
    </li>
  );
}
