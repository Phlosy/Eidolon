import { useEffect, useState } from "react";
import {
  MISSING_SNAPSHOT,
  readTargetSnapshot,
  tutorialTargets,
  type TutorialTargetQuery,
  type TutorialTargetSnapshot,
} from "./target-registry";

/** 订阅某个教程目标当前的位置。query 用原始值进依赖，避免对象字面量每帧重订阅。 */
export function useTutorialTarget(
  targetId: string | null,
  targetKey?: string | null,
): TutorialTargetSnapshot {
  const [snapshot, setSnapshot] = useState<TutorialTargetSnapshot>(MISSING_SNAPSHOT);
  useEffect(() => {
    if (!targetId) {
      setSnapshot(MISSING_SNAPSHOT);
      return undefined;
    }
    const query: TutorialTargetQuery = { id: targetId, key: targetKey ?? null };
    return tutorialTargets.subscribe(query, setSnapshot);
  }, [targetId, targetKey]);
  return snapshot;
}

function firstVisibleHint(targetIds: string[]): number {
  return targetIds.findIndex((id) => readTargetSnapshot({ id }).status === "visible");
}

/**
 * 返回 targetIds 中第一个"此刻可见"的下标（全不可见 → -1）。
 * 向导每翻一步只渲染当前那一段 DOM，所以这个下标就是用户真正走到第几段指引。
 *
 * 必须走订阅而不是渲染期读一次：实测引擎只订阅"当前那个"指引目标，向导换段落时
 * 没有任何东西通知它，光就永远停在第一条指引上。
 */
export function useFirstVisibleHint(targetIds: string[]): number {
  const key = targetIds.join("|");
  const ids = key ? key.split("|") : [];
  const [index, setIndex] = useState(() => firstVisibleHint(ids));
  useEffect(() => {
    if (!ids.length) {
      setIndex(-1);
      return undefined;
    }
    // subscribe() 会立刻回一次当前值，所以首帧不空白，之后由 MutationObserver 驱动
    const offs = ids.map((id) =>
      tutorialTargets.subscribe({ id }, () => setIndex(firstVisibleHint(ids))),
    );
    return () => offs.forEach((off) => off());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return index;
}
