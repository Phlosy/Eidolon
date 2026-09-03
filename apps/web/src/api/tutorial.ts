import { get, post } from "./client";
import type { CreateProjectInput } from "../types";

/**
 * 教程数据层。字段与 app/tutorials/schema.py 一一对应。
 *
 * 前端**不判断业务完成**：这里能读到的唯一事实是 requirement 这个名字（用于
 * 展示"在等哪条业务状态"），完成与否由后端求值后写进 progress。
 */

export type TutorialStepKind = "REQUIRED_ACTION" | "OPTIONAL_ACTION" | "INFORMATION" | "PRACTICE";

/** 遮罩强度。TARGET_ONLY 只放开目标；FOCUS_ONLY 只打光不锁；NON_BLOCKING 不挡操作。 */
export type TutorialInteractionMode = "FOCUS_ONLY" | "TARGET_ONLY" | "NON_BLOCKING";

export type TutorialPlacement = "auto" | "top" | "right" | "bottom" | "left";

export interface TutorialStep {
  id: string;
  kind: TutorialStepKind;
  requirement: string;
  /** 步骤所在路由；`{xxx}` 占位符从 progress.context 取值替换 */
  route: string;
  /** React 侧 Target Registry 的 id，不是 CSS selector */
  target_id: string | null;
  placement: TutorialPlacement;
  interaction_mode: TutorialInteractionMode;
  allow_skip: boolean;
  auto_advance: boolean;
  order: number | null;
  title_key: string;
  description_key: string;
  why_key: string;
  has_why: boolean;
  metadata: Record<string, unknown>;
  stage?: string;
}

export interface TutorialStage {
  id: string;
  title_key: string;
  steps: TutorialStep[];
}

export interface TutorialDefinition {
  id: string;
  version: number;
  title_key: string;
  description_key?: string;
  kind: string;
  sets_operating_stage: boolean;
  allow_skip: boolean;
  stages: TutorialStage[];
}

export interface TutorialProgress {
  id: number;
  user_id: number;
  company_id: number;
  tutorial_id: string;
  status: "not_started" | "active" | "paused" | "skipped" | "completed";
  current_stage: string;
  current_step: string;
  completed_steps: string[];
  skipped_steps: string[];
  context: Record<string, unknown>;
  started_at: string | null;
  completed_at: string | null;
  paused_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface TutorialLibraryEntry {
  id: string;
  title_key: string;
  route: string;
  replayable: boolean;
  practice?: boolean;
}

export interface TutorialLibrary {
  library: TutorialLibraryEntry[];
  center: TutorialLibraryEntry[];
  tutorials: Array<{ definition: TutorialDefinition; progress: TutorialProgress }>;
}

export interface PracticeTeamMember {
  employee_id: number;
  name: string;
  role: string;
  lifecycle_status: string;
  runtime: string | null;
  provider: string | null;
  model: string | null;
}

/** 开始实战前的成本说明。uses_llm 由"非 mock runtime + 有效 provider 绑定"推出。 */
export interface PracticePreview {
  tutorial_id: string;
  template_name: string;
  tutorial_accelerated: boolean;
  uses_llm: boolean;
  mock_only: boolean;
  team: PracticeTeamMember[];
  practice_status: TutorialProgress["status"];
}

export function flattenSteps(definition: TutorialDefinition | undefined): TutorialStep[] {
  if (!definition) return [];
  return definition.stages.flatMap((stage) =>
    stage.steps.map((step) => ({ ...step, stage: stage.id })),
  );
}

/** 把 route 里的 `{ceo_employee_id}` 之类占位符换成 context 里的真实 id。 */
export function resolveRoute(route: string, context: Record<string, unknown>): string {
  return route.replace(/\{([a-z_]+)\}/g, (match, key: string) => {
    const value = context[key];
    return value === undefined || value === null ? match : String(value);
  });
}

/** 占位符没被填充时，这一步其实还去不了（例如 CEO 还没建出来）。 */
export function unresolvedRouteParams(route: string): string[] {
  return [...route.matchAll(/\{([a-z_]+)\}/g)].map((match) => match[1]);
}

export function getTutorial(): Promise<TutorialProgress> {
  return get<TutorialProgress>("/tutorial");
}

export function getTutorialDefinition(): Promise<TutorialDefinition> {
  return get<TutorialDefinition>("/tutorial/definition");
}

export function getTutorialLibrary(): Promise<TutorialLibrary> {
  return get<TutorialLibrary>("/tutorial/library");
}

export function startTutorial(): Promise<TutorialProgress> {
  return post<TutorialProgress>("/tutorial/start");
}

export function pauseTutorial(): Promise<TutorialProgress> {
  return post<TutorialProgress>("/tutorial/pause");
}

export function resumeTutorial(): Promise<TutorialProgress> {
  return post<TutorialProgress>("/tutorial/resume");
}

export function skipTutorial(): Promise<TutorialProgress> {
  return post<TutorialProgress>("/tutorial/skip");
}

export function completeTutorialStep(step: string): Promise<TutorialProgress> {
  return post<TutorialProgress>(`/tutorial/steps/${step}/complete`);
}

export function skipTutorialStep(step: string): Promise<TutorialProgress> {
  return post<TutorialProgress>(`/tutorial/steps/${step}/skip`);
}

export function deferTutorialQa(): Promise<TutorialProgress> {
  return post<TutorialProgress>("/tutorial/defer-qa");
}

export function getClassicSnakeTemplate(): Promise<{ name: string; intake: CreateProjectInput }> {
  return get<{ name: string; intake: CreateProjectInput }>("/tutorial/templates/classic-snake");
}

// ---- First Project Practice ----
export function getPractice(): Promise<{
  definition: TutorialDefinition;
  progress: TutorialProgress;
}> {
  return get("/practice");
}

export function startPractice(): Promise<TutorialProgress> {
  return post<TutorialProgress>("/practice/start");
}

export function resumePractice(): Promise<TutorialProgress> {
  return post<TutorialProgress>("/practice/resume");
}

/** 零成本跳过：后端只写一行状态，测试用表行数差守着它。 */
export function skipPractice(): Promise<TutorialProgress> {
  return post<TutorialProgress>("/practice/skip");
}

export function getPracticePreview(): Promise<PracticePreview> {
  return get<PracticePreview>("/practice/preview");
}
