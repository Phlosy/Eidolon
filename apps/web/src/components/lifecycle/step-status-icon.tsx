import { useTranslation } from "react-i18next";
import { CheckCircle2, Circle, Loader2, MinusCircle, XCircle, type LucideIcon } from "lucide-react";
import { cn } from "../../utils/cn";
import { enumLabel } from "../../utils/labels";

const STEP_STATUS_ICON: Record<string, LucideIcon> = {
  pending: Circle,
  running: Loader2,
  done: CheckCircle2,
  failed: XCircle,
  skipped: MinusCircle,
};

/** Provisioning step status icon (spinner while running, token colors otherwise). */
export function StepStatusIcon({ status }: { status: string }) {
  const { t } = useTranslation();
  const Icon = STEP_STATUS_ICON[status] ?? Circle;
  return (
    <Icon
      data-testid={`step-icon-${status}`}
      aria-label={enumLabel(t, "lifecycle:job.stepStatus", status)}
      className={cn(
        "h-4 w-4 shrink-0",
        status === "done" && "text-status-working",
        status === "running" && "animate-spin text-status-researching",
        status === "failed" && "text-status-error",
        (status === "pending" || status === "skipped") && "text-muted-foreground",
      )}
    />
  );
}
