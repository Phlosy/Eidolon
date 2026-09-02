import { useTranslation } from "react-i18next";
import type { Employee } from "../../types";
import { EMPLOYEE_STATUS_META } from "../../utils/status";
import { enumLabel } from "../../utils/labels";
import { initials } from "../../utils/format";
import { cn } from "../../utils/cn";

export function OfficeEmployee({ employee, selected, onSelect }: { employee: Employee; selected: boolean; onSelect: () => void }) {
  const { t } = useTranslation();
  const meta = EMPLOYEE_STATUS_META[employee.status];
  return <button type="button" onClick={onSelect} aria-pressed={selected} className={cn("group relative flex min-h-[78px] min-w-0 items-center gap-3 rounded-2xl border bg-background/70 p-3 text-left backdrop-blur transition duration-200 hover:-translate-y-0.5 hover:bg-surface-elevated", selected ? "border-border-active shadow-[var(--glow-primary)]" : "border-border", meta.hoverClass)}>
    <span className={cn("relative flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border bg-surface text-xs font-semibold", meta.auraClass, meta.pulse && "pulse-ring")}>{initials(employee.name)}<span className={cn("absolute -bottom-0.5 -right-0.5 h-2.5 w-2.5 rounded-full border-2 border-surface", meta.dotClass, meta.pulse && "status-pulse")} /></span>
    <span className="min-w-0 flex-1"><span className="block truncate text-xs font-semibold">{employee.name}</span><span className="mt-0.5 block truncate text-[10px] text-muted-foreground">{employee.title ?? enumLabel(t, "employee:role", employee.role)}</span><span className={cn("type-kicker mt-1.5 block truncate text-[8px]", meta.textClass)}>{enumLabel(t, "employee:status", employee.status)}</span></span>
  </button>;
}
