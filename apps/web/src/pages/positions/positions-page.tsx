import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { usePositionProfiles } from "../../hooks/usePositionProfiles";
import { Badge } from "../../components/common/badge";
import { EmptyState, ErrorState } from "../../components/common/states";
import { Skeleton } from "../../components/common/skeleton";
import type { PositionProfileStatus } from "../../types";

function StatusBadge({ status }: { status: PositionProfileStatus | null }) {
  const { t } = useTranslation();
  if (status === "active") {
    return (
      <Badge variant="success" data-testid="status-active">
        {t("position:positions.active")}
      </Badge>
    );
  }
  if (status === "draft") {
    return (
      <Badge variant="info" data-testid="status-draft">
        {t("position:positions.draft")}
      </Badge>
    );
  }
  return (
    <Badge variant="muted" data-testid="status-not-configured">
      {t("position:positions.notConfigured")}
    </Badge>
  );
}

export function PositionsPage() {
  const { t } = useTranslation();
  const profilesQuery = usePositionProfiles();

  if (profilesQuery.isLoading) {
    return <Skeleton className="h-40 w-full" />;
  }
  if (profilesQuery.isError) {
    return <ErrorState error={profilesQuery.error} onRetry={() => profilesQuery.refetch()} />;
  }
  const profiles = profilesQuery.data ?? [];
  if (profiles.length === 0) {
    return (
      <EmptyState
        title={t("position:positions.listTitle")}
        hint={t("position:positions.noProfileHint")}
      />
    );
  }
  return (
    <div className="space-y-4 panel-enter">
      <header>
        <h1 className="text-lg font-semibold">{t("position:positions.listTitle")}</h1>
        <p className="text-xs text-muted-foreground">{t("position:positions.listDescription")}</p>
      </header>
      <ul className="space-y-2">
        {profiles.map((profile) => (
          <li key={profile.position_definition_id}>
            <Link
              to={`/positions/${profile.position_definition_id}`}
              className="flex flex-wrap items-center gap-3 rounded-md border border-border px-4 py-3 transition-colors hover:bg-muted/50"
            >
              <div className="min-w-[10rem]">
                <span className="text-sm font-medium">{profile.name}</span>
                <span className="ml-2 font-mono text-[11px] text-muted-foreground">
                  {profile.code}
                </span>
              </div>
              <StatusBadge status={profile.profile_status} />
              <span className="ml-auto flex gap-4 font-mono text-xs text-muted-foreground">
                <span>
                  {t("position:positions.requirements")}: {profile.requirement_count}
                </span>
                <span>
                  {t("position:positions.assessmentProfile")}:{" "}
                  {profile.assessment_profile_code ?? "—"}
                </span>
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
