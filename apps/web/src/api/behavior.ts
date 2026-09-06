import { get } from "./client";
import type { BehaviorPolicySummary } from "../types";

/**
 * 服务端预览：把一个 0..1 的 trait 换成"会生效成什么工作方式"。
 *
 * 存在的理由：档位边界（0.30 / 0.70）与候选技能线（0.70）只能住在
 * `app/brain/config.py`。前端自己写死一套就会漂移，所以这里只发数字、只读结果。
 */
export function previewBehavior(
  trait: string,
  value: number,
  learningEnabled = true,
): Promise<BehaviorPolicySummary> {
  const params = new URLSearchParams({
    trait,
    value: String(value),
    learning_enabled: String(learningEnabled),
  });
  return get<BehaviorPolicySummary>(`/behavior/preview?${params.toString()}`);
}
