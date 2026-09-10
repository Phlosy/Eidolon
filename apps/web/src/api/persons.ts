import { get } from "./client";
import type { EmployeeCapabilities, TraitView } from "../types";
import type { EducationEvent } from "./cultivation";

/**
 * T2.1 Person 读面（docs/t2-talent-market-design.md §9）。
 * 类型对齐 apps/server/app/schemas/person.py；改动后端 schema 时同步这里。
 *
 * 语义铁律：无证据 → `score/confidence = null`（**永不 0**），后端把未评估维度统一给
 * `status="unrated"`、`trend_direction="unknown"`。
 */

export interface PersonIdentity {
  person_id: number;
  name: string;
  slug: string;
  avatar: string;
  /** 培养档案字段：没有 CharacterProfile 的纯入职员工为 null。 */
  identity_id: string | null;
  origin: string | null;
  cultivation_state: string | null;
  owner_company_id: number | null;
  created_at: string;
}

export interface PersonKnowledgeTopic {
  topic: string;
  count: number;
}

/** 知识摘要：只有统计与主题，**没有正文**（后端有意如此，市场投影复用同一形状）。 */
export interface PersonKnowledgeSummary {
  total: number;
  by_scope: Record<string, number>;
  top_topics: PersonKnowledgeTopic[];
}

export interface PersonEvidence {
  id: number;
  person_id: number | null;
  competency_definition_id: number;
  competency_code: string;
  competency_name: string;
  source_kind: string;
  source_id: number | null;
  source_ref: string;
  assessment_run_id: number | null;
  signal: number | null;
  quality: number | null;
  occurred_at: string;
}

export interface PersonProfile {
  identity: PersonIdentity;
  traits: TraitView[];
  competencies: EmployeeCapabilities;
  knowledge_summary: PersonKnowledgeSummary;
  /** 未请求（include 缺省）时为 null；请求了但没有事件时为 []。 */
  timeline: EducationEvent[] | null;
  evidence: PersonEvidence[] | null;
}

export type PersonInclude = "timeline" | "evidence";

export function getPersonProfile(
  personId: number,
  options: { include?: PersonInclude[]; timelineLimit?: number; evidenceLimit?: number } = {},
): Promise<PersonProfile> {
  const params = new URLSearchParams();
  if (options.include?.length) params.set("include", options.include.join(","));
  if (options.timelineLimit != null) params.set("timeline_limit", String(options.timelineLimit));
  if (options.evidenceLimit != null) params.set("evidence_limit", String(options.evidenceLimit));
  const query = params.size > 0 ? `?${params.toString()}` : "";
  return get<PersonProfile>(`/persons/${personId}${query}`);
}

export function getPersonTimeline(
  personId: number,
  options: { limit?: number; offset?: number } = {},
): Promise<EducationEvent[]> {
  const params = new URLSearchParams();
  if (options.limit != null) params.set("limit", String(options.limit));
  if (options.offset != null) params.set("offset", String(options.offset));
  const query = params.size > 0 ? `?${params.toString()}` : "";
  return get<EducationEvent[]>(`/persons/${personId}/timeline${query}`);
}

export function getPersonEvidence(
  personId: number,
  options: { limit?: number; offset?: number; sourceType?: string; competency?: string } = {},
): Promise<PersonEvidence[]> {
  const params = new URLSearchParams();
  if (options.limit != null) params.set("limit", String(options.limit));
  if (options.offset != null) params.set("offset", String(options.offset));
  if (options.sourceType) params.set("source_type", options.sourceType);
  if (options.competency) params.set("competency", options.competency);
  const query = params.size > 0 ? `?${params.toString()}` : "";
  return get<PersonEvidence[]>(`/persons/${personId}/evidence${query}`);
}
