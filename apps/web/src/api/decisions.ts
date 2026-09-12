import { get } from "./client";
import type { DecisionStats, DecisionView, ToolAuditView } from "../types";

/** M2.4 决策读面（**只读**）：管理语义 + 执行事实的定位信息。 */
export function listDecisions(params: {
  projectId?: number;
  taskId?: number;
  status?: string;
  limit?: number;
}): Promise<DecisionView[]> {
  const search = new URLSearchParams();
  if (params.projectId != null) search.set("project_id", String(params.projectId));
  if (params.taskId != null) search.set("task_id", String(params.taskId));
  if (params.status) search.set("status", params.status);
  search.set("limit", String(params.limit ?? 50));
  return get<DecisionView[]>(`/decisions?${search.toString()}`);
}

export function getDecision(id: number): Promise<DecisionView> {
  return get<DecisionView>(`/decisions/${id}`);
}

/** 反查执行事实（`ToolAudit.decision_id` 是唯一关联方向）。 */
export function listDecisionToolAudits(id: number): Promise<ToolAuditView[]> {
  return get<ToolAuditView[]>(`/decisions/${id}/tool-audits`);
}

export function getDecisionStats(): Promise<DecisionStats> {
  return get<DecisionStats>("/decisions/stats");
}
