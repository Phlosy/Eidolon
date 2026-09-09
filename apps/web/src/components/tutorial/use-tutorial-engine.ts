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
  usePractice,
  useResumeTutorial,
  useSkipPractice,
  useSkipTutorialStep,
  useTutorial,
  useTutorialDefinition,
} from "../../hooks/useTutorial";
import type { TutorialTargetSnapshot } from "./target-registry";
import { useFirstVisibleHint, useTutorialTarget } from "./use-tutorial-target";
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

/** 向导内部指引：只决定"光打在向导的哪个控件上"，不决定完成。 */
export interface TutorialUiHint {
  targetId: string;
  textKey: string;
}

function uiHintsOf(step: TutorialStep | null): TutorialUiHint[] {
  const raw = step?.metadata?.ui_hints;
  if (!Array.isArray(raw)) return [];
  return raw
    .map((item) => {
      const entry = item as { target_id?: unknown; text_key?: unknown };
      return typeof entry.target_id === "string" && typeof entry.text_key === "string"
        ? { targetId: entry.target_id, textKey: entry.text_key }
        : null;
    })
    .filter((item): item is TutorialUiHint => item !== null);
}

/**
 * 教学卡片"下一步"的门禁（纯函数，可测）：
 * 需要先在聚光灯处操作（engage_to_advance）且还没操作、且目标可见时，
 * 点了下一步不推进，转成聚光灯提醒。
 */
export function shouldRemindStep(
  needsEngagement: boolean,
  engaged: boolean,
  targetVisible: boolean,
): boolean {
  return needsEngagement && !engaged && targetVisible;
}

