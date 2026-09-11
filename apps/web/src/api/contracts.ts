import { get, post } from "./client";
import type { EscrowView } from "./workOrders";

/**
 * M1.6 合同（docs/m1-economy-design.md §21/§24）。
 *
 * 对价在**创建时**锁进托管；履约即结算（多腿：承接方净额 + 平台手续费）；
 * 取消（`FUNDED` 之前）与失败 ⇒ 退款。前端只调用这四个业务动作。
 */

export interface ContractView {
  contract_id: number;
  code: string;
  contract_type: string;
  title: string;
  subject: string;
  terms: Record<string, unknown>;
  consideration_amount: number;
  currency: string;
  status: string;
  issuer_company_id: number;
  contractor_company_id: number | null;
  reference_type: string;
  reference_id: string;
  effective_at: string | null;
  expires_at: string | null;
  fulfilled_at: string | null;
  settled_at: string | null;
  settlement_transaction_id: number | null;
  policy_version: string;
  is_issuer: boolean;
  is_contractor: boolean;
  escrow: EscrowView | null;
  settlement: Record<string, number | boolean | string | null>;
}

export interface ContractPage {
  items: ContractView[];
  total: number;
  limit: number;
  offset: number;
}

export interface ContractFilters {
  status?: string;
  contractType?: string;
  limit?: number;
  offset?: number;
}

export function getContracts(filters: ContractFilters = {}): Promise<ContractPage> {
  const params = new URLSearchParams();
  if (filters.status) params.set("status", filters.status);
  if (filters.contractType) params.set("contract_type", filters.contractType);
  params.set("limit", String(filters.limit ?? 20));
  params.set("offset", String(filters.offset ?? 0));
  return get<ContractPage>(`/contracts?${params.toString()}`);
}

export function getContract(contractId: number): Promise<ContractView> {
  return get<ContractView>(`/contracts/${contractId}`);
}

/** 承接方接受（`PENDING_ACCEPTANCE → ACTIVE → FUNDED`）。 */
export function acceptContract(contractId: number): Promise<ContractView> {
  return post<ContractView>(`/contracts/${contractId}/accept`, {});
}

/** 承接方交付 ⇒ 结算（多腿放款 + 手续费）。 */
export function fulfillContract(contractId: number): Promise<ContractView> {
  return post<ContractView>(`/contracts/${contractId}/fulfill`, {});
}

/** 当事方取消（`FUNDED` 之前）⇒ 退款。 */
export function cancelContract(contractId: number): Promise<ContractView> {
  return post<ContractView>(`/contracts/${contractId}/cancel`, {});
}
