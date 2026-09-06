import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { previewBehavior } from "../api/behavior";

/**
 * 招聘向导的"这个人格会变成什么工作方式"预览。
 * `enabled` 由调用方控制（只在人格步骤挂载），避免每步都发请求。
 */
export function useBehaviorPreview(
  trait: string,
  value: number,
  learningEnabled = true,
  enabled = true,
) {
  return useQuery({
    queryKey: ["behavior-preview", trait, value, learningEnabled],
    queryFn: () => previewBehavior(trait, value, learningEnabled),
    enabled,
    // 拖动滑杆时连续请求：保留上一帧数据避免摘要闪白
    placeholderData: keepPreviousData,
  });
}
