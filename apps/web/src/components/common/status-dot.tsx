import { cn } from "../../utils/cn";
import { EMPLOYEE_STATUS_META } from "../../utils/status";
import type { EmployeeStatus } from "../../types";

interface StatusDotProps {
  status: EmployeeStatus;
  className?: string;
}

/** Colored status dot; pulses only for active statuses (see utils/status). */
export function StatusDot({ status, className }: StatusDotProps) {
  const meta = EMPLOYEE_STATUS_META[status];
  return (
    <span
      data-testid="status-dot"
      data-status={status}
      className={cn(
        "inline-block h-2 w-2 rounded-full",
        meta.dotClass,
        meta.pulse && "status-pulse",
        className,
      )}
    />
  );
}
