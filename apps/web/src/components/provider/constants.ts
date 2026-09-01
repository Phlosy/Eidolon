import type { ProviderType } from "../../types";

/** All provider types the UI can configure (contract §3.3). */
export const PROVIDER_TYPES: ProviderType[] = [
  "openai",
  "anthropic",
  "openrouter",
  "deepseek",
  "gemini",
  "ollama",
  "custom",
];
