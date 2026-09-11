import { get, post } from "./client";

/**
 * M1 经济读面（docs/m1-economy-design.md §31/§32）。
 * 类型对齐 apps/server/app/schemas/economy.py；改后端 schema 时同步这里。
 *
 * 纪律：前端**只读**资金真相 —— 余额/流水都来自账本投影；
 * 写操作只有"奖励领取 / 工作订单领取与提交 / 合同动作"这些服务层授权的业务动作，
 * 没有任何直接改余额的入口（那是 LedgerService 的内部能力）。
 */

export interface LedgerAccountView {
  account_id: number;
  kind: string;
  currency: string;
  status: string;
  subject_ref: number;
  posted_balance: number;
  available_balance: number;
  reserved_balance: number;
  version: number;
}

export interface CategoryFlow {
  category: string;
  label: string;
  income: number;
  expense: number;
  net: number;
}

export interface EconomyOverview {
  actor_kind: string;
  actor_ref: number;
  currency: string;
  posted_balance: number;
  available_balance: number;
  reserved_balance: number;
  income_total: number;
  expense_total: number;
  net_total: number;
  by_category: CategoryFlow[];
  compute_paid: number;
  compute_unpaid: number;
}

export interface LedgerEntryView {
  account_id: number;
  direction: string;
  amount: number;
}

export interface LedgerTransactionView {
  transaction_id: number;
  transaction_type: string;
  currency: string;
  status: string;
  reference_type: string;
  reference_id: string;
  reason: string;
  amount: number;
  occurred_at: string;
  posted_at: string;
  entries: LedgerEntryView[];
}

export interface LedgerTransactionPage {
  items: LedgerTransactionView[];
  total: number;
  limit: number;
  offset: number;
}

export interface PersonalWallet {
  actor_kind: string;
  actor_ref: number;
  currency: string;
  posted_balance: number;
  available_balance: number;
  reserved_balance: number;
  accounts: LedgerAccountView[];
  transactions: LedgerTransactionPage;
}

export interface ComputeUsageView {
  usage_id: number;
  employee_id: number | null;
  work_session_id: number | null;
  model: string;
  units: number;
  unit_price: number;
  amount: number;
  tokens: number;
  duration_seconds: number | null;
  status: string;
  unpaid_reason: string;
  occurred_at: string;
}

export interface ComputeUsagePage {
  items: ComputeUsageView[];
  total: number;
  limit: number;
  offset: number;
  paid_total: number;
  unpaid_total: number;
}

export interface RewardOptionView {
  reward_type: string;
  label: string;
  actor_kind: string;
  actor_ref: number;
  amount: number;
  currency: string;
  reference_key: string;
  claimable: boolean;
  reason: string;
  policy_version: string;
  next_eligible_at: string | null;
  claimed_at: string | null;
  grant_id: number | null;
  metadata: Record<string, unknown>;
}

export interface RewardClaimView {
  grant_id: number;
  reward_type: string;
  label: string;
  actor_kind: string;
  actor_ref: number;
  amount: number;
  currency: string;
  reference_key: string;
  status: string;
  policy_version: string;
  ledger_transaction_id: number | null;
  claimed_at: string | null;
  posted_at: string | null;
  created: boolean;
}

export interface TransactionFilters {
  limit?: number;
  offset?: number;
  transactionType?: string;
  referenceType?: string;
  referenceId?: string;
}

function transactionQuery(filters: TransactionFilters = {}): string {
  const params = new URLSearchParams();
  if (filters.limit != null) params.set("limit", String(filters.limit));
  if (filters.offset != null) params.set("offset", String(filters.offset));
  if (filters.transactionType) params.set("transaction_type", filters.transactionType);
  if (filters.referenceType) params.set("reference_type", filters.referenceType);
  if (filters.referenceId) params.set("reference_id", filters.referenceId);
  const query = params.toString();
  return query ? `?${query}` : "";
}

/** 公司经营报表：收入/成本/净额 + 分类 + 算力欠费。 */
export function getEconomyOverview(): Promise<EconomyOverview> {
  return get<EconomyOverview>("/economy/overview");
}

/** 我的钱包（个人主体）：个人奖励进的是这里，而不是公司账户。 */
export function getMyWallet(limit = 20): Promise<PersonalWallet> {
  return get<PersonalWallet>(`/economy/wallet/me?limit=${limit}`);
}

/** 公司流水（分页 + 过滤）——"这笔钱从哪来、花到哪去"。 */
export function getCompanyTransactions(
  filters: TransactionFilters = {},
): Promise<LedgerTransactionPage> {
  return get<LedgerTransactionPage>(`/economy/transactions${transactionQuery(filters)}`);
}

/** 算力用量（含未扣费记录：欠费要看得见）。 */
export function getComputeUsage(limit = 20, offset = 0): Promise<ComputeUsagePage> {
  return get<ComputeUsagePage>(`/economy/compute-usage?limit=${limit}&offset=${offset}`);
}

/** 自助奖励清单（可领/已领/原因/下次可领时间）。 */
export function getRewards(): Promise<{ items: RewardOptionView[] }> {
  return get<{ items: RewardOptionView[] }>("/economy/rewards");
}

/** 领取奖励（幂等：重复领取返回既有 grant，不重复发钱）。 */
export function claimReward(
  rewardType: string,
  referenceKey?: string | null,
): Promise<RewardClaimView> {
  return post<RewardClaimView>(`/economy/rewards/${rewardType}/claim`, {
    reference_key: referenceKey ?? null,
  });
}
