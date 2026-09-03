import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { QueryKey } from "@tanstack/react-query";
import {
  completeTutorialStep,
  deferTutorialQa,
  getClassicSnakeTemplate,
  getPractice,
  getPracticePreview,
  getTutorial,
  getTutorialDefinition,
  getTutorialLibrary,
  pauseTutorial,
  resumePractice,
  resumeTutorial,
  skipPractice,
  skipTutorial,
  skipTutorialStep,
  startPractice,
  startTutorial,
} from "../api/tutorial";
import type { TutorialProgress } from "../api/tutorial";

/**
 * 教程数据 hooks。
 *
 * 进度刷新是**事件驱动**的：业务 mutation 成功 → invalidate(["tutorial"])；
 * WS 的 tutorial.* / 业务域事件 → 同样 invalidate（见 useEventStream）。
 * 30–60s 的轮询只是"事件丢了也要能恢复"的兜底，不是主路径。
 */

export const TUTORIAL_QUERY_KEY: QueryKey = ["tutorial"];
export const PRACTICE_RECONCILE_INTERVAL_MS = 45_000;

export function useTutorial() {
  return useQuery({
    queryKey: TUTORIAL_QUERY_KEY,
    queryFn: getTutorial,
    refetchInterval: PRACTICE_RECONCILE_INTERVAL_MS,
  });
}

export function useTutorialDefinition() {
  return useQuery({
    // 定义随版本变化，会话内基本是静态的：不做轮询、不做窗口聚焦重取
    queryKey: [...TUTORIAL_QUERY_KEY, "definition"],
    queryFn: getTutorialDefinition,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });
}

export function useTutorialLibrary() {
  return useQuery({
    queryKey: [...TUTORIAL_QUERY_KEY, "library"],
    queryFn: getTutorialLibrary,
    refetchOnWindowFocus: false,
  });
}

export function usePractice() {
  return useQuery({
    queryKey: [...TUTORIAL_QUERY_KEY, "practice"],
    queryFn: getPractice,
    refetchInterval: PRACTICE_RECONCILE_INTERVAL_MS,
  });
}

/** 只在成本确认弹窗打开时才取：预览是只读的，但没必要一直占着请求。 */
export function usePracticePreview(enabled: boolean) {
  return useQuery({
    queryKey: [...TUTORIAL_QUERY_KEY, "practice", "preview"],
    queryFn: getPracticePreview,
    enabled,
    staleTime: 10_000,
  });
}

/**
 * 所有会改变进度的操作共用这一个包装：
 * 后端返回值直接写进缓存，同时整棵 ["tutorial"] 子树失效，
 * 这样实战/核心两条进度不会因为只更新其中一个 key 而各自过期。
 *
 * TArg 用 void 表示"无参动作"，这样 mutate() 与 mutate(step) 都能类型正确。
 */
function useTutorialAction<TArg = void>(action: (arg: TArg) => Promise<TutorialProgress>) {
  const queryClient = useQueryClient();
  return useMutation<TutorialProgress, Error, TArg | undefined>({
    // 教程自己的 mutation 已经把返回值写进缓存了，让全局 mutationCache 跳过它
    meta: { tutorialOwned: true },
    mutationFn: (arg) => action(arg as TArg),
    onSuccess: (progress) => {
      queryClient.setQueryData(TUTORIAL_QUERY_KEY, progress);
      queryClient.invalidateQueries({ queryKey: TUTORIAL_QUERY_KEY });
    },
  });
}

export function useStartTutorial() {
  return useTutorialAction(startTutorial);
}

export function usePauseTutorial() {
  return useTutorialAction(pauseTutorial);
}

export function useResumeTutorial() {
  return useTutorialAction(resumeTutorial);
}

export function useSkipTutorial() {
  return useTutorialAction(skipTutorial);
}

export function useCompleteTutorialStep() {
  return useTutorialAction<string>(completeTutorialStep);
}

export function useSkipTutorialStep() {
  return useTutorialAction<string>(skipTutorialStep);
}

export function useDeferTutorialQa() {
  return useTutorialAction(deferTutorialQa);
}

export function useStartPractice() {
  return useTutorialAction(startPractice);
}

export function useResumePractice() {
  return useTutorialAction(resumePractice);
}

export function useSkipPractice() {
  return useTutorialAction(skipPractice);
}

export function useClassicSnakeTemplate(enabled = true) {
  return useQuery({
    queryKey: [...TUTORIAL_QUERY_KEY, "templates", "classic-snake"],
    queryFn: getClassicSnakeTemplate,
    enabled,
    staleTime: Infinity,
  });
}
