import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { ArrowLeft, UserPlus } from "lucide-react";
import { useMarketListing } from "../../hooks/useMarket";
import { Badge } from "../../components/common/badge";
import { Button } from "../../components/common/button";
import { ErrorState } from "../../components/common/states";
import { Skeleton } from "../../components/common/skeleton";
import { TraitsList } from "../../components/person/traits-list";
import { CompetencyList } from "../../components/person/competency-list";
import { MarketFitPanel } from "../../components/market/market-fit-panel";
import { RecruitDialog } from "../../components/market/recruit-dialog";
import { EducationTimeline } from "../../components/cultivation/education-timeline";
import { enumLabel } from "../../utils/labels";

/**
 * 候选人档案（市场公开读面）：身份 + 人格 + 能力画像 + 知识摘要 + 履历时间线 + 证据。
 * 复用培养/员工侧的共享组件 —— 三处看同一个人，语义一致（设计 §9）。
 */
export function MarketListingPage() {
  const { t } = useTranslation();
  const { listingId } = useParams<{ listingId: string }>();
  const id = Number(listingId);
  const query = useMarketListing(Number.isFinite(id) ? id : null);
  const [positionId, setPositionId] = useState<number | null>(null);
  const [recruitOpen, setRecruitOpen] = useState(false);

  if (query.isLoading) {
    return <Skeleton className="h-64 w-full" />;
  }
  if (query.isError || !query.data) {
    return <ErrorState error={query.error} onRetry={() => query.refetch()} />;
  }

  const candidate = query.data;
  const competencies = candidate.competencies;

  return (
    <div className="space-y-5 panel-enter">
      <div>
        <Link
          to="/market"
          className="inline-flex items-center gap-1.5 text-xs text-muted-foreground transition-colors hover:text-foreground"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          {t("market:title")}
        </Link>
      </div>

      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-lg font-semibold">{candidate.identity.name}</h1>
            {candidate.identity.origin ? (
              <Badge variant="muted">
                {enumLabel(t, "cultivation:origin", candidate.identity.origin)}
              </Badge>
            ) : null}
            {candidate.listing.quality_tier ? (
              <Badge variant="violet">{candidate.listing.quality_tier}</Badge>
            ) : null}
            {candidate.identity.cultivation_state ? (
              <Badge
                variant={candidate.identity.cultivation_state === "ready" ? "success" : "info"}
              >
                {enumLabel(t, "cultivation:lifecycle", candidate.identity.cultivation_state)}
              </Badge>
            ) : null}
          </div>
          <p className="mt-1 font-mono text-[11px] text-muted-foreground">
            {t("market:listing.identity")}: {candidate.identity.identity_id ?? "—"}
          </p>
          <p className="mt-0.5 text-[11px] text-muted-foreground">
            {t("market:listing.listedBy", { name: candidate.listing.listed_by })} ·{" "}
            {t("market:listing.listedAt", {
              value: new Date(candidate.listing.listed_at).toLocaleDateString(),
            })}
          </p>
        </div>
        <Button data-testid="open-recruit" onClick={() => setRecruitOpen(true)}>
          <UserPlus className="h-3.5 w-3.5" />
          {t("market:recruit.action")}
        </Button>
      </header>

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_24rem]">
        <div className="space-y-4">
          <EducationTimeline events={candidate.timeline} />

          <section className="rounded-2xl border border-border bg-card p-4 shadow-card">
            <h3 className="text-sm font-semibold">{t("market:evidence.title")}</h3>
            <p className="mt-1 mb-3 text-[11px] leading-4 text-muted-foreground">
              {t("market:evidence.hint")}
            </p>
            {candidate.evidence.length === 0 ? (
              <p className="text-xs text-muted-foreground">{t("market:evidence.none")}</p>
            ) : (
              <ul className="space-y-1.5" data-testid="market-evidence">
                {candidate.evidence.map((row) => (
                  <li
                    key={row.id}
                    className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-border/60 px-3 py-2 text-xs"
                  >
                    <span className="min-w-0 truncate">
                      {row.competency_name || row.competency_code}
                      <span className="ml-2 font-mono text-[10px] text-muted-foreground">
                        {row.source_ref}
                      </span>
                    </span>
                    <span className="font-mono text-[11px] text-muted-foreground">
                      {t("market:evidence.sourceKind")}: {row.source_kind}
                      {row.signal != null
                        ? ` · ${t("market:evidence.signal", { value: row.signal })}`
                        : ""}
                      {row.assessment_run_id != null ? ` · ${t("market:evidence.assessment")}` : ""}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>

        <aside className="space-y-4">
          <MarketFitPanel
            listingId={candidate.listing.listing_id}
            positionDefinitionId={positionId}
            onPickPosition={setPositionId}
          />

          <section className="rounded-2xl border border-border bg-card p-4 shadow-card">
            <h3 className="text-sm font-semibold">{t("person:traitsTitle")}</h3>
            <div className="mt-2">
              <TraitsList traits={candidate.traits} />
            </div>
          </section>

          <section className="rounded-2xl border border-border bg-card p-4 shadow-card">
            <h3 className="text-sm font-semibold">{t("person:competenciesTitle")}</h3>
            <div className="mt-2">
              <CompetencyList rows={competencies.general} />
            </div>
            {competencies.professional.length > 0 ? (
              <div className="mt-3">
                <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  {t("person:professionalTitle")}
                </h4>
                <div className="mt-2">
                  <CompetencyList
                    rows={competencies.professional}
                    testId="professional-competency-list"
                  />
                </div>
              </div>
            ) : null}
          </section>

          <section className="rounded-2xl border border-border bg-card p-4 shadow-card">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h3 className="text-sm font-semibold">{t("person:knowledgeTitle")}</h3>
              <span className="font-mono text-[11px] text-muted-foreground">
                {t("person:knowledgeTotal", { count: candidate.knowledge_summary.total })}
              </span>
            </div>
            {candidate.knowledge_summary.total === 0 ? (
              <p className="mt-2 text-[11px] text-muted-foreground">{t("person:knowledgeEmpty")}</p>
            ) : (
              <div className="mt-2">
                <ul className="space-y-1 text-xs" data-testid="market-knowledge-topics">
                  {candidate.knowledge_summary.top_topics.map((topic) => (
                    <li key={topic.topic} className="flex justify-between gap-2">
                      <span className="truncate">{topic.topic}</span>
                      <span className="font-mono text-[11px] text-muted-foreground">
                        {topic.count}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </section>
        </aside>
      </div>

      <RecruitDialog
        open={recruitOpen}
        onOpenChange={setRecruitOpen}
        listingId={candidate.listing.listing_id}
        candidateName={candidate.identity.name}
      />
    </div>
  );
}
