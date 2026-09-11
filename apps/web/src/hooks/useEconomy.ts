import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  claimReward,
  getCompanyTransactions,
  getComputeUsage,
  getEconomyOverview,
  getMyWallet,
  getRewards,
  type TransactionFilters,
} from "../api/economy";

/** 公司经营报表（收入/成本/净额/分类/算力欠费）。 */
export function useEconomyOverview() {
  return useQuery({ queryKey: ["economy", "overview"], queryFn: getEconomyOverview });
}

/** 我的钱包（个人主体；个人奖励进这里）。 */
export function useMyWallet(limit = 20) {
  return useQuery({ queryKey: ["economy", "wallet", limit], queryFn: () => getMyWallet(limit) });
}

export function useCompanyTransactions(filters: TransactionFilters = {}) {
  return useQuery({
    queryKey: ["economy", "transactions", filters],
    queryFn: () => getCompanyTransactions(filters),
  });
}

export function useComputeUsage(limit = 20, offset = 0) {
  return useQuery({
    queryKey: ["economy", "compute", limit, offset],
    queryFn: () => getComputeUsage(limit, offset),
  });
}

export function useRewards() {
  return useQuery({ queryKey: ["economy", "rewards"], queryFn: getRewards });
}

/** 领奖（幂等）——成功后刷新奖励清单与钱包/报表。 */
export function useClaimReward() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      rewardType,
      referenceKey,
    }: {
      rewardType: string;
      referenceKey?: string | null;
    }) => claimReward(rewardType, referenceKey),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["economy"] });
    },
  });
}
