import type { HTMLAttributes } from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "../../utils/cn";

const badgeVariants = cva(
  "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium",
  {
    variants: {
      variant: {
        default: "border-border bg-muted text-foreground",
        success: "border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
        info: "border-blue-500/30 bg-blue-500/10 text-blue-600 dark:text-blue-400",
        violet: "border-violet-500/30 bg-violet-500/10 text-violet-600 dark:text-violet-400",
        warning: "border-amber-500/30 bg-amber-500/10 text-amber-600 dark:text-amber-400",
        danger: "border-red-500/30 bg-red-500/10 text-red-600 dark:text-red-400",
        muted: "border-border bg-transparent text-muted-foreground",
      },
    },
    defaultVariants: { variant: "default" },
  },
);

export interface BadgeProps
  extends HTMLAttributes<HTMLSpanElement>, VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}
