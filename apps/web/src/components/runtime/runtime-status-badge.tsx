import { useTranslation } from "react-i18next";
import { cn } from "../../utils/cn";
import { enumLabel } from "../../utils/labels";
import type { RuntimeInstanceStatus } from "../../types";
import type { StatusVariant } from "../../utils/status";
import { Badge } from "../common/badge";

const STATUS_VARIANT: Record<RuntimeInstanceStatus, StatusVariant> = {
  created: "muted",
  starting: "info",
  running: "success",
  idle: "info",
  stopping: "warning",
  stopped: "muted",
  unhealthy: "warning",
  crashed: "danger",
  error: "danger",
  deleting: "muted",
};

const PULSING: ReadonlySet<RuntimeInstanceStatus> = new Set(["starting", "crashed"]);

const PULSE_CLASS: Partial<Record<RuntimeInstanceStatus, string>> = {
  crashed: "bg-red-500",
  starting: "bg-blue-500",
};

interface RuntimeStatusBadgeProps {
  status: RuntimeInstanceStatus;
  className?: string;
}

/** Runtime instance status. crashed pulses red; unhealthy is amber. */
export function RuntimeStatusBadge({ status, className }: RuntimeStatusBadgeProps) {
  const { t } = useTranslation();
  return (
    <Badge
      data-testid="runtime-status-badge"
      data-status={status}
      variant={STATUS_VARIANT[status]}
      className={cn("normal-case", className)}
    >
      <span
        data-testid="runtime-status-dot"
        className={cn(
          "inline-block h-1.5 w-1.5 rounded-full",
          PULSE_CLASS[status] ?? "bg-current",
          PULSING.has(status) && "status-pulse",
        )}
      />
      {enumLabel(t, "runtime:instanceStatus", status)}
    </Badge>
  );
}
