import type { LucideIcon } from "lucide-react";
import { Card, CardContent } from "../common/card";
import { Skeleton } from "../common/skeleton";
import { cn } from "../../utils/cn";
import { useCountUp } from "../../hooks/useCountUp";

export type StatTone = "emerald" | "blue" | "violet" | "amber" | "neutral";

const TONE_TILE: Record<StatTone, string> = {
  emerald: "border-status-working/25 bg-status-working/10 text-status-working",
  blue: "border-status-researching/25 bg-status-researching/10 text-status-researching",
  violet: "border-status-learning/25 bg-status-learning/10 text-status-learning",
  amber: "border-status-reflecting/25 bg-status-reflecting/10 text-status-reflecting",
  neutral: "border-border bg-muted text-muted-foreground",
};

interface StatCardProps {
  label: string;
  value: string | number;
  hint?: string;
  icon: LucideIcon;
  tone?: StatTone;
  loading?: boolean;
}

export function StatCard({ label, value, hint, icon: Icon, tone = "neutral", loading }: StatCardProps) {
  const isNumeric = typeof value === "number";
  const counted = useCountUp(isNumeric ? value : 0);

  return (
    <Card className="transition-all duration-200 hover:-translate-y-0.5 hover:shadow-lift">
      <CardContent className="flex items-center gap-4 p-4">
        <div
          className={cn(
            "flex h-10 w-10 shrink-0 items-center justify-center rounded-md border",
            TONE_TILE[tone],
          )}
        >
          <Icon className="h-4 w-4" />
        </div>
        <div className="min-w-0">
          <p className="truncate text-xs text-muted-foreground">{label}</p>
          {loading ? (
            <Skeleton className="mt-1 h-6 w-16" />
          ) : (
            <p className="text-xl font-semibold tracking-tight tabular-nums">
              {isNumeric ? counted : value}
            </p>
          )}
          {hint && !loading ? (
            <p className="truncate text-[11px] text-muted-foreground">{hint}</p>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}
