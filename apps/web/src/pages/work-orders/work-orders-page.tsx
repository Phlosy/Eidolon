import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useAcceptWorkOrder, useSubmitWorkOrder, useWorkOrders } from "../../hooks/useWorkOrders";
import { EmptyState, ErrorState } from "../../components/common/states";
import { Skeleton } from "../../components/common/skeleton";
import type { WorkOrderView } from "../../api/workOrders";

function money(value: number): string {
  return value.toLocaleString();
}

/**
 * 工作订单市场（M1.3/M1.4）：在招订单 + 我承接的；领取 → 交付（auto 验收即结算）。
 *
 * 只有这两步是玩家面（§32）；发布（官方发行）与验收/结算在 CLI/管理面。
 */
export function WorkOrdersPage() {
  const { t } = useTranslation();
  const [mine, setMine] = useState(false);
  const [summaryByOrder, setSummaryByOrder] = useState<Record<number, string>>({});
  const list = useWorkOrders({ mine, limit: 30 });
  const accept = useAcceptWorkOrder();
  const submit = useSubmitWorkOrder();

  const items = list.data?.items ?? [];

  return (
    <div className="space-y-5 panel-enter">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold">{t("workOrders:title")}</h1>
          <p className="mt-1 max-w-3xl text-xs leading-5 text-muted-foreground">
            {t("workOrders:description")}
          </p>
        </div>
        <button
          type="button"
          className="rounded-md border border-border px-3 py-1.5 text-xs"
          onClick={() => setMine((value) => !value)}
        >
          {mine ? t("workOrders:filter.all") : t("workOrders:filter.mine")}
        </button>
      </header>

      {list.isLoading ? (
        <Skeleton className="h-32 w-full" />
      ) : list.isError ? (
        <ErrorState error={list.error} onRetry={() => void list.refetch()} />
      ) : items.length === 0 ? (
        <EmptyState title={mine ? t("workOrders:empty.mine") : t("workOrders:empty.market")} />
      ) : (
        <ul className="space-y-3">
          {items.map((order) => (
            <WorkOrderCard
              key={order.work_order_id}
              order={order}
              summary={summaryByOrder[order.work_order_id] ?? ""}
              onSummaryChange={(value) =>
                setSummaryByOrder((current) => ({ ...current, [order.work_order_id]: value }))
              }
              onAccept={() => accept.mutate(order.work_order_id)}
              onSubmit={() =>
                submit.mutate({
                  orderId: order.work_order_id,
                  input: { summary: summaryByOrder[order.work_order_id] ?? "" },
                })
              }
              busy={accept.isPending || submit.isPending}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

function WorkOrderCard({
  order,
  summary,
  busy,
  onSummaryChange,
  onAccept,
  onSubmit,
}: {
  order: WorkOrderView;
  summary: string;
  busy: boolean;
  onSummaryChange: (value: string) => void;
  onAccept: () => void;
  onSubmit: () => void;
}) {
  const { t } = useTranslation();
  const canAccept = order.status === "OPEN";
  const canSubmit = order.status === "ACCEPTED" || order.status === "IN_PROGRESS";
  const requiredKeys = Array.isArray(order.deliverables?.required_keys)
    ? (order.deliverables.required_keys as string[])
    : [];

  return (
    <li className="rounded-lg border border-border p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-medium">{order.title}</span>
            <span className="rounded bg-surface-elevated px-2 py-0.5 text-[11px] text-muted-foreground">
              {t(`workOrders:status.${order.status}`, order.status)}
            </span>
            {order.is_mine ? (
              <span className="rounded bg-surface-elevated px-2 py-0.5 text-[11px]">
                {t("workOrders:badge.mine")}
              </span>
            ) : null}
          </div>
          <div className="mt-1 text-xs text-muted-foreground">
            {order.code} · {order.kind} ·{" "}
            {t("workOrders:reward", { amount: money(order.reward_amount) })}
            {order.deadline_at
              ? ` · ${t("workOrders:deadline", { at: new Date(order.deadline_at).toLocaleDateString() })}`
              : ""}
          </div>
          {order.escrow ? (
            <div className="mt-1 text-xs text-muted-foreground">
              {t("workOrders:escrow", {
                amount: money(order.escrow.amount),
                status: order.escrow.status,
              })}
            </div>
          ) : null}
          {requiredKeys.length > 0 ? (
            <div className="mt-1 text-xs text-muted-foreground">
              {t("workOrders:required", { keys: requiredKeys.join(", ") })}
            </div>
          ) : null}
        </div>
        <div className="flex items-center gap-2">
          {canAccept ? (
            <button
              type="button"
              className="rounded-md border border-border px-3 py-1.5 text-xs disabled:opacity-50"
              disabled={busy}
              onClick={onAccept}
            >
              {t("workOrders:action.accept")}
            </button>
          ) : null}
          {canSubmit ? (
            <button
              type="button"
              className="rounded-md border border-border px-3 py-1.5 text-xs disabled:opacity-50"
              disabled={busy}
              onClick={onSubmit}
            >
              {t("workOrders:action.submit")}
            </button>
          ) : null}
        </div>
      </div>
      {canSubmit ? (
        <textarea
          className="mt-3 w-full rounded-md border border-border bg-transparent px-3 py-2 text-sm"
          rows={2}
          placeholder={t("workOrders:submitPlaceholder")}
          value={summary}
          onChange={(event) => onSummaryChange(event.target.value)}
        />
      ) : null}
      {order.payable_amount > 0 ? (
        <div className="mt-2 text-xs text-muted-foreground">
          {t("workOrders:payable", { amount: money(order.payable_amount) })}
        </div>
      ) : null}
    </li>
  );
}
