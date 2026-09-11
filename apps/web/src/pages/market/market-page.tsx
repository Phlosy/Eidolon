import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Search } from "lucide-react";
import { useMarketListings } from "../../hooks/useMarket";
import { usePositionProfiles } from "../../hooks/usePositionProfiles";
import { Input } from "../../components/common/input";
import { EmptyState, ErrorState } from "../../components/common/states";
import { Skeleton } from "../../components/common/skeleton";
import { MarketListingCard } from "../../components/market/market-listing-card";
import { MyListingsPanel } from "../../components/market/my-listings-panel";

const selectClass =
  "rounded-md border border-border bg-transparent px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

/**
 * 人才市场：浏览在市人才（履历即证据链），可选职位 → 附 Fit 摘要并排序。
 *
 * 市场是**跨公司公开读面**（D4）；这里只展示公开投影，私有字段永远拿不到。
 */
export function MarketPage() {
  const { t } = useTranslation();
  const [text, setText] = useState("");
  const [origin, setOrigin] = useState("");
  const [tier, setTier] = useState("");
  const [positionId, setPositionId] = useState<number | null>(null);
  const profiles = usePositionProfiles();
  const positions = (profiles.data ?? []).filter((profile) => profile.profile_status === "active");

  const listingsQuery = useMarketListings({
    text: text.trim() || undefined,
    origin: origin || undefined,
    qualityTier: tier || undefined,
    positionDefinitionId: positionId,
    limit: 60,
  });

  const items = listingsQuery.data?.items ?? [];

  return (
    <div className="space-y-5 panel-enter">
      <header>
        <h1 className="text-lg font-semibold">{t("market:title")}</h1>
        <p className="mt-1 max-w-3xl text-xs leading-5 text-muted-foreground">
          {t("market:description")}
        </p>
      </header>

      <MyListingsPanel />

      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-56 flex-1">
          <Search className="pointer-events-none absolute top-1/2 left-3 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            className="pl-8"
            data-testid="market-search"
            placeholder={t("market:filters.searchPlaceholder")}
            value={text}
            onChange={(event) => setText(event.target.value)}
          />
        </div>
        <select
          className={selectClass}
          data-testid="market-origin"
          value={origin}
          onChange={(event) => setOrigin(event.target.value)}
        >
          <option value="">{t("market:filters.originAll")}</option>
          <option value="trained">trained</option>
          <option value="blank">blank</option>
          <option value="issued">issued</option>
        </select>
        <select
          className={selectClass}
          data-testid="market-tier"
          value={tier}
          onChange={(event) => setTier(event.target.value)}
        >
          <option value="">{t("market:filters.tierAll")}</option>
          <option value="normal">normal</option>
          <option value="fine">fine</option>
          <option value="rare">rare</option>
        </select>
        <select
          className={selectClass}
          data-testid="market-position"
          value={positionId ?? ""}
          onChange={(event) =>
            setPositionId(event.target.value === "" ? null : Number(event.target.value))
          }
        >
          <option value="">{t("market:filters.positionAll")}</option>
          {positions.map((profile) => (
            <option key={profile.position_definition_id} value={profile.position_definition_id}>
              {profile.name} ({profile.code})
            </option>
          ))}
        </select>
      </div>

      {positionId != null ? (
        <p className="text-[11px] leading-4 text-muted-foreground">
          {t("market:filters.positionHint")}
        </p>
      ) : null}

      {listingsQuery.isLoading ? (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          <Skeleton className="h-44 w-full" />
          <Skeleton className="h-44 w-full" />
          <Skeleton className="h-44 w-full" />
        </div>
      ) : listingsQuery.isError ? (
        <ErrorState error={listingsQuery.error} onRetry={() => listingsQuery.refetch()} />
      ) : items.length === 0 ? (
        <EmptyState title={t("market:empty.title")} hint={t("market:empty.hint")} />
      ) : (
        <>
          <p className="text-xs text-muted-foreground">
            {t("market:summary", { count: listingsQuery.data?.total ?? items.length })}
          </p>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {items.map((listing) => (
              <MarketListingCard key={listing.listing_id} listing={listing} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
