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
  const [deliverablesByOrder, setDeliverablesByOrder] = useState<
    Record<number, Record<string, string>>
  >({});
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
              deliverables={deliverablesByOrder[order.work_order_id] ?? {}}
              onSummaryChange={(value) =>
                setSummaryByOrder((current) => ({ ...current, [order.work_order_id]: value }))
              }
              onDeliverableChange={(key, value) =>
                setDeliverablesByOrder((current) => ({
                  ...current,
                  [order.work_order_id]: { ...current[order.work_order_id], [key]: value },
                }))
              }
              onAccept={() => accept.mutate(order.work_order_id)}
              onSubmit={() =>
                submit.mutate({
                  orderId: order.work_order_id,
                  input: {
                    summary: summaryByOrder[order.work_order_id] ?? "",
                    deliverables: deliverablesByOrder[order.work_order_id] ?? {},
                  },
                })
              }
              busy={accept.isPending || submit.isPending}
              accepting={accept.isPending && accept.variables === order.work_order_id}
              submitting={submit.isPending && submit.variables?.orderId === order.work_order_id}
              error={
                (submit.variables?.orderId === order.work_order_id && submit.error) ||
                (accept.variables === order.work_order_id && accept.error) ||
                null
              }
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
  deliverables,
  busy,
  accepting,
  submitting,
  error,
  onSummaryChange,
  onDeliverableChange,
  onAccept,
  onSubmit,
}: {
  order: WorkOrderView;
  summary: string;
  deliverables: Record<string, string>;
  busy: boolean;
  accepting: boolean;
  submitting: boolean;
  error: Error | null;
  onSummaryChange: (value: string) => void;
  onDeliverableChange: (key: string, value: string) => void;
  onAccept: () => void;
  onSubmit: () => void;
}) {
  const { t } = useTranslation();
  const canAccept = order.status === "OPEN";
  const canSubmit = ["ACCEPTED", "IN_PROGRESS", "REJECTED"].includes(order.status);
  const requiredKeys = Array.isArray(order.deliverables?.required_keys)
    ? [
        ...new Set(
          order.deliverables.required_keys.filter((key): key is string => typeof key === "string"),
        ),
      ]
    : [];

  return (
    <li className="rounded-lg border border-border p-4">
      <form
        aria-busy={accepting || submitting}
        onSubmit={(event) => {
          event.preventDefault();
          if (canSubmit && !busy) onSubmit();
        }}
      >
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
                {t(accepting ? "workOrders:action.accepting" : "workOrders:action.accept")}
              </button>
            ) : null}
            {canSubmit ? (
              <button
                type="submit"
                className="rounded-md border border-border px-3 py-1.5 text-xs disabled:opacity-50"
                disabled={busy}
              >
                {t(
                  submitting
                    ? "workOrders:action.submitting"
                    : order.status === "REJECTED"
                      ? "workOrders:action.resubmit"
                      : "workOrders:action.submit",
                )}
              </button>
            ) : null}
          </div>
        </div>
        {canSubmit ? (
          <div className="mt-3 space-y-3">
            <label className="block space-y-1.5 text-xs">
              <span className="font-medium">{t("workOrders:summaryLabel")}</span>
              <textarea
                className="w-full rounded-md border border-border bg-transparent px-3 py-2 text-sm disabled:opacity-50"
                rows={2}
                disabled={busy}
                placeholder={t("workOrders:submitPlaceholder")}
                value={summary}
                onChange={(event) => onSummaryChange(event.target.value)}
              />
            </label>
            {requiredKeys.map((key) => (
              <label key={key} className="block space-y-1.5 text-xs">
                <span className="break-words font-medium">{key}</span>
                <textarea
                  className="w-full rounded-md border border-border bg-transparent px-3 py-2 text-sm disabled:opacity-50"
                  rows={2}
                  required
                  disabled={busy}
                  placeholder={t("workOrders:deliverablePlaceholder")}
                  value={deliverables[key] ?? ""}
                  onChange={(event) => onDeliverableChange(key, event.target.value)}
                />
              </label>
            ))}
          </div>
        ) : null}
        {error ? (
          <p role="alert" className="mt-3 text-xs text-danger">
            {error.message || t("common:errorFallback")}
          </p>
        ) : null}
        {order.payable_amount > 0 ? (
          <div className="mt-2 text-xs text-muted-foreground">
            {t("workOrders:payable", { amount: money(order.payable_amount) })}
          </div>
        ) : null}
      </form>
    </li>
  );
}
