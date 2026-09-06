import type { ModelEntry, ProviderType } from "../../types";

/**
 * 全部可配置的厂商类型（预设清单的静态兜底）。
 * 首选数据源是 GET /providers/presets（useProviderPresets）——这里只用于
 * 预设还没加载完成时的渲染兜底，顺序与后端 PROVIDER_PRESETS 保持一致。
 */
export const PROVIDER_TYPES: ProviderType[] = [
  "openai",
  "anthropic",
  "deepseek",
  "moonshot",
  "zhipu",
  "qwen",
  "groq",
  "mistral",
  "openrouter",
  "gemini",
  "ollama",
  "custom",
];

// 与后端 services/providers.py 的 validate_model_name 同一规则：
// 字母数字开头，可含 . - _ / :（覆盖 ollama tag 与 openrouter 的 org/model）
const MODEL_NAME_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._/:-]{0,199}$/;

/** 模型名校验（提交前在前端先拦一道）。 */
export function isValidModelName(model: string): boolean {
  return MODEL_NAME_PATTERN.test(model);
}

/** 默认条目必须指向一个"选用中"的条目；失效就顶到第一个选用的。 */
export function fixEntriesDefault(
  entries: ModelEntry[],
  primaryModel: string,
): { entries: ModelEntry[]; primaryModel: string } {
  const enabled = entries.filter((entry) => entry.enabled && entry.model.trim());
  const primary = enabled.some((entry) => entry.model === primaryModel)
    ? primaryModel
    : (enabled[0]?.model ?? "");
  return { entries, primaryModel: primary };
}
