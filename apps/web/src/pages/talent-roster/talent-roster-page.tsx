import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { LayoutGrid, Rows3, Search } from "lucide-react";
import { useTalentRoster } from "../../hooks/useTalentRoster";
import { useCompetencyDomains } from "../../hooks/useCompetencies";
import { Badge } from "../../components/common/badge";
import { EmptyState, ErrorState, PageHeader } from "../../components/common/states";
import { Skeleton } from "../../components/common/skeleton";
import type { TalentRosterItem } from "../../types";

const STATUS_VARIANT: Record<string, "success" | "info" | "warning" | "muted"> = {
  assigned: "success",
  available: "info",
  transferring: "warning",
  suspended: "muted",
};

export function TalentRosterPage() {
  const { t } = useTranslation();
  const [view, setView] = useState<"card" | "table">(
    () => (localStorage.getItem("roster.view") as "card" | "table") ?? "card",
  );
  const [status, setStatus] = useState<string>("");
  const departmentId = "";
  const [competencyCode, setCompetencyCode] = useState("");
  const [minScore, setMinScore] = useState("");
  const [minConfidence, setMinConfidence] = useState("");
  const traitCode = "";
  const [search, setSearch] = useState("");
  const domainsQuery = useCompetencyDomains();

  const rowsQuery = useTalentRoster(
    useMemo(
      () => ({
        status: status ? [status] : undefined,
        department_id: departmentId ? Number(departmentId) : undefined,
        competency_code: competencyCode || undefined,
        min_competency_score: minScore ? Number(minScore) : undefined,
        min_competency_confidence: minConfidence ? Number(minConfidence) : undefined,
        trait_code: traitCode || undefined,
        search: search || undefined,
      }),
      [status, departmentId, competencyCode, minScore, minConfidence, traitCode, search],
    ),
  );

  const switchView = (next: "card" | "table") => {
    setView(next);
    localStorage.setItem("roster.view", next);
  };

  const generalDomains = domainsQuery.data?.filter((domain) => domain.kind === "general") ?? [];
  if (rowsQuery.isLoading) return <Skeleton className="h-48 w-full" />;
  if (rowsQuery.isError)
    return <ErrorState error={rowsQuery.error} onRetry={() => rowsQuery.refetch()} />;
  const rows = rowsQuery.data ?? [];

  return (
    <div className="space-y-5 panel-enter">
      <PageHeader
        icon={LayoutGrid}
        title={t("employee:roster.rosterTitle")}
        description={t("employee:roster.rosterDesc")}
      />
      <section className="flex flex-wrap items-center gap-2">
        <label className="relative min-w-[200px] flex-1 md:max-w-xs">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search…"
            className="h-9 w-full rounded-md border border-border bg-background pl-9 pr-3 text-sm"
          />
        </label>
        <select
          value={status}
          onChange={(e) => setStatus(e.target.value)}
          className="h-9 rounded-md border border-border bg-background px-2 text-xs"
        >
          <option value="">
            {t("employee:roster.filterWorkforce")} · {t("employee:roster.all")}
          </option>
          <option value="available">{t("employee:roster.statusAvailable")}</option>
          <option value="assigned">{t("employee:roster.statusAssigned")}</option>
          <option value="transferring">{t("employee:roster.statusTransferring")}</option>
        </select>
        <select
          value={competencyCode}
          onChange={(e) => setCompetencyCode(e.target.value)}
          className="h-9 rounded-md border border-border bg-background px-2 text-xs"
        >
          <option value="">{t("employee:roster.filterCompetency")} · all</option>
          {generalDomains.map((domain) => (
            <option key={domain.id} value={domain.code}>
              {domain.code}
            </option>
          ))}
        </select>
        {competencyCode ? (
          <>
            <input
              value={minScore}
              onChange={(e) => setMinScore(e.target.value)}
              placeholder={t("employee:roster.filterMinScore")}
              className="h-9 w-20 rounded-md border border-border bg-background px-2 text-xs"
            />
            <input
              value={minConfidence}
              onChange={(e) => setMinConfidence(e.target.value)}
              placeholder={t("employee:roster.filterMinConfidence")}
              className="h-9 w-24 rounded-md border border-border bg-background px-2 text-xs"
            />
          </>
        ) : null}
        <div className="ml-auto flex overflow-hidden rounded-md border border-border">
          <button
            onClick={() => switchView("card")}
            className={`px-3 py-1.5 text-xs ${view === "card" ? "bg-primary/10 text-primary" : "text-muted-foreground"}`}
          >
            <LayoutGrid className="h-4 w-4" />
          </button>
          <button
            onClick={() => switchView("table")}
            className={`px-3 py-1.5 text-xs ${view === "table" ? "bg-primary/10 text-primary" : "text-muted-foreground"}`}
          >
            <Rows3 className="h-4 w-4" />
          </button>
        </div>
      </section>

      {rows.length === 0 ? <EmptyState title={t("employee:roster.rosterTitle")} /> : null}

      {view === "card" ? (
        <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3" data-testid="roster-cards">
          {rows.map((item) => (
            <RosterCard key={item.employee_id} item={item} />
          ))}
        </ul>
      ) : (
        <table className="w-full text-left text-sm" data-testid="roster-table">
          <thead>
            <tr className="border-b border-border text-xs text-muted-foreground">
              <th className="py-2">{t("employee:roster.rosterTitle")}</th>
              <th>{t("employee:roster.filterWorkforce")}</th>
              <th>{t("employee:roster.filterPosition")}</th>
              <th>{t("employee:roster.topGeneral")}</th>
              <th>{t("employee:roster.assessmentSummary")}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((item) => (
              <tr key={item.employee_id} className="border-b border-border/50">
                <td className="py-2">
                  {item.name}{" "}
                  <span className="font-mono text-[11px] text-muted-foreground">{item.slug}</span>
                </td>
                <td>
                  <Badge variant={STATUS_VARIANT[item.workforce_status] ?? "muted"}>
                    {item.workforce_status}
                  </Badge>
                </td>
                <td className="text-xs">{item.current_position?.name ?? "—"}</td>
                <td className="text-xs font-mono">
                  {item.top_general_competencies.map((c) => `${c.code} ${c.score}`).join(", ") ||
                    "—"}
                </td>
                <td className="text-xs">{item.assessment_summary?.evidence_coverage ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function RosterCard({ item }: { item: TalentRosterItem }) {
  const { t } = useTranslation();
  const coverage = item.assessment_summary?.evidence_coverage ?? "none";
  const coverageText =
    coverage === "none"
      ? t("employee:roster.evNone")
      : coverage === "low"
        ? t("employee:roster.evLow")
        : coverage === "medium"
          ? t("employee:roster.evMedium")
          : t("employee:roster.evHigh");
  return (
    <li
      className="rounded-md border border-border p-4 shadow-[var(--shadow-panel)]"
      data-testid={`roster-card-${item.slug}`}
    >
      <div className="flex items-start gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-full bg-primary/10 font-semibold">
          {item.name.slice(0, 1)}
        </div>
        <div className="min-w-0">
          <Link
            to={`/employees/${item.employee_id}`}
            className="text-sm font-semibold hover:underline"
          >
            {item.name}
          </Link>
          <div className="mt-0.5 flex flex-wrap items-center gap-1.5">
            <Badge variant={STATUS_VARIANT[item.workforce_status] ?? "muted"}>
              {item.workforce_status}
            </Badge>
            <span className="text-xs text-muted-foreground">
              {item.current_position?.name ?? t("employee:roster.statusAvailable")}
            </span>
          </div>
          <div className="mt-1 flex flex-wrap gap-1">
            {item.runtime ? <Badge variant="muted">{item.runtime.type}</Badge> : null}
            {item.traits_summary.map((trait) => (
              <Badge key={trait.code} variant="info">
                {trait.code}
              </Badge>
            ))}
          </div>
        </div>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
        <div>
          <p className="text-muted-foreground">{t("employee:roster.topGeneral")}</p>
          <p className="font-mono">
            {item.top_general_competencies.map((c) => `${c.code} ${c.score}`).join("  ") || "—"}
          </p>
        </div>
        <div>
          <p className="text-muted-foreground">{t("employee:roster.topProfessional")}</p>
          <p className="font-mono">
            {item.top_professional_competencies.map((c) => `${c.code} ${c.score}`).join("  ") ||
              "—"}
          </p>
        </div>
      </div>
      <p className="mt-2 text-xs text-muted-foreground" data-testid="coverage-label">
        {coverageText}
      </p>
    </li>
  );
}
