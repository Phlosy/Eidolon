import { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import {
  flattenSteps,
  resolveRoute,
  unresolvedRouteParams,
  type TutorialProgress,
  type TutorialStep,
} from "../../api/tutorial";
import {
  useCompleteTutorialStep,
  usePauseTutorial,
  useResumeTutorial,
  useSkipTutorialStep,
  useTutorial,
  useTutorialDefinition,
} from "../../hooks/useTutorial";
import type { TutorialTargetSnapshot } from "./target-registry";
import { useTutorialTarget } from "./use-tutorial-target";
import { moveReplay, stopReplay, useReplaySession } from "./tutorial-replay";

/**
 * 教程引擎状态机：把"后端说的当前步骤"翻译成"页面上该把光打向哪儿"。
 *
 * 四条硬规则：
 * 1. 完成与否只读后端 progress；这里没有任何"点一下就算完成"的分支。
 * 2. 跨路由靠 step.route + context 占位符解析；每一步最多自动跳转一次，
 *    之后方向盘还给用户（否则用户每点一个链接都被教程拽回去）。
 * 3. Replay 模式纯本地：不读 progress、不发 mutation。
 * 4. 全屏页面（像素办公室、独立评审页）不叠加教程遮罩。
 */

const EXCLUDED_ROUTES: RegExp[] = [/^\/office$/, /\/reviews\//];
const VISIBLE_STATUSES = new Set(["active", "paused"]);

export interface TutorialEngine {
  mode: "live" | "replay" | "hidden";
  step: TutorialStep | null;
  progress: TutorialProgress | null;
  index: number;
  total: number;
  snapshot: TutorialTargetSnapshot;
  stepDone: boolean;
  stepSkipped: boolean;
  onRoute: boolean;
  /** route 里仍有未填充的占位符 —— 目标对象还不存在，只能走兜底 */
  pendingParams: string[];
  resolvedRoute: string;
  busy: boolean;
  errorMessage: string | null;
  acknowledge: () => void;
  skipStep: () => void;
  togglePause: () => void;
  goToTarget: () => void;
  dismissError: () => void;
  replayNext: () => void;
  replayPrevious: () => void;
  exitReplay: () => void;
}

/** API 错误的 detail 才是人能看懂的那句，别把 Error 对象直接渲染出来。 */
function describeError(error: unknown): string | null {
  if (!error) return null;
  const detail = (error as { detail?: unknown }).detail;
  if (typeof detail === "string" && detail) return detail;
  const message = (error as { message?: unknown }).message;
  return typeof message === "string" && message ? message : null;
}

function liveStepFor(
  progress: TutorialProgress | undefined,
  steps: TutorialStep[],
): TutorialStep | null {
  if (!progress || !VISIBLE_STATUSES.has(progress.status) || !steps.length) return null;
  const declared = steps.find((item) => item.id === progress.current_step);
  if (declared) return declared;
  // 后端可能因为新定义发版而指向一个不存在的步骤：退到第一个未完成的真实步骤
  const finished = new Set([...progress.completed_steps, ...progress.skipped_steps]);
  return steps.find((item) => !finished.has(item.id)) ?? steps[steps.length - 1];
}

export function useTutorialEngine(): TutorialEngine {
  const location = useLocation();
  const navigate = useNavigate();
  const { data: progress } = useTutorial();
  const { data: definition } = useTutorialDefinition();
  const replay = useReplaySession();

  const completeStep = useCompleteTutorialStep();
  const skipStep = useSkipTutorialStep();
  const pause = usePauseTutorial();
  const resume = useResumeTutorial();
  const [dismissedError, setDismissedError] = useState<string | null>(null);

  const steps = useMemo(() => flattenSteps(definition), [definition]);
  const ordered = replay ? replay.steps : steps;
  const step = replay ? (replay.steps[replay.index] ?? null) : liveStepFor(progress, steps);
  const mode: TutorialEngine["mode"] = replay ? "replay" : step ? "live" : "hidden";

  const context = (progress?.context ?? {}) as Record<string, unknown>;
  const resolvedRoute = step ? resolveRoute(step.route, context) : "";
  const pendingParams = step ? unresolvedRouteParams(resolvedRoute) : [];
  const onRoute = Boolean(
    resolvedRoute &&
    // /employees/{id} 这类子路由同样算"就在这一步的页面上"
    (location.pathname === resolvedRoute || location.pathname.startsWith(`${resolvedRoute}/`)),
  );
  const excluded = EXCLUDED_ROUTES.some((pattern) => pattern.test(location.pathname));
  const stepDone = Boolean(step && progress?.completed_steps.includes(step.id));
  const stepSkipped = Boolean(step && progress?.skipped_steps.includes(step.id));

  const targetKey =
    step && typeof step.metadata?.target_key === "string" ? step.metadata.target_key : null;
  const active = Boolean(step) && mode !== "hidden" && !excluded;
  const snapshot = useTutorialTarget(active ? (step?.target_id ?? null) : null, targetKey);

  // 每一步最多自动跳转一次；有弹窗打开时不抢方向盘
  const navigated = useRef(new Set<string>());
  useEffect(() => {
    if (mode !== "live" || !step || progress?.status !== "active") return;
    if (onRoute || stepDone || pendingParams.length) return;
    if (navigated.current.has(step.id)) return;
    if (document.querySelector('[role="dialog"][aria-modal="true"]')) return;
    navigated.current.add(step.id);
    navigate(resolvedRoute);
  }, [
    mode,
    step,
    progress?.status,
    onRoute,
    stepDone,
    pendingParams.length,
    resolvedRoute,
    navigate,
  ]);

  const clearError = () => setDismissedError(null);
  const rawError = describeError(completeStep.error) ?? describeError(skipStep.error);

  return {
    mode,
    step,
    progress: progress ?? null,
    index: step
      ? Math.max(
          ordered.findIndex((item) => item.id === step.id),
          0,
        )
      : 0,
    total: ordered.length,
    snapshot,
    stepDone,
    stepSkipped,
    onRoute,
    pendingParams,
    resolvedRoute,
    busy: completeStep.isPending || skipStep.isPending || pause.isPending || resume.isPending,
    errorMessage: dismissedError ? null : rawError,
    acknowledge: () => {
      clearError();
      if (step && mode === "live") completeStep.mutate(step.id);
    },
    skipStep: () => {
      clearError();
      if (step && mode === "live") skipStep.mutate(step.id);
    },
    togglePause: () => {
      clearError();
      if (progress?.status === "paused") resume.mutate();
      else pause.mutate();
    },
    goToTarget: () => {
      if (!step || !resolvedRoute) return;
      navigated.current.add(step.id);
      navigate(resolvedRoute);
    },
    dismissError: clearError,
    replayNext: () => moveReplay(1),
    replayPrevious: () => moveReplay(-1),
    exitReplay: stopReplay,
  };
}

/** 遮罩是否应该渲染。放在 hook 外，方便测试直接断言。 */
export function engineVisible(engine: TutorialEngine): boolean {
  return engine.mode !== "hidden" && engine.step !== null;
}
