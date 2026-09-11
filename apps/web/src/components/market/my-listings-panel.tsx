import { useTranslation } from "react-i18next";
import { Loader2, Tag, Undo2 } from "lucide-react";
import { Button } from "../common/button";
import { Badge } from "../common/badge";
import { useCultivationCharacters } from "../../hooks/useCultivation";
import {
  useCreateMarketListing,
  useDelistMarketListing,
  useMarketListings,
} from "../../hooks/useMarket";
import { ApiError } from "../../api/client";

/**
 * 「我的挂牌」：结业角色一键挂牌 / 在市一键下架。
 *
 * 挂牌是**幂等**的（后端：重复挂牌返回既有 active 挂牌）；资格不符时后端 409，
 * 这里把 reason code 翻成文案 —— 前端不复制 eligibility 判定（UI 不是业务真相）。
 */
export function MyListingsPanel() {
  const { t } = useTranslation();
  const charactersQuery = useCultivationCharacters();
  const mineQuery = useMarketListings({ mine: true, limit: 200 });
  const listMutation = useCreateMarketListing();
  const delistMutation = useDelistMarketListing();

  const ready = (charactersQuery.data ?? []).filter((character) => character.lifecycle === "ready");
  const myListings = mineQuery.data?.items ?? [];
  // 公开投影不含 person_id：用终身唯一的 identity_id 把挂牌对回自己的角色
  const listingByIdentity = new Map(myListings.map((item) => [item.identity_id, item]));
  const error = listMutation.error ?? delistMutation.error;
  const errorDetail = error instanceof ApiError ? error.detail : error ? String(error) : null;

  if (ready.length === 0 && myListings.length === 0) return null;

  return (
    <section
      className="space-y-3 rounded-2xl border border-border bg-card p-4 shadow-card"
      data-testid="my-listings"
    >
      <div>
        <h3 className="text-sm font-semibold">{t("market:manage.title")}</h3>
        <p className="mt-1 text-[11px] leading-4 text-muted-foreground">
          {t("market:manage.hint")}
        </p>
      </div>

      <ul className="space-y-1.5">
        {ready.map((character) => {
          const listed = listingByIdentity.get(character.identity_id);
          return (
            <li
              key={character.id}
              data-testid={`my-character-${character.id}`}
              className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-border px-3 py-2"
            >
              <div className="min-w-0">
                <span className="text-xs font-medium">{character.name}</span>
                <span className="ml-2 font-mono text-[11px] text-muted-foreground">
                  {character.identity_id}
                </span>
              </div>
              <div className="flex items-center gap-2">
                <Badge variant={listed ? "violet" : "success"}>
                  {listed ? t("market:manage.listed") : t("market:manage.ready")}
                </Badge>
                {listed ? (
                  <Button
                    size="sm"
                    variant="outline"
                    data-testid={`delist-${listed.listing_id}`}
                    disabled={delistMutation.isPending}
                    onClick={() => delistMutation.mutate(listed.listing_id)}
                  >
                    {delistMutation.isPending ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Undo2 className="h-3.5 w-3.5" />
                    )}
                    {delistMutation.isPending
                      ? t("market:manage.delisting")
                      : t("market:manage.delist")}
                  </Button>
                ) : (
                  <Button
                    size="sm"
                    data-testid={`list-${character.person_id}`}
                    disabled={listMutation.isPending}
                    onClick={() => listMutation.mutate({ personId: character.person_id })}
                  >
                    {listMutation.isPending ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Tag className="h-3.5 w-3.5" />
                    )}
                    {listMutation.isPending ? t("market:manage.listing") : t("market:manage.list")}
                  </Button>
                )}
              </div>
            </li>
          );
        })}
      </ul>

      {errorDetail ? (
        <p className="text-xs text-danger" role="alert">
          {t("market:manage.error", { error: errorDetail })}
        </p>
      ) : null}
    </section>
  );
}