function modalOpen(): boolean {
  return Boolean(document.querySelector('[role="dialog"][aria-modal="true"]'));
}

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
  /** 弹窗打开且这一步有向导指引时，聚光灯跟随向导内部元素 */
  hint: TutorialUiHint | null;
  hints: TutorialUiHint[];
  hintIndex: number;
  /** 当前展示的是可选的实战教程（决定"退出"是暂停还是暂时跳过） */
  isPractice: boolean;
  exitLabelKey: string;
  nextHint: () => void;
  previousHint: () => void;
  acknowledge: () => void;
  /** 步骤要求"先在聚光灯处操作"（step.metadata.engage_to_advance） */
  needsEngagement: boolean;
  /** 当前步骤的聚光灯目标已被操作过 —— 教学卡片不再要求重复下一步 */
  engaged: boolean;
  /** 点了下一步但没操作 → 聚光灯提醒（remind 当前步骤 id） */
  remindStep: string | null;
  dismissRemind: () => void;
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
  const { data: coreProgress } = useTutorial();
  const { data: coreDefinition } = useTutorialDefinition();
  const { data: practiceData } = usePractice();
  const skipPractice = useSkipPractice();
  const replay = useReplaySession();

  const completeStep = useCompleteTutorialStep();
  const skipStep = useSkipTutorialStep();
  const pause = usePauseTutorial();
  const resume = useResumeTutorial();
  const [dismissedError, setDismissedError] = useState<string | null>(null);

  // 两个教程各自独立评分：核心优先；核心不在展示态（未开始 / 已完成 / 已跳过）
  // 时才轮到实战教程 —— 否则用户从教程中心"重新开始实战"后根本看不到聚光灯
  // （实测过：实战进度明明是 active，界面一片安静）。
  const coreSteps = useMemo(() => flattenSteps(coreDefinition), [coreDefinition]);
  const practiceSteps = useMemo(
    () => flattenSteps(practiceData?.definition ?? undefined),
    [practiceData?.definition],
  );
  const coreStep = liveStepFor(coreProgress ?? undefined, coreSteps);
  const practiceStep = liveStepFor(practiceData?.progress ?? undefined, practiceSteps);
  const isPractice = !replay && !coreStep && Boolean(practiceStep);
  const steps = isPractice ? practiceSteps : coreSteps;
  const progress = isPractice ? (practiceData?.progress ?? null) : (coreProgress ?? null);
  const ordered = replay ? replay.steps : steps;
  const step = replay ? (replay.steps[replay.index] ?? null) : isPractice ? practiceStep : coreStep;
  const mode: TutorialEngine["mode"] = replay ? "replay" : step ? "live" : "hidden";

  const context = (progress?.context ?? {}) as Record<string, unknown>;
  const resolvedRoute = step ? resolveRoute(step.route, context) : "";
  const pendingParams = step ? unresolvedRouteParams(resolvedRoute) : [];
  // 只有声明了参数的"资源页"才允许把子路径算作同一页（/projects/7/reviews 仍是
  // /projects/{id} 那一步）。索引页必须精确匹配：步骤要的是 /employees 列表，
  // 人停在 /employees/21 详情页时不能当成"已经在页面上"，否则目标永远找不到、
  // 自动跳转也永远不触发（实测卡在第 10 步就是这个原因）。
  const declaredHasParam = /\{[^}]+\}/.test(step?.route ?? "");
  const onRoute = Boolean(
    resolvedRoute &&
    (location.pathname === resolvedRoute ||
      (declaredHasParam && location.pathname.startsWith(`${resolvedRoute}/`))),
  );
  const excluded = EXCLUDED_ROUTES.some((pattern) => pattern.test(location.pathname));
  const stepDone = Boolean(step && progress?.completed_steps.includes(step.id));
  const stepSkipped = Boolean(step && progress?.skipped_steps.includes(step.id));

  const targetKey =
    step && typeof step.metadata?.target_key === "string" ? step.metadata.target_key : null;
  const active = Boolean(step) && mode !== "hidden" && !excluded;
  const stepHints = uiHintsOf(step);
  // 惰性同步读取：如果 useState(false) + effect 里再设，首帧的自动导航 effect
  // 会赶在弹窗状态生效之前把用户从弹窗里拽走（实测过）。
  const [dialogOpen, setDialogOpen] = useState(modalOpen);
  const [manualHint, setManualHint] = useState<number | null>(null);
  const [engagedStep, setEngagedStep] = useState<string | null>(null);
  const [remindStep, setRemindStep] = useState<string | null>(null);
  const lastVisibleHint = useRef(0);
  useEffect(() => {
    // 之后靠 MutationObserver 跟进弹窗开关，不轮询
    const observer = new MutationObserver(() => setDialogOpen(modalOpen()));
    observer.observe(document.body, { childList: true, subtree: true, attributes: true });
    setDialogOpen(modalOpen());
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    // 换步骤就从"跟随向导"重新开始；聚光灯操作与提醒也按步清零
    setManualHint(null);
    lastVisibleHint.current = 0;
    setEngagedStep(null);
    setRemindStep(null);
  }, [step?.id]);
  const hintCandidate =
    Boolean(step) && active && stepHints.length > 0 && step?.kind !== "INFORMATION";
  // 向导每翻一步只渲染当前那一段 DOM，所以"此刻可见的指引目标"就是用户真正
  // 所在的那一步 —— 用它自动跟随。
  const visibleHintIndex = useFirstVisibleHint(
    hintCandidate ? stepHints.map((item) => item.targetId) : [],
  );
  useEffect(() => {
    // 向导步骤变了 → 放弃手翻，回到跟随。纯自动跟随会永远压掉手翻，两条路都得留。
    if (visibleHintIndex >= 0 && visibleHintIndex !== lastVisibleHint.current) {
      lastVisibleHint.current = visibleHintIndex;
      setManualHint(null);
    }
  }, [visibleHintIndex]);
  const hinting = hintCandidate && (dialogOpen || visibleHintIndex >= 0);
  const hintFloor = visibleHintIndex >= 0 ? visibleHintIndex : lastVisibleHint.current;
  const workingIndex = Math.min(
    Math.max(0, manualHint ?? hintFloor),
    Math.max(0, stepHints.length - 1),
  );
  const hint = hinting ? (stepHints[workingIndex] ?? null) : null;
  const needsEngagement = Boolean(
    step && step.metadata && step.metadata.engage_to_advance === true,
  );
  const onTargetInteract = () => {
    if (!step || mode !== "live") return;
    setEngagedStep(step.id);
    setRemindStep(null);
    // 操作过的"下一步/确定"可以覆盖教学卡片的下一步：指引模式下自动前进一条，
    // 已完成的不重复要求（完成与否仍由后端 reconcile 决定）。
    if (!stepDone && hinting) {
      const peek = Math.min(workingIndex + 1, Math.max(0, stepHints.length - 1));
      if (peek > workingIndex) setManualHint(peek);
    }
  };
  const snapshot = useTutorialTarget(
    active ? (hint ? hint.targetId : (step?.target_id ?? null)) : null,
    hint ? null : targetKey,
    onTargetInteract,
  );

  // 每一步最多自动跳转一次；有弹窗打开时不抢方向盘
  const navigated = useRef(new Set<string>());
  useEffect(() => {
    if (mode !== "live" || !step || progress?.status !== "active") return;
    if (onRoute || stepDone || pendingParams.length) return;
    if (navigated.current.has(step.id)) return;
    if (dialogOpen) {
      // 弹窗开着时把这一步标记为"已给过自动跳转机会"：用户正在对话框里做事
      // （例如填 provider/密钥），弹窗关闭后绝不能再补跳 —— 否则就会把人拽回
      // 上一步的页面（实测：填完 provider 关掉对话框就被拉走）。
      navigated.current.add(step.id);
      return;
    }
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
    dialogOpen,
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
    busy:
      completeStep.isPending ||
      skipStep.isPending ||
      pause.isPending ||
      resume.isPending ||
      skipPractice.isPending,
    errorMessage: dismissedError ? null : rawError,
    needsEngagement,
    engaged: Boolean(step && engagedStep === step.id),
    remindStep,
    dismissRemind: () => setRemindStep(null),
    acknowledge: () => {
      clearError();
      if (step && mode === "live") {
        if (
          shouldRemindStep(needsEngagement, engagedStep === step.id, snapshot.status === "visible")
        ) {
          // 教学卡片点了下一步但界面还没操作完：不推进，让聚光灯再次提醒
          setRemindStep(step.id);
          return;
        }
        setRemindStep(null);
        completeStep.mutate(step.id);
      }
    },
    skipStep: () => {
      clearError();
      if (step && mode === "live") skipStep.mutate(step.id);
    },
    // 核心教程：收成小药丸（可恢复）。实战教程本身就是可选的，"退出"就是暂时跳过
    // —— 零副作用，之后还能从教程中心重新开始。
    togglePause: () => {
      clearError();
      if (isPractice) {
        skipPractice.mutate();
        return;
      }
      if (progress?.status === "paused") resume.mutate();
      else pause.mutate();
    },
    isPractice,
    exitLabelKey: isPractice ? "library.skipPractice" : "ui.pause",
    goToTarget: () => {
      if (!step || !resolvedRoute) return;
      navigated.current.add(step.id);
      navigate(resolvedRoute);
    },
    hint,
    hints: stepHints,
    hintIndex: hint ? workingIndex : -1,
    // 手翻以当前生效位置为基准，否则自动跟随会被旧索引拽回去
    nextHint: () => setManualHint(Math.min(workingIndex + 1, Math.max(0, stepHints.length - 1))),
    previousHint: () => setManualHint(Math.max(0, workingIndex - 1)),
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
