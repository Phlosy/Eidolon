import type { TFunction } from "i18next";

export function eventLabel(t: TFunction, type: string): string {
  const key = `event:${type}`;
  const translated = t(key);
  // Missing everywhere → i18next returns the key itself; show the raw type.
  return translated === key ? type : translated;
}

export function payloadSummary(data: Record<string, unknown>): string | null {
  const candidates = ["title", "name", "employee_name", "project_name", "status"];
  for (const key of candidates) {
    const value = data[key];
    if (typeof value === "string" && value.length > 0) return value;
  }
  return null;
}
