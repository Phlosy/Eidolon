import type { HTMLAttributes, ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { cn } from "../../utils/cn";

export function Panel({ className, ...props }: HTMLAttributes<HTMLElement>) {
  return <section className={cn("command-panel relative overflow-hidden", className)} {...props} />;
}

export function SectionHeader({ kicker, title, description, icon: Icon, action }: { kicker?: string; title: string; description?: string; icon?: LucideIcon; action?: ReactNode }) {
  return <div className="mb-4 flex items-start justify-between gap-4"><div className="flex min-w-0 items-start gap-3">{Icon ? <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-primary/20 bg-primary/8 text-primary"><Icon className="h-4 w-4" /></span> : null}<div className="min-w-0">{kicker ? <p className="type-kicker text-muted-foreground">{kicker}</p> : null}<h2 className="mt-0.5 text-base font-semibold tracking-tight">{title}</h2>{description ? <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{description}</p> : null}</div></div>{action}</div>;
}
