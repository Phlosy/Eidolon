import { get, post } from "./client";
import type { LedgerTransactionView } from "./economy";

/**
 * M1.3/M1.4 工作订单市场（docs/m1-economy-design.md §17/§18/§19）。
 *
 * 玩家能做的两件事：**领取**（OPEN → ACCEPTED）与**提交**（交付 ⇒ auto 验收 ⇒ 结算）。
 * 发布（官方发行）与验收/结算不在玩家面（§32 三层边界）。
 */

export interface EscrowView {
  escrow_id: number;
  status: string;
  amount: number;
  currency: string;
  account_balance: number;
  payee_company_id: number | null;
}

export interface WorkOrderView {
  work_order_id: number;
  code: string;
  kind: string;
  title: string;
  description: string;
  requirements: Record<string, unknown>;
  deliverables: Record<string, unknown>;
  reward_amount: number;
  currency: string;
  funding_mode: string;
  evaluation_mode: string;
  status: string;
  deadline_at: string | null;
  issuer_actor_kind: string;
  accepted_at: string | null;
  submitted_at: string | null;
  settled_at: string | null;
  assignee_company_id: number | null;
  issuer_company_id: number | null;
  is_mine: boolean;
  is_issuer: boolean;
  escrow: EscrowView | null;
  submission_count: number;
  payable_amount: number;
  policy_version: string;
}

export interface WorkOrderDetail extends WorkOrderView {
  submissions: Array<{
    submission_id: number;
    attempt: number;
    company_id: number;
    summary: string;
    deliverables: Record<string, unknown>;
    artifact_refs: string[];
    project_id: number | null;
    created_at: string;
  }>;
  evaluations: Array<{
    evaluation_id: number;
    mode: string;
    verdict: string;
    score: number | null;
    bonuses: Record<string, number>;
    notes: string;
    created_at: string;
  }>;
}

export interface WorkOrderPage {
  items: WorkOrderView[];
  total: number;
  limit: number;
  offset: number;
}

export interface WorkOrderFilters {
  status?: string;
  kind?: string;
  mine?: boolean;
  limit?: number;
  offset?: number;
}

export function getWorkOrders(filters: WorkOrderFilters = {}): Promise<WorkOrderPage> {
  const params = new URLSearchParams();
  if (filters.status) params.set("status", filters.status);
  if (filters.kind) params.set("kind", filters.kind);
  if (filters.mine) params.set("mine", "true");
  params.set("limit", String(filters.limit ?? 20));
  params.set("offset", String(filters.offset ?? 0));
  return get<WorkOrderPage>(`/work-orders?${params.toString()}`);
}

export function getWorkOrder(orderId: number): Promise<WorkOrderDetail> {
  return get<WorkOrderDetail>(`/work-orders/${orderId}`);
}

/** 领取订单（并发只有一个赢家；重复领取幂等）。 */
export function acceptWorkOrder(orderId: number): Promise<WorkOrderDetail> {
  return post<WorkOrderDetail>(`/work-orders/${orderId}/accept`, {});
}

export interface WorkOrderSubmissionInput {
  summary: string;
  deliverables?: Record<string, string>;
  artifactRefs?: string[];
  projectId?: number | null;
}

/** 提交交付物（auto 模式同一请求内完成验收与结算）。 */
export function submitWorkOrder(
  orderId: number,
  input: WorkOrderSubmissionInput,
): Promise<WorkOrderDetail> {
  return post<WorkOrderDetail>(`/work-orders/${orderId}/submit`, {
    summary: input.summary,
    deliverables: input.deliverables ?? {},
    artifact_refs: input.artifactRefs ?? [],
    project_id: input.projectId ?? null,
  });
}

export type { LedgerTransactionView };
