import { useEffect, useState } from "react";
import {
  MISSING_SNAPSHOT,
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
