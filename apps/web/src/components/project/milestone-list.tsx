import { useTranslation } from "react-i18next";
import { CheckCircle2, Circle, LoaderCircle } from "lucide-react";
import { cn } from "../../utils/cn";
import type { Milestone } from "../../types";

export function MilestoneList({ milestones }: { milestones: Milestone[] }) {
  const { t } = useTranslation();
  const sorted = [...milestones].sort((a, b) => a.order - b.order);
  if (sorted.length === 0) {
    return <p className="text-sm text-muted-foreground">{t("project:milestonesEmpty")}</p>;
  }
  return (
    <ul className="space-y-2">
      {sorted.map((milestone) => (
        <li key={milestone.id} className="flex items-start gap-2.5">
          {milestone.status === "completed" ? (
            <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-500" />
          ) : milestone.status === "in_progress" ? (
            <LoaderCircle className="status-pulse mt-0.5 h-4 w-4 shrink-0 text-amber-500" />
          ) : (
            <Circle className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
          )}
          <div className="min-w-0">
            <p
              className={cn(
                "text-sm font-medium",
                milestone.status === "pending" && "text-muted-foreground",
              )}
            >
              {milestone.name}
            </p>
            {milestone.description ? (
              <p className="truncate text-xs text-muted-foreground">{milestone.description}</p>
            ) : null}
          </div>
        </li>
      ))}
    </ul>
  );
}
