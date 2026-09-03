import { useTranslation } from "react-i18next";
import { Check } from "lucide-react";
import { cn } from "../../utils/cn";
import { enumLabel } from "../../utils/labels";
import type { RuntimeCapabilities } from "../../types";

const CAPABILITY_KEYS = [
  "chat",
  "task",
  "filesystem",
  "terminal",
  "web",
  "memory",
  "skills",
  "scheduler",
  "streaming",
  "artifacts",
] as const satisfies readonly (keyof RuntimeCapabilities)[];

/**
 * Capability matrix for a runtime type. Unsupported capabilities are shown
 * muted and labelled "Unsupported" — capabilities are never assumed.
 */
export function RuntimeCapabilitiesGrid({ capabilities }: { capabilities: RuntimeCapabilities }) {
  const { t } = useTranslation();
  return (
    <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 sm:grid-cols-5">
      {CAPABILITY_KEYS.map((key) => {
        const supported = capabilities[key];
        return (
          <div
            key={key}
            className={cn(
              "flex items-center gap-1.5 text-xs",
              supported ? "text-foreground" : "text-muted-foreground/60",
            )}
          >
            {supported ? (
              <Check className="h-3 w-3 text-emerald-600 dark:text-emerald-400" />
            ) : (
              <span className="inline-block h-3 w-3 text-center leading-3">—</span>
            )}
            <span>{enumLabel(t, "runtime:capability", key)}</span>
            {!supported ? <span className="text-[10px]">{t("runtime:unsupported")}</span> : null}
          </div>
        );
      })}
    </div>
  );
}
