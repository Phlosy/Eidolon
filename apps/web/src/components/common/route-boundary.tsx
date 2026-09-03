import { Suspense, type ReactNode } from "react";
import { Skeleton } from "./skeleton";

export function RouteBoundary({ children }: { children: ReactNode }) {
  return (
    <Suspense
      fallback={
        <div className="space-y-4" aria-busy="true">
          <Skeleton className="h-40 w-full rounded-[var(--radius-panel)]" />
          <div className="grid gap-4 md:grid-cols-2">
            <Skeleton className="h-56 w-full rounded-[var(--radius-panel)]" />
            <Skeleton className="h-56 w-full rounded-[var(--radius-panel)]" />
          </div>
        </div>
      }
    >
      {children}
    </Suspense>
  );
}
