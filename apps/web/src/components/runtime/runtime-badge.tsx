import { useTranslation } from "react-i18next";
import { Badge } from "../common/badge";
import { enumLabel } from "../../utils/labels";
import type { RuntimeType } from "../../types";

const RUNTIME_VARIANT: Record<RuntimeType, "muted" | "info" | "violet"> = {
  mock: "muted",
  hermes: "info",
  openclaw: "info",
  codex: "violet",
  claude_code: "violet",
  opencode: "violet",
  custom: "muted",
};

export function RuntimeBadge({ type }: { type: RuntimeType }) {
  const { t } = useTranslation();
  return (
    <Badge variant={RUNTIME_VARIANT[type]} className="font-mono normal-case">
      {enumLabel(t, "runtime:type", type)}
    </Badge>
  );
}
