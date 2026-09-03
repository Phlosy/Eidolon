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

function lastVisibleHint(targetIds: string[]): number {
  // 取"最后一个可见"，不是第一个：指引是一条有顺序的路径，用户已经走到的
  // 最远处才是他该看的那一条。实测标签页流程（先开"运行时"页签 → 再绑 Provider）
  // 里页签按钮一直可见，用 findIndex 会永远停在第 1 条。
  for (let i = targetIds.length - 1; i >= 0; i -= 1) {
    if (readTargetSnapshot({ id: targetIds[i] }).status === "visible") return i;
  }
  return -1;
}

/**
 * 返回 targetIds 中"最后一个此刻可见"的下标（全不可见 → -1）。
 * 向导每翻一步只渲染当前那一段 DOM；标签页流程里前面的目标会一直可见 ——
 * 两种情况下"最远处可见"都正好等于用户当前所在的那一段指引。
 *
 * 必须走订阅而不是渲染期读一次：实测引擎只订阅"当前那个"指引目标，向导换段落时
 * 没有任何东西通知它，光就永远停在第一条指引上。
 */
export function useFirstVisibleHint(targetIds: string[]): number {
  const key = targetIds.join("|");
  const ids = key ? key.split("|") : [];
  const [index, setIndex] = useState(() => lastVisibleHint(ids));
  useEffect(() => {
    if (!ids.length) {
      setIndex(-1);
      return undefined;
    }
    // subscribe() 会立刻回一次当前值，所以首帧不空白，之后由 MutationObserver 驱动
    const offs = ids.map((id) =>
      tutorialTargets.subscribe({ id }, () => setIndex(lastVisibleHint(ids))),
    );
    return () => offs.forEach((off) => off());
  }, [key]);
  return index;
}
