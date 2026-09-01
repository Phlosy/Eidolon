import type { TFunction } from "i18next";
import { formatLabel } from "./format";

/**
 * Translate a snake_case technical enum value (status/kind/type) through an
 * i18n namespace path, e.g. `enumLabel(t, "project:status", "in_progress")`.
 *
 * Falls back to the humanized technical value when the key is missing in
 * every language. i18next signals a missing key by returning the key itself
 * — with the namespace stripped for `ns:key` lookups — so both shapes are
 * checked (never crashes, never shows a raw key path in the UI).
 */
export function enumLabel(t: TFunction, nsKey: string, value: string): string {
  const key = `${nsKey}.${value}`;
  const bareKey = key.slice(key.indexOf(":") + 1);
  const translated = t(key);
  return translated === key || translated === bareKey ? formatLabel(value) : translated;
}
