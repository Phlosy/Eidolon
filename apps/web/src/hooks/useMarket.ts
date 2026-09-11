import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createMarketListing,
  delistMarketListing,
  getMarketFit,
  getMarketListing,
  getMarketListings,
  recruitMarketListing,
  type MarketListingFilters,
  type RecruitInput,
} from "../api/market";

/** 市场列表（可选带职位 → 附 Fit 摘要并排序；`mine` → 只看自己的挂牌）。 */
export function useMarketListings(filters: MarketListingFilters = {}) {
  return useQuery({
    queryKey: ["market", "listings", filters],
    queryFn: () => getMarketListings(filters),
  });
}

export function useMarketListing(listingId: number | null) {
  return useQuery({
    queryKey: ["market", "listing", listingId],
    queryFn: () => getMarketListing(listingId!),
    enabled: listingId != null,
  });
}

/** 候选人 × 职位的 Fit（市场公开投影）。 */
export function useMarketFit(
  listingId: number | null,
  positionDefinitionId: number | null,
  profileVersionId?: number | null,
) {
  return useQuery({
    queryKey: ["market", "fit", listingId, positionDefinitionId, profileVersionId ?? null],
    queryFn: () => getMarketFit(listingId!, positionDefinitionId!, profileVersionId),
    enabled: listingId != null && positionDefinitionId != null,
    retry: false,
  });
}

export function useCreateMarketListing() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ personId, qualityTier }: { personId: number; qualityTier?: string | null }) =>
      createMarketListing(personId, qualityTier),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["market"] }),
  });
}

export function useDelistMarketListing() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (listingId: number) => delistMarketListing(listingId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["market"] }),
  });
}

export function useRecruitListing() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ listingId, body }: { listingId: number; body: RecruitInput }) =>
      recruitMarketListing(listingId, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["market"] });
      void queryClient.invalidateQueries({ queryKey: ["employees"] });
    },
  });
}
