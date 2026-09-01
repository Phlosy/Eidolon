import { Link, useParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useArtifact } from "../../hooks/useArtifacts";
import { Badge } from "../../components/common/badge";
import { ErrorState } from "../../components/common/states";
import { Skeleton } from "../../components/common/skeleton";
import { enumLabel } from "../../utils/labels";
import { eid, formatDateTime } from "../../utils/format";
import { ARTIFACT_STATUS_VARIANT } from "../../utils/status";

export function ArtifactDetailPage() {
  const { t } = useTranslation();
  const { id } = useParams<{ id: string }>();
  const artifactQuery = useArtifact(Number(id));

  if (artifactQuery.isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-96 w-full" />
      </div>
    );
  }
  if (artifactQuery.isError || !artifactQuery.data) {
    return <ErrorState error={artifactQuery.error} onRetry={() => artifactQuery.refetch()} />;
  }
  const artifact = artifactQuery.data;

  return (
    <div>
      <Link
        to="/artifacts"
        className="mb-4 inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="h-3.5 w-3.5" />
        {t("artifact:backToList")}
      </Link>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <h1 className="text-lg font-semibold tracking-tight">{artifact.title}</h1>
        <span className="font-mono text-xs text-muted-foreground">{eid(artifact.id)}</span>
        <Badge variant="muted">{enumLabel(t, "artifact:type", artifact.type)}</Badge>
        <Badge variant={ARTIFACT_STATUS_VARIANT[artifact.status]}>
          {enumLabel(t, "artifact:status", artifact.status)}
        </Badge>
        <span className="font-mono text-[11px] text-muted-foreground">v{artifact.version}</span>
      </div>
      <p className="mb-4 text-[11px] text-muted-foreground">
        {artifact.project_id != null ? (
          <>
            {t("artifact:projectLabel")}{" "}
            <Link to={`/projects/${artifact.project_id}`} className="font-mono hover:underline">
              {eid(artifact.project_id)}
            </Link>{" "}
            ·{" "}
          </>
        ) : null}
        {t("artifact:updatedAt", { time: formatDateTime(artifact.updated_at) })}
      </p>
      {/* MVP: Markdown-ish content rendered verbatim (§ spec allows <pre>). */}
      <pre className="scroll-area max-h-[70vh] overflow-auto whitespace-pre-wrap rounded-lg border border-border bg-card p-5 font-mono text-xs leading-relaxed">
        {artifact.content}
      </pre>
    </div>
  );
}
