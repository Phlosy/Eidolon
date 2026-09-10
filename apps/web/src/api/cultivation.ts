import { get, post } from "./client";
import type { EmployeeCapabilities, EmployeeCompetencyView, TraitView } from "../types";

/**
 * T1 培养子系统（docs/cultivation-system-design.md）。
 * 类型对齐 apps/server/app/schemas/cultivation.py；改动后端 schema 时同步这里。
 */

export type CharacterOrigin = "trained" | "blank" | "issued";
/**
 * 培养状态轴（T2 设计 §4）：只允许 `cultivating` / `ready`。
 * `listed`/`hired` 曾是预留值，T2.0 起废弃 —— 市场态走 market listing、
 * 任职态走 employments，不再写进 character_profiles.lifecycle。
 */
export type CharacterLifecycle = "cultivating" | "ready";
export type CultivationTemplateId = "academic" | "vocational" | "self_taught";
export type EducationEventKind =
  "course" | "exam" | "project" | "internship" | "competition" | "fortune";
export type CultivationMode = "web_research" | "document_study" | "knowledge_review";

export interface CultivationProgram {
  id: number;
  template: string;
  current_stage: number;
  /** 模板阶段总数（后端模板注册表透出）。 */
  stages_total: number;
  resource_used: { sessions?: number; knowledge?: number } & Record<string, unknown>;
  status: string;
  created_at: string;
}

/** education_events.outcome 按事件 kind 分形（engine.py 的写入处是唯一事实来源）。 */
export interface EducationEventOutcome {
  stage_id?: string;
  topics?: string[];
  signals?: number[];
  session_ids?: number[];
  knowledge_produced?: number;
  duration_weeks?: number;
  fortunes?: string[];
  assessment_run_id?: number | null;
  /** fortune 事件专属 */
  fortune?: string;
  narrative?: string;
  signal_delta?: number;
  extra_topics?: string[];
  trait_shift?: Record<string, number>;
  /** 自由养成会话专属 */
  signal?: number;
}

export interface EducationEvent {
  id: number;
  program_id: number | null;
  kind: string;
  topic: string;
  outcome: EducationEventOutcome;
  evidence_id: number | null;
  occurred_at: string;
}

/** 列表卡片的培养进度快照（详情页用完整 CultivationProgram）。 */
export interface CultivationProgramSummary {
  template: string;
  current_stage: number;
  stages_total: number;
  status: string;
}

export interface CultivationCharacter {
  id: number;
  person_id: number;
  identity_id: string;
  name: string;
  slug: string;
  origin: string;
  owner_company_id: number | null;
  lifecycle: string;
  created_at: string;
  /** 活跃培养实例摘要；自由养成或全部结束后为 null。 */
  program: CultivationProgramSummary | null;
}

export interface CultivationCharacterDetail extends CultivationCharacter {
  programs: CultivationProgram[];
  events: EducationEvent[];
  /** T1.3 成品档案：人格 8 维，与 /employees/{id}/traits 同构（只读）。 */
  traits: TraitView[];
  /** T1.3 成品档案：证据聚合能力画像，与 /employees/{id}/competencies 同构；
   *  未评估 = score/confidence 为 null（绝不显示 0 分）。 */
  competencies: EmployeeCapabilities;
}

/** 能力维度行：复用员工读面的展示契约（后端 _competencies_out 同一份 payload）。 */
export type CultivationCompetencyView = EmployeeCompetencyView;

export interface CreateCharacterInput {
  name: string;
  origin: CharacterOrigin;
  /** 缺省 = 自由养成（不开 TrainingProgram）。 */
  template?: CultivationTemplateId;
}

export interface AdvanceResult {
  program: CultivationProgram;
  event: EducationEvent;
  lifecycle: string;
}

export interface FreeSessionInput {
  topic: string;
  mode: CultivationMode;
  kind: Exclude<EducationEventKind, "fortune">;
  /** 强度 0-100 → 证据 signal。 */
  signal: number;
}

export interface FreeSessionResult {
  event: EducationEvent;
}

export function listCultivationCharacters(lifecycle?: string): Promise<CultivationCharacter[]> {
  const query = lifecycle ? `?lifecycle=${encodeURIComponent(lifecycle)}` : "";
  return get<CultivationCharacter[]>(`/cultivation/characters${query}`);
}

export function getCultivationCharacter(id: number): Promise<CultivationCharacterDetail> {
  return get<CultivationCharacterDetail>(`/cultivation/characters/${id}`);
}

export function createCultivationCharacter(
  body: CreateCharacterInput,
): Promise<CultivationCharacter> {
  return post<CultivationCharacter>("/cultivation/characters", body);
}

/** T2.2 自由养成显式结业：→ lifecycle=ready（幂等；模板进行中后端 409）。 */
export function completeCultivation(profileId: number): Promise<CultivationCharacter> {
  return post<CultivationCharacter>(`/cultivation/characters/${profileId}/complete`);
}

export function advanceCultivationProgram(programId: number): Promise<AdvanceResult> {
  return post<AdvanceResult>(`/cultivation/programs/${programId}/advance`);
}

export function createFreeSession(
  characterId: number,
  body: FreeSessionInput,
): Promise<FreeSessionResult> {
  return post<FreeSessionResult>(`/cultivation/characters/${characterId}/sessions`, body);
}
