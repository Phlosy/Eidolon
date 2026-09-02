import { useTranslation } from "react-i18next";
import { Building2, Code2, Compass, Crown, FlaskConical, ShieldCheck, type LucideIcon } from "lucide-react";
import type { Department, Employee } from "../../types";
import { OfficeEmployee } from "./office-employee";
import { cn } from "../../utils/cn";

const zoneMeta: Record<string, { icon: LucideIcon; tone: string; accent: string }> = {
  executive: { icon: Crown, tone: "border-status-learning/25 bg-status-learning/[0.035]", accent: "text-status-learning" },
  product: { icon: Compass, tone: "border-status-reflecting/25 bg-status-reflecting/[0.035]", accent: "text-status-reflecting" },
  research: { icon: FlaskConical, tone: "border-status-researching/25 bg-status-researching/[0.035]", accent: "text-status-researching" },
  engineering: { icon: Code2, tone: "border-status-working/25 bg-status-working/[0.035]", accent: "text-status-working" },
  qa: { icon: ShieldCheck, tone: "border-status-meeting/25 bg-status-meeting/[0.035]", accent: "text-status-meeting" },
};

export function DepartmentZone({ department, employees, selectedId, onSelect, featured = false }: { department: Department; employees: Employee[]; selectedId: number | null; onSelect: (employee: Employee) => void; featured?: boolean }) {
  const { t } = useTranslation();
  const meta = zoneMeta[department.slug] ?? { icon: Building2, tone: "border-border bg-surface/65", accent: "text-primary" };
  const Icon = meta.icon;
  const active = employees.filter((employee) => employee.status !== "offline").length;
  return <section className={cn("relative min-h-56 overflow-hidden rounded-[var(--radius-panel)] border p-4 shadow-[var(--shadow-panel)]", meta.tone, featured && "lg:col-span-2")}>
    <div className="pointer-events-none absolute inset-0 bg-[linear-gradient(90deg,var(--grid-line)_1px,transparent_1px),linear-gradient(var(--grid-line)_1px,transparent_1px)] bg-[size:28px_28px]" />
    <div className="relative mb-4 flex items-start justify-between gap-4"><div className="flex items-center gap-3"><span className={cn("flex h-9 w-9 items-center justify-center rounded-xl border border-current/20 bg-background/50", meta.accent)}><Icon className="h-4 w-4" /></span><div><p className="type-kicker text-muted-foreground">{t("office:departmentZone")}</p><h2 className="mt-0.5 text-sm font-semibold uppercase tracking-[0.08em]">{department.name}</h2></div></div><div className="text-right"><p className="type-telemetry text-sm font-semibold">{active}/{employees.length}</p><p className="text-[9px] text-muted-foreground">{t("office:activeNow")}</p></div></div>
    <div className="relative grid gap-2 sm:grid-cols-2 2xl:grid-cols-3">{employees.map((employee) => <OfficeEmployee key={employee.id} employee={employee} selected={selectedId === employee.id} onSelect={() => onSelect(employee)} />)}</div>
  </section>;
}
