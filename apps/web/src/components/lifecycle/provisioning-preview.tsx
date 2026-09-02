import { useTranslation } from "react-i18next";
import { CheckCircle2, XCircle } from "lucide-react";
import { Badge } from "../common/badge";
import type { ProvisioningPreviewStep } from "../../types";

/**
 * Pure rendering of provisioning preview steps: each row shows an ✓ (available)
 * or ⚠ (unavailable) marker. Fetching lives in the caller (wizard step).
 */
export function ProvisioningPreviewList({ steps }: { steps: ProvisioningPreviewStep[] }) {
  const { t } = useTranslation();
  const unavailable = steps.filter((s) => !s.available).length;
  return (
    <div className="space-y-2">
      <ul className="space-y-1.5">
        {steps.map((step, i) => (
          <li
            key={`${step.resource_type}-${step.provider_key}-${i}`}
            data-testid="preview-step"
            data-available={step.available}
            className="flex items-center gap-2.5 rounded-md border border-border px-3 py-2 text-sm"
          >
            {step.available ? (
              <CheckCircle2
                className="h-4 w-4 shrink-0 text-status-working"
                data-testid="preview-step-available"
              />
            ) : (
              <XCircle
                className="h-4 w-4 shrink-0 text-status-reflecting"
                data-testid="preview-step-unavailable"
              />
            )}
            <span className="min-w-0 flex-1">
              <span className="font-medium">{step.description ?? step.action}</span>
              <span className="ml-2 font-mono text-[11px] text-muted-foreground">
                {step.provider_key}
              </span>
            </span>
            <Badge variant={step.available ? "success" : "warning"}>
              {step.available
                ? t("lifecycle:wizard.previewAvailable")
                : t("lifecycle:wizard.previewUnavailable")}
            </Badge>
          </li>
        ))}
      </ul>
      {unavailable > 0 ? (
        <p
          data-testid="preview-unavailable-warning"
          className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-600 dark:text-amber-400"
        >
          {t("lifecycle:wizard.unavailableWarning", { count: unavailable })}
        </p>
      ) : null}
    </div>
  );
}
