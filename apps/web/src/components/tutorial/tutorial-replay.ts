import { useSyncExternalStore } from "react";
import type { TutorialDefinition, TutorialStep } from "../../api/tutorial";
import { flattenSteps } from "../../api/tutorial";

/**
 * Replay（回看教程）是一个**纯本地**会话。
 *
 * 它刻意不碰任何进度接口：回放只按定义顺序走步骤、展示说明，
 * 完成/跳过/暂停都属于 live 教程的概念，回放进去做只会污染真实进度。
 * §"Replay 不得修改业务数据、不得自动完成任何 REQUIRED_ACTION"。
 */

export interface ReplaySession {
  definition: TutorialDefinition;
  index: number;
  steps: TutorialStep[];
}

let session: ReplaySession | null = null;
const listeners = new Set<() => void>();

function emit() {
  listeners.forEach((listener) => listener());
}

export function startReplay(definition: TutorialDefinition, index = 0): void {
  const steps = flattenSteps(definition);
  session = { definition, steps, index: Math.min(Math.max(0, index), steps.length - 1) };
  emit();
}

export function stopReplay(): void {
  if (!session) return;
  session = null;
  emit();
}

/** 返回是否移动了：到头/到尾时 UI 需要把"下一步"置灰。 */
export function moveReplay(delta: number): boolean {
  if (!session) return false;
  const next = Math.min(Math.max(0, session.index + delta), session.steps.length - 1);
  if (next === session.index) return false;
  session = { ...session, index: next };
  emit();
  return true;
}

export function getReplaySession(): ReplaySession | null {
  return session;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useReplaySession(): ReplaySession | null {
  return useSyncExternalStore(subscribe, getReplaySession, () => null);
}
