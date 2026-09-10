import { get, post } from "./client";
import type { KnowledgeItem, KnowledgeScope } from "../types";

/** 晋升目标只允许 department/company —— 后端会拒绝 private。 */
export type PromotionTargetScope = Exclude<KnowledgeScope, "private">;

export interface ListKnowledgeParams {
  scope?: KnowledgeScope;
  topic?: string;
  /** scope=private 时后端强制要求（否则 400）。 */
  employeeId?: number;
}

export function listKnowledgeItems(params: ListKnowledgeParams = {}): Promise<KnowledgeItem[]> {
  const search = new URLSearchParams();
  if (params.scope) search.set("scope", params.scope);
  if (params.topic?.trim()) search.set("topic", params.topic.trim());
  if (params.employeeId != null) search.set("employee_id", String(params.employeeId));
  const query = search.toString();
  return get<KnowledgeItem[]>(`/knowledge${query ? `?${query}` : ""}`);
}

export function proposeKnowledgePromotion(
  itemId: number,
  targetScope: PromotionTargetScope,
): Promise<KnowledgeItem> {
  return post<KnowledgeItem>(`/knowledge/${itemId}/proposals`, { target_scope: targetScope });
}

export function reviewKnowledgeProposal(itemId: number, approve: boolean): Promise<KnowledgeItem> {
  return post<KnowledgeItem>(`/knowledge/${itemId}/review`, { approve });
}
