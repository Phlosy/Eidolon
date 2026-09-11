import { del, get, post } from "./client";
import type { EmployeeCapabilities, TraitView } from "../types";
import type { EducationEvent } from "./cultivation";
import type { PersonKnowledgeSummary } from "./persons";

/**
 * T2.7 人才市场读面（docs/t2-talent-market-design.md §6/§8）。
 * 类型对齐 apps/server/app/schemas/market.py；改动后端 schema 时同步这里。
 *
 * 公开投影纪律：这里**没有** owner id / credential / 知识正文 —— 后端也不会返回。
 */

export interface MarketFitSummary {
  position_definition_id: number;
  fit_status: string;
  qualification_status: string;
  known_fit_score: number | null;
  fit_confidence: number | null;
  requirement_coverage: number;
  known_count: number;
  total_count: number;
}

export interface MarketListing {
  listing_id: number;
  identity_id: string;
  name: string;
  avatar: string;
  origin: string;
  cultivation_state: string;
  status: string;
  quality_tier: string | null;
  listed_at: string;
  closed_at: string | null;
  listed_by: string;
  /** 仅当检索带 position_definition_id 时非空（T2.5）。 */
  fit?: MarketFitSummary | null;
}

export interface MarketListingPage {
  items: MarketListing[];
  total: number;
  limit: number;
  offset: number;
}

export interface MarketFitEvaluation {
  code: string;
  name: string;
  domain_code: string;
  kind: string;
  requirement_type: string;
  critical: boolean;
  minimum_score: number | null;
  target_score: number | null;
  minimum_confidence: number | null;
  /** 候选人侧（null = 未评估，**不是 0**）。 */
  candidate_score: number | null;
  candidate_confidence: number | null;
  evaluation_status: string;
  reason_code: string;
  gap_type: string | null;
  is_unknown: boolean;
  is_strength: boolean;
  is_development_opportunity: boolean;
  margin_to_minimum: number | null;
  margin_to_target: number | null;
}

export interface MarketFit {
  listing_id: number;
  position_definition_id: number;
  position_code: string;
  configured: boolean;
  profile_version_id: number | null;
  profile_version: number | null;
  fit_status: string;
  qualification_status: string;
  known_fit_score: number | null;
  overall_fit_score: number | null;
  fit_confidence: number | null;
  requirement_coverage: number;
  required_coverage: number;
  preferred_coverage: number;
  known_count: number;
  total_count: number;
  general_fit: number | null;
  professional_fit: number | null;
  strengths: MarketFitEvaluation[];
  gaps: MarketFitEvaluation[];
  uncertainties: MarketFitEvaluation[];
  development_opportunities: MarketFitEvaluation[];
  requirement_evaluations: MarketFitEvaluation[];
  engine_version: string;
  policy_version: string;
  serializer_version: string;
  calculated_at: string | null;
}

export interface MarketEvidence {
  id: number;
  competency_code: string;
  competency_name: string;
  source_kind: string;
  source_ref: string;
  assessment_run_id: number | null;
  signal: number | null;
  quality: number | null;
  occurred_at: string;
}

export interface MarketCandidate {
  listing: {
    listing_id: number;
    status: string;
    quality_tier: string | null;
    listed_at: string;
    listed_by: string;
  };
  identity: {
    identity_id: string | null;
    name: string;
    avatar: string;
    origin: string | null;
    cultivation_state: string | null;
  };
  traits: TraitView[];
  competencies: EmployeeCapabilities;
  knowledge_summary: PersonKnowledgeSummary;
  timeline: EducationEvent[];
  evidence: MarketEvidence[];
  market_state: string;
}

export interface MarketListingFilters {
  text?: string;
  origin?: string;
  qualityTier?: string;
  /** 带职位时后端附 Fit 摘要并按匹配度排序（不筛人）。 */
  positionDefinitionId?: number | null;
  /** 只看本公司挂牌（T2.7a 的"我的挂牌"）。 */
  mine?: boolean;
  limit?: number;
  offset?: number;
}

export interface RecruitInput {
  department_id?: number | null;
  position_slot_id?: number | null;
  title?: string | null;
  role?: string | null;
  reason?: string;
}

export interface RecruitResult {
  person_id: number;
  employee_id: number;
  employee_slug: string;
  identity_id: string | null;
  company_id: number;
  listing_id: number;
  position_slot_id: number | null;
  assignment_id: number | null;
}

export function getMarketListings(filters: MarketListingFilters = {}): Promise<MarketListingPage> {
  const params = new URLSearchParams();
  if (filters.text) params.set("text", filters.text);
  if (filters.origin) params.set("origin", filters.origin);
  if (filters.qualityTier) params.set("quality_tier", filters.qualityTier);
  if (filters.positionDefinitionId != null) {
    params.set("position_definition_id", String(filters.positionDefinitionId));
  }
  if (filters.mine) params.set("mine", "true");
  if (filters.limit != null) params.set("limit", String(filters.limit));
  if (filters.offset != null) params.set("offset", String(filters.offset));
  const query = params.size > 0 ? `?${params.toString()}` : "";
  return get<MarketListingPage>(`/market/listings${query}`);
}

export function getMarketListing(listingId: number): Promise<MarketCandidate> {
  return get<MarketCandidate>(`/market/listings/${listingId}`);
}

export function getMarketFit(
  listingId: number,
  positionDefinitionId: number,
  profileVersionId?: number | null,
): Promise<MarketFit> {
  const params = new URLSearchParams({ position_definition_id: String(positionDefinitionId) });
  if (profileVersionId != null) params.set("profile_version_id", String(profileVersionId));
  return get<MarketFit>(`/market/listings/${listingId}/fit?${params.toString()}`);
}

/** 挂牌自己的角色（T2.3；幂等：已挂牌返回既有挂牌）。 */
export function createMarketListing(
  personId: number,
  qualityTier?: string | null,
): Promise<MarketListing> {
  return post<MarketListing>("/market/listings", {
    person_id: personId,
    ...(qualityTier ? { quality_tier: qualityTier } : {}),
  });
}

export function delistMarketListing(listingId: number): Promise<void> {
  return del<void>(`/market/listings/${listingId}`);
}

/** 招募（T2.6）：把既有 Person 变成本公司员工（不复制人级资产）。 */
export function recruitMarketListing(
  listingId: number,
  body: RecruitInput = {},
): Promise<RecruitResult> {
  return post<RecruitResult>(`/market/listings/${listingId}/recruit`, body);
}
