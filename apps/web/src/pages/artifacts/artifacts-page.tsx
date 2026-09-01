import { useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useArtifacts } from "../../hooks/useArtifacts";
import { Badge } from "../../components/common/badge";
import { EmptyState, ErrorState, PageHeader } from "../../components/common/states";
import { Skeleton } from "../../components/common/skeleton";
import { cn } from "../../utils/cn";
import { enumLabel } from "../../utils/labels";
import { eid, formatRelativeTime } from "../../utils/format";
import { ARTIFACT_STATUS_VARIANT } from "../../utils/status";
import type { ArtifactType } from "../../types";

const ARTIFACT_TYPES: ArtifactType[] = [
  "prd",
  "research_report",
  "architecture",
  "source_code",
  "test_report",
  "readme",
  "release",
  "plan",
  "other",
];

export function ArtifactsPage() {
  const { t } = useTranslation();
  const [typeFilter, setTypeFilter] = useState<ArtifactType | "all">("all");
  const artifactsQuery = useArtifacts(typeFilter === "all" ? {} : { type: typeFilter });

  const artifacts = artifactsQuery.data ?? [];

  return (
    <div>
      <PageHeader title={t("artifact:listTitle")} description={t("artifact:listDescription")} />

      <div className="mb-4 flex flex-wrap gap-1.5">
        <button
          onClick={() => setTypeFilter("all")}
          className={cn(
            "rounded-full border px-2.5 py-1 text-xs transition-colors",
            typeFilter === "all"
              ? "border-foreground/40 bg-muted font-medium"
              : "border-border text-muted-foreground hover:bg-muted",
          )}
        >
          {t("artifact:filterAll")}
        </button>
        {ARTIFACT_TYPES.map((type) => (
          <button
            key={type}
            onClick={() => setTypeFilter(type)}
            className={cn(
              "rounded-full border px-2.5 py-1 text-xs transition-colors",
              typeFilter === type
                ? "border-foreground/40 bg-muted font-medium"
                : "border-border text-muted-foreground hover:bg-muted",
            )}
          >
            {enumLabel(t, "artifact:type", type)}
          </button>
        ))}
      </div>

      {artifactsQuery.isLoading ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-28 w-full" />
          ))}
        </div>
      ) : artifactsQuery.isError ? (
        <ErrorState error={artifactsQuery.error} onRetry={() => artifactsQuery.refetch()} />
      ) : artifacts.length === 0 ? (
        <EmptyState title={t("artifact:emptyTitle")} hint={t("artifact:emptyHint")} />
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {artifacts.map((artifact) => (
            <Link
              key={artifact.id}
              to={`/artifacts/${artifact.id}`}
              className="flex flex-col rounded-lg border border-border bg-card p-4 transition-colors hover:border-foreground/20"
            >
              <div className="flex items-center gap-2">
                <Badge variant="muted">{enumLabel(t, "artifact:type", artifact.type)}</Badge>
                <Badge variant={ARTIFACT_STATUS_VARIANT[artifact.status]}>
                  {enumLabel(t, "artifact:status", artifact.status)}
                </Badge>
                <span className="ml-auto font-mono text-[11px] text-muted-foreground">
                  v{artifact.version}
                </span>
              </div>
              <p className="mt-2 truncate text-sm font-semibold">{artifact.title}</p>
              <p className="mt-1 line-clamp-2 flex-1 text-xs text-muted-foreground">
                {artifact.content.slice(0, 160)}
              </p>
              <p className="mt-3 flex items-center justify-between font-mono text-[11px] text-muted-foreground">
                <span>{eid(artifact.id)}</span>
                <span>{formatRelativeTime(artifact.updated_at)}</span>
              </p>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
