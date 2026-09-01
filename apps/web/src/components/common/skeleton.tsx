import type { HTMLAttributes } from "react";
import { cn } from "../../utils/cn";

export function Skeleton({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("rounded-md bg-muted", className)} {...props} />;
}

/** Scroll-area substitute: overflow container with slim themed scrollbars. */
export function ScrollArea({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("scroll-area overflow-auto", className)} {...props} />;
}
