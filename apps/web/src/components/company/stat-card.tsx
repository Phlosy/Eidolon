import type { LucideIcon } from "lucide-react";
import { Card, CardContent } from "../common/card";
import { Skeleton } from "../common/skeleton";
import { cn } from "../../utils/cn";

interface StatCardProps {
  label: string;
  value: string | number;
  hint?: string;
  icon: LucideIcon;
  loading?: boolean;
}

export function StatCard({ label, value, hint, icon: Icon, loading }: StatCardProps) {
  return (
    <Card>
      <CardContent className="flex items-center gap-4 p-4">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md border border-border bg-muted">
          <Icon className="h-4 w-4 text-muted-foreground" />
        </div>
        <div className="min-w-0">
          <p className="truncate text-xs text-muted-foreground">{label}</p>
          {loading ? (
            <Skeleton className="mt-1 h-6 w-16" />
          ) : (
            <p className={cn("text-xl font-semibold tracking-tight")}>{value}</p>
          )}
          {hint && !loading ? (
            <p className="truncate text-[11px] text-muted-foreground">{hint}</p>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}
