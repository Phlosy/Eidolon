import { useTranslation } from "react-i18next";
import { cn } from "../../utils/cn";
import { lifecycleStatusMeta } from "../../utils/status";
import { enumLabel } from "../../utils/labels";
import type { LifecycleStatus } from "../../types";

/**
 * Lifecycle status chip (v0.4). Colors borrow the employee runtime status
 * tokens via LIFECYCLE_STATUS_SOURCE (active=emerald, onboarding=violet,
 * suspended=amber, offboarded=dim, …).
 */
export function LifecycleStatusBadge({
  status,
  className,
}: {
  status: LifecycleStatus;
  className?: string;
}) {
  const { t } = useTranslation();
  const meta = lifecycleStatusMeta(status);
  return (
    <span
      data-testid="lifecycle-badge"
      data-status={status}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-medium",
        meta.chipClass,
        className,
      )}
    >
      <span className={cn("h-1.5 w-1.5 rounded-full", meta.dotClass)} />
      {enumLabel(t, "lifecycle:status", status)}
    </span>
  );
}
