import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Activity, Bot, Box, Cpu, ExternalLink, Gauge, ServerCog } from "lucide-react";
import { useRuntimeImages, useRuntimeInstances } from "../../hooks/useRuntimes";
import { useEmployees } from "../../hooks/useEmployees";
import { ProvidersOverviewTable } from "../../components/provider/providers-overview-table";
import { RuntimeUpdatesSection } from "../../components/runtime/runtime-updates-section";
import { RuntimeStatusBadge } from "../../components/runtime/runtime-status-badge";
import { EmptyState, ErrorState, PageHeader } from "../../components/common/states";
import { Skeleton } from "../../components/common/skeleton";
import { enumLabel } from "../../utils/labels";

export function RuntimePage() {
  const { t } = useTranslation();
  const runtimesQuery = useRuntimeInstances();
  const imagesQuery = useRuntimeImages();
  const employees = useEmployees().data ?? [];
  const runtimes = runtimesQuery.data ?? [];
  const running = runtimes.filter((runtime) => runtime.status === "running" || runtime.status === "idle").length;
  const unhealthy = runtimes.filter((runtime) => ["unhealthy", "crashed", "error"].includes(runtime.status)).length;
  const imageByType = new Map((imagesQuery.data ?? []).map((image) => [image.runtime_type, image]));

  return <div className="space-y-6 panel-enter">
    <PageHeader icon={ServerCog} title={t("runtime:controlCenter.title")} description={t("runtime:controlCenter.description")} />
    <section className="command-panel relative overflow-hidden p-5 md:p-6"><div className="relative grid gap-5 md:grid-cols-[1.2fr_repeat(3,minmax(0,.6fr))]"><div><p className="type-kicker text-primary">{t("runtime:controlCenter.liveInfrastructure")}</p><h2 className="mt-2 text-2xl font-semibold tracking-tight">{running > 0 ? t("runtime:controlCenter.operational") : t("runtime:controlCenter.standby")}</h2><p className="mt-2 text-sm text-muted-foreground">{t("runtime:controlCenter.summary", { running, total: runtimes.length })}</p></div>{[[Activity, t("runtime:controlCenter.activeNodes"), `${running}/${runtimes.length}`], [Gauge, t("runtime:controlCenter.incidents"), unhealthy], [Box, t("runtime:controlCenter.images"), imagesQuery.data?.length ?? 0]].map(([Icon, label, value]) => { const MetricIcon = Icon as typeof Activity; return <div key={String(label)} className="rounded-2xl border border-border bg-background/45 p-4"><MetricIcon className="h-4 w-4 text-primary" /><p className="type-telemetry mt-5 text-2xl font-semibold">{String(value)}</p><p className="mt-1 text-[11px] text-muted-foreground">{String(label)}</p></div>; })}</div></section>

    <section><div className="mb-3 flex items-end justify-between"><div><p className="type-kicker text-muted-foreground">{t("runtime:controlCenter.nodesKicker")}</p><h2 className="mt-1 text-lg font-semibold">{t("runtime:controlCenter.nodesTitle")}</h2></div></div>{runtimesQuery.isLoading ? <div className="grid gap-4 lg:grid-cols-2">{[0,1].map((item) => <Skeleton key={item} className="h-52 rounded-[var(--radius-panel)]" />)}</div> : runtimesQuery.isError ? <ErrorState error={runtimesQuery.error} onRetry={() => runtimesQuery.refetch()} /> : runtimes.length === 0 ? <EmptyState title={t("runtime:controlCenter.emptyTitle")} hint={t("runtime:controlCenter.emptyHint")} /> : <div className="grid gap-4 lg:grid-cols-2">{runtimes.map((runtime) => { const employee = employees.find((item) => item.id === runtime.employee_id); const image = imageByType.get(runtime.runtime_type); return <article key={runtime.id} className="command-panel relative overflow-hidden p-5 transition hover:-translate-y-0.5 hover:border-border-active"><div className="relative flex items-start justify-between gap-4"><div className="flex min-w-0 items-center gap-3"><span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border border-primary/25 bg-primary/8 text-primary"><Bot className="h-5 w-5" /></span><div className="min-w-0"><p className="truncate font-semibold">{employee?.name ?? `#${runtime.employee_id}`}</p><p className="mt-0.5 text-xs text-muted-foreground">{enumLabel(t, "runtime:type", runtime.runtime_type)} · {runtime.provider_name ?? "—"}</p></div></div><RuntimeStatusBadge status={runtime.status} /></div><dl className="relative mt-6 grid grid-cols-2 gap-4 text-xs"><div><dt className="text-muted-foreground">{t("runtime:card.version")}</dt><dd className="type-telemetry mt-1">{runtime.runtime_version ?? "—"}{image?.update_available ? ` · ${t("runtime:updateAvailable")}` : ""}</dd></div><div><dt className="text-muted-foreground">{t("runtime:card.resources")}</dt><dd className="type-telemetry mt-1 flex items-center gap-1.5"><Cpu className="h-3 w-3" />{runtime.cpu_limit} / {runtime.memory_limit_mb} MB</dd></div><div className="col-span-2"><dt className="text-muted-foreground">{t("runtime:card.container")}</dt><dd className="type-telemetry mt-1 truncate">{runtime.container_name ?? "—"}</dd></div></dl>{employee ? <Link to={`/employees/${employee.id}`} className="relative mt-5 inline-flex items-center gap-2 text-xs font-medium text-primary hover:underline">{t("runtime:controlCenter.openWorkbench")}<ExternalLink className="h-3 w-3" /></Link> : null}</article>; })}</div>}</section>
    <ProvidersOverviewTable /><RuntimeUpdatesSection />
  </div>;
}
