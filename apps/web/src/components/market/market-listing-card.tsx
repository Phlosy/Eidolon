import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Badge } from "../common/badge";
import { enumLabel } from "../../utils/labels";
import type { MarketListing } from "../../api/market";

const FIT_STATUS_VARIANT: Record<string, "success" | "info" | "warning" | "muted"> = {
  STRONG_MATCH: "success",
  PARTIAL_MATCH: "info",
  EVALUABLE: "info",
  WEAK_MATCH: "warning",
  CRITICAL_GAP: "warning",
  INSUFFICIENT_DATA: "muted",
  NOT_EVALUABLE: "muted",
};

function pct(value: number | null | undefined): string {
  return value == null ? "—" : `${Math.round(value * 100)}%`;
}

/**
 * 市场候选卡片：是谁、什么档位、谁挂的、以及（选了职位时）匹配摘要。
 * 刻意不显示"战力/总评分大字" —— 履历才是判断依据（愿景 §2.1）。
 */
export function MarketListingCard({ listing }: { listing: MarketListing }) {
  const { t } = useTranslation();
  const fit = listing.fit ?? null;
  return (
    <Link
      to={`/market/${listing.listing_id}`}
      data-testid={`market-listing-${listing.listing_id}`}
      className="group flex flex-col gap-3 rounded-2xl border border-border bg-background/55 p-4 transition-colors hover:border-border-active hover:bg-surface-elevated"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold">{listing.name}</p>
          <p className="mt-0.5 truncate font-mono text-[11px] text-muted-foreground">
            {listing.identity_id}
          </p>
        </div>
        {fit ? (
          <Badge variant={FIT_STATUS_VARIANT[fit.fit_status] ?? "muted"}>
            {t(`market:fit.status.${fit.fit_status}`, { defaultValue: fit.fit_status })}
          </Badge>
        ) : null}
      </div>

      <div className="flex flex-wrap items-center gap-1.5">
        <Badge variant="muted">{enumLabel(t, "cultivation:origin", listing.origin)}</Badge>
        {listing.quality_tier ? <Badge variant="violet">{listing.quality_tier}</Badge> : null}
        <Badge variant={listing.cultivation_state === "ready" ? "success" : "info"}>
          {enumLabel(t, "cultivation:lifecycle", listing.cultivation_state)}
        </Badge>
      </div>

      {fit ? (
        <dl className="grid grid-cols-2 gap-2 text-center">
          <div className="rounded-xl border border-border/60 px-2 py-1.5">
            <dt className="text-[10px] uppercase tracking-wide text-muted-foreground">
              {t("market:fit.score")}
            </dt>
            <dd className="font-mono text-sm font-semibold">{pct(fit.known_fit_score)}</dd>
          </div>
          <div className="rounded-xl border border-border/60 px-2 py-1.5">
            <dt className="text-[10px] uppercase tracking-wide text-muted-foreground">
              {t("market:fit.confidence")}
            </dt>
            <dd className="font-mono text-sm font-semibold">{pct(fit.fit_confidence)}</dd>
          </div>
        </dl>
      ) : null}

      <div className="flex flex-wrap items-center justify-between gap-2 text-[11px] text-muted-foreground">
        <span className="truncate">
          {t("market:listing.listedBy", { name: listing.listed_by })}
        </span>
        {fit ? (
          <span className="font-mono">
            {t("market:fit.known", { known: fit.known_count, total: fit.total_count })}
          </span>
        ) : null}
      </div>
    </Link>
  );
}
