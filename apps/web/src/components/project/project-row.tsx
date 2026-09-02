import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useProject } from "../../hooks/useProjects";
import { Badge } from "../common/badge";
import { Skeleton } from "../common/skeleton";
import { enumLabel } from "../../utils/labels";
import { eid, formatRelativeTime } from "../../utils/format";
import { PROJECT_STATUS_VARIANT } from "../../utils/status";
import type { Project } from "../../types";

/**
 * Project list row with status badge and done/total task progress.
 * The list endpoint returns bare projects, so each row fetches its detail
 * (cached by TanStack Query) to compute progress.
 */
export function ProjectRow({ project }: { project: Project }) {
  const { t } = useTranslation();
  const detailQuery = useProject(project.id);
  const tasks = detailQuery.data?.tasks;
  const done = tasks?.filter((t) => t.status === "done").length ?? 0;
  const total = tasks?.length ?? 0;
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;

  return (
    <Link
      to={`/projects/${project.id}`}
      className="flex items-center gap-4 rounded-lg border border-border bg-card p-4 shadow-card transition-all duration-200 hover:-translate-y-0.5 hover:border-accent/40 hover:shadow-lift"
    >
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <p className="truncate text-sm font-semibold">{project.name}</p>
          <span className="font-mono text-[11px] text-muted-foreground">{eid(project.id)}</span>
        </div>
        <p className="mt-0.5 truncate text-xs text-muted-foreground">
          {project.description ?? project.goal ?? t("project:noDescription")}
        </p>
      </div>

      <div className="hidden w-40 shrink-0 sm:block">
        {detailQuery.isLoading ? (
          <Skeleton className="h-1.5 w-full" />
        ) : (
          <>
            <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
              <div className="h-full rounded-full bg-status-working" style={{ width: `${pct}%` }} />
            </div>
            <p className="mt-1 text-right text-[11px] text-muted-foreground">
              {t("project:taskProgress", { done, total })}
            </p>
          </>
        )}
      </div>

      <div className="flex w-32 shrink-0 flex-col items-end gap-1">
        <Badge variant={PROJECT_STATUS_VARIANT[project.status]}>
          {enumLabel(t, "project:status", project.status)}
        </Badge>
        <span className="text-[11px] text-muted-foreground">
          {formatRelativeTime(project.updated_at)}
        </span>
      </div>
    </Link>
  );
}
