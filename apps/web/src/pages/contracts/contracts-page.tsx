import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  useAcceptContract,
  useCancelContract,
  useContracts,
  useFulfillContract,
} from "../../hooks/useContracts";
import { EmptyState, ErrorState } from "../../components/common/states";
import { Skeleton } from "../../components/common/skeleton";
import type { ContractView } from "../../api/contracts";

function money(value: number | null | undefined): string {
  if (value == null) return "—";
  return Number(value).toLocaleString();
}

/**
 * 合同（M1.6）：对价创建即锁资、履约即结算（多腿：净额 + 平台手续费）、取消/失败退款。
 *
 * 结算明细直接来自账本（`settlement` 是结算时写下的快照）—— 前端只展示，不计算。
 */
export function ContractsPage() {
  const { t } = useTranslation();
  const list = useContracts({ limit: 30 });
  const accept = useAcceptContract();
  const fulfill = useFulfillContract();
  const cancel = useCancelContract();
  const [selected, setSelected] = useState<number | null>(null);

  const items = list.data?.items ?? [];
  const detail = selected != null ? items.find((row) => row.contract_id === selected) : undefined;

  if (list.isLoading) return <Skeleton className="h-40 w-full" />;
  if (list.isError) {
    return <ErrorState error={list.error} onRetry={() => void list.refetch()} />;
  }

  return (
    <div className="space-y-5 panel-enter">
      <header>
        <h1 className="text-lg font-semibold">{t("contracts:title")}</h1>
        <p className="mt-1 max-w-3xl text-xs leading-5 text-muted-foreground">
          {t("contracts:description")}
        </p>
      </header>

      {items.length === 0 ? (
        <EmptyState title={t("contracts:empty")} />
      ) : (
        <ul className="space-y-3">
          {items.map((contract) => (
            <ContractCard
              key={contract.contract_id}
              contract={contract}
              expanded={detail?.contract_id === contract.contract_id}
              busy={accept.isPending || fulfill.isPending || cancel.isPending}
              onToggle={() =>
                setSelected((current) =>
                  current === contract.contract_id ? null : contract.contract_id,
                )
              }
              onAccept={() => accept.mutate(contract.contract_id)}
              onFulfill={() => fulfill.mutate(contract.contract_id)}
              onCancel={() => cancel.mutate(contract.contract_id)}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

function ContractCard({
  contract,
  expanded,
  busy,
  onToggle,
  onAccept,
  onFulfill,
  onCancel,
}: {
  contract: ContractView;
  expanded: boolean;
  busy: boolean;
  onToggle: () => void;
  onAccept: () => void;
  onFulfill: () => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  const canAccept = contract.is_contractor && contract.status === "PENDING_ACCEPTANCE";
  const canFulfill = contract.is_contractor && contract.status === "FUNDED";
  const canCancel =
    (contract.is_issuer || contract.is_contractor) &&
    ["DRAFT", "PENDING_ACCEPTANCE", "ACTIVE"].includes(contract.status);
  const settlement = contract.settlement ?? {};

  return (
    <li className="rounded-lg border border-border p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <button type="button" className="font-medium hover:underline" onClick={onToggle}>
              {contract.title}
            </button>
            <span className="rounded bg-surface-elevated px-2 py-0.5 text-[11px] text-muted-foreground">
              {t(`contracts:status.${contract.status}`, contract.status)}
            </span>
            <span className="rounded bg-surface-elevated px-2 py-0.5 text-[11px] text-muted-foreground">
              {t(`contracts:type.${contract.contract_type}`, contract.contract_type)}
            </span>
          </div>
          <div className="mt-1 text-xs text-muted-foreground">
            {contract.code} ·{" "}
            {t("contracts:consideration", { amount: money(contract.consideration_amount) })}
            {contract.escrow
              ? ` · ${t("contracts:escrow", { amount: money(contract.escrow.amount), status: contract.escrow.status })}`
              : ""}
          </div>
        </div>
        <div className="flex items-center gap-2">
          {canAccept ? (
            <button
              type="button"
              className="rounded-md border border-border px-3 py-1.5 text-xs disabled:opacity-50"
              disabled={busy}
              onClick={onAccept}
            >
              {t("contracts:action.accept")}
            </button>
          ) : null}
          {canFulfill ? (
            <button
              type="button"
              className="rounded-md border border-border px-3 py-1.5 text-xs disabled:opacity-50"
              disabled={busy}
              onClick={onFulfill}
            >
              {t("contracts:action.fulfill")}
            </button>
          ) : null}
          {canCancel ? (
            <button
              type="button"
              className="rounded-md border border-border px-3 py-1.5 text-xs disabled:opacity-50"
              disabled={busy}
              onClick={onCancel}
            >
              {t("contracts:action.cancel")}
            </button>
          ) : null}
        </div>
      </div>

      {expanded ? (
        <dl className="mt-3 grid gap-2 border-t border-border pt-3 text-xs sm:grid-cols-2">
          <div className="flex justify-between">
            <dt className="text-muted-foreground">{t("contracts:detail.issuer")}</dt>
            <dd>{contract.issuer_company_id}</dd>
          </div>
          <div className="flex justify-between">
            <dt className="text-muted-foreground">{t("contracts:detail.contractor")}</dt>
            <dd>{contract.contractor_company_id ?? "—"}</dd>
          </div>
          <div className="flex justify-between">
            <dt className="text-muted-foreground">{t("contracts:detail.settlementTx")}</dt>
            <dd>{contract.settlement_transaction_id ?? "—"}</dd>
          </div>
          {Object.keys(settlement).length > 0 ? (
            <>
              <div className="flex justify-between">
                <dt className="text-muted-foreground">{t("contracts:detail.gross")}</dt>
                <dd>{money(settlement.gross as number)}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-muted-foreground">{t("contracts:detail.fee")}</dt>
                <dd>{money(settlement.fee as number)}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-muted-foreground">{t("contracts:detail.net")}</dt>
                <dd>{money(settlement.net as number)}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-muted-foreground">{t("contracts:detail.split")}</dt>
                <dd>
                  {money(settlement.treasury as number)} / {money(settlement.burn as number)}
                </dd>
              </div>
            </>
          ) : null}
        </dl>
      ) : null}
    </li>
  );
}
