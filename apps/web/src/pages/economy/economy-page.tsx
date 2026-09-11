import { useTranslation } from "react-i18next";
import { Coins, PiggyBank, Receipt, Wallet } from "lucide-react";
import {
  useClaimReward,
  useCompanyTransactions,
  useEconomyOverview,
  useMyWallet,
  useRewards,
} from "../../hooks/useEconomy";
import { EmptyState, ErrorState } from "../../components/common/states";
import { Skeleton } from "../../components/common/skeleton";

function money(value: number | null | undefined): string {
  if (value == null) return "—";
  return value.toLocaleString();
}

/**
 * 经济总览（M1.9）：公司钱包 + 收支分类 + 流水 + 个人钱包 + 奖励领取。
 *
 * 纪律：余额与流水全部来自后端读面（账本投影）—— **前端不计算钱**（Risks：UI 不能成为业务真相）。
 */
export function EconomyPage() {
  const { t } = useTranslation();
  const overview = useEconomyOverview();
  const wallet = useMyWallet(10);
  const transactions = useCompanyTransactions({ limit: 15 });
  const rewards = useRewards();
  const claim = useClaimReward();

  if (overview.isLoading) return <Skeleton className="h-40 w-full" />;
  if (overview.isError || !overview.data) {
    return <ErrorState error={overview.error} onRetry={() => void overview.refetch()} />;
  }
  const data = overview.data;
  const categories = data.by_category ?? [];
  const rewardItems = rewards.data?.items ?? [];

  return (
    <div className="space-y-5 panel-enter">
      <header>
        <h1 className="text-lg font-semibold">{t("economy:title")}</h1>
        <p className="mt-1 max-w-3xl text-xs leading-5 text-muted-foreground">
          {t("economy:description")}
        </p>
      </header>

      <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <div className="rounded-lg border border-border p-4">
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <Wallet className="h-4 w-4" />
            {t("economy:wallet.available")}
          </div>
          <div className="mt-2 text-2xl font-semibold">{money(data.available_balance)}</div>
          <div className="mt-1 text-xs text-muted-foreground">
            {t("economy:wallet.locked", { amount: money(data.reserved_balance) })}
          </div>
        </div>
        <div className="rounded-lg border border-border p-4">
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <Coins className="h-4 w-4" />
            {t("economy:kpi.income")}
          </div>
          <div className="mt-2 text-2xl font-semibold">{money(data.income_total)}</div>
        </div>
        <div className="rounded-lg border border-border p-4">
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <Receipt className="h-4 w-4" />
            {t("economy:kpi.expense")}
          </div>
          <div className="mt-2 text-2xl font-semibold">{money(data.expense_total)}</div>
        </div>
        <div className="rounded-lg border border-border p-4">
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <PiggyBank className="h-4 w-4" />
            {t("economy:kpi.net")}
          </div>
          <div className="mt-2 text-2xl font-semibold">{money(data.net_total)}</div>
          {data.compute_unpaid > 0 ? (
            <div className="mt-1 text-xs text-amber-500">
              {t("economy:kpi.computeUnpaid", { amount: money(data.compute_unpaid) })}
            </div>
          ) : null}
        </div>
      </section>

      <section className="rounded-lg border border-border">
        <h2 className="border-b border-border px-4 py-3 text-sm font-medium">
          {t("economy:categories.title")}
        </h2>
        {categories.length === 0 ? (
          <EmptyState title={t("economy:categories.empty")} />
        ) : (
          <table className="w-full text-sm">
            <thead className="text-xs text-muted-foreground">
              <tr>
                <th className="px-4 py-2 text-left font-normal">
                  {t("economy:categories.category")}
                </th>
                <th className="px-4 py-2 text-right font-normal">
                  {t("economy:categories.income")}
                </th>
                <th className="px-4 py-2 text-right font-normal">
                  {t("economy:categories.expense")}
                </th>
                <th className="px-4 py-2 text-right font-normal">{t("economy:categories.net")}</th>
              </tr>
            </thead>
            <tbody>
              {categories.map((row) => (
                <tr key={row.category} className="border-t border-border">
                  <td className="px-4 py-2">{row.label}</td>
                  <td className="px-4 py-2 text-right">{money(row.income)}</td>
                  <td className="px-4 py-2 text-right">{money(row.expense)}</td>
                  <td className="px-4 py-2 text-right font-medium">{money(row.net)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <div className="grid gap-4 lg:grid-cols-2">
        <section className="rounded-lg border border-border">
          <h2 className="border-b border-border px-4 py-3 text-sm font-medium">
            {t("economy:rewards.title")}
          </h2>
          {rewardItems.length === 0 ? (
            <EmptyState title={t("economy:rewards.empty")} />
          ) : (
            <ul className="divide-y divide-border">
              {rewardItems.map((item) => (
                <li
                  key={`${item.reward_type}:${item.reference_key}`}
                  className="flex items-center gap-3 px-4 py-2 text-sm"
                >
                  <div className="min-w-0 flex-1">
                    <div className="truncate">{item.label}</div>
                    <div className="text-xs text-muted-foreground">
                      {item.claimable
                        ? money(item.amount)
                        : t(`economy:rewards.reason.${item.reason}`, item.reason)}
                    </div>
                  </div>
                  <button
                    type="button"
                    className="rounded-md border border-border px-3 py-1 text-xs disabled:opacity-50"
                    disabled={!item.claimable || claim.isPending}
                    onClick={() =>
                      claim.mutate({
                        rewardType: item.reward_type,
                        referenceKey: item.reference_key,
                      })
                    }
                  >
                    {item.claimable ? t("economy:rewards.claim") : t("economy:rewards.claimed")}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="rounded-lg border border-border">
          <h2 className="border-b border-border px-4 py-3 text-sm font-medium">
            {t("economy:personal.title")}
          </h2>
          {wallet.isLoading ? (
            <Skeleton className="m-4 h-16 w-auto" />
          ) : wallet.data ? (
            <div className="space-y-2 px-4 py-3 text-sm">
              <div className="flex justify-between">
                <span className="text-muted-foreground">{t("economy:wallet.available")}</span>
                <span className="font-medium">{money(wallet.data.available_balance)}</span>
              </div>
              <p className="text-xs text-muted-foreground">{t("economy:personal.hint")}</p>
              <ul className="divide-y divide-border text-xs">
                {wallet.data.transactions.items.slice(0, 5).map((tx) => (
                  <li key={tx.transaction_id} className="flex justify-between py-1">
                    <span className="truncate">{tx.reason || tx.transaction_type}</span>
                    <span>{money(tx.amount)}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <EmptyState title={t("economy:personal.empty")} />
          )}
        </section>
      </div>

      <section className="rounded-lg border border-border">
        <h2 className="border-b border-border px-4 py-3 text-sm font-medium">
          {t("economy:transactions.title")}
        </h2>
        {(transactions.data?.items ?? []).length === 0 ? (
          <EmptyState title={t("economy:transactions.empty")} />
        ) : (
          <table className="w-full text-sm">
            <thead className="text-xs text-muted-foreground">
              <tr>
                <th className="px-4 py-2 text-left font-normal">
                  {t("economy:transactions.kind")}
                </th>
                <th className="px-4 py-2 text-left font-normal">
                  {t("economy:transactions.reference")}
                </th>
                <th className="px-4 py-2 text-right font-normal">
                  {t("economy:transactions.amount")}
                </th>
                <th className="px-4 py-2 text-right font-normal">
                  {t("economy:transactions.postedAt")}
                </th>
              </tr>
            </thead>
            <tbody>
              {(transactions.data?.items ?? []).map((tx) => (
                <tr key={tx.transaction_id} className="border-t border-border">
                  <td className="px-4 py-2">{tx.reason || tx.transaction_type}</td>
                  <td className="px-4 py-2 text-xs text-muted-foreground">
                    {tx.reference_type ? `${tx.reference_type}:${tx.reference_id}` : "—"}
                  </td>
                  <td className="px-4 py-2 text-right">{money(tx.amount)}</td>
                  <td className="px-4 py-2 text-right text-xs text-muted-foreground">
                    {new Date(tx.posted_at).toLocaleString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
