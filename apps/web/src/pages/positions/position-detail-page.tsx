import { useState } from "react";
import { useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { usePositionCompetencyProfile } from "../../hooks/usePositionProfiles";
import { useEmployees } from "../../hooks/useEmployees";
import { useEmployeePositionFit } from "../../hooks/usePositionFit";
import { FitAnalysis } from "../../components/position-fit/fit-analysis";
import { listCompetenciesForPicker } from "../../api/positionProfiles";
import { ProfileEditor } from "../../components/position-profile/profile-editor";
import { RequirementRow } from "../../components/position-profile/requirement-row";
import { Badge } from "../../components/common/badge";
import { ErrorState } from "../../components/common/states";
import { Skeleton } from "../../components/common/skeleton";
import { Panel } from "../../components/shared/panel";

export function PositionDetailPage() {
  const { t } = useTranslation();
  const { id } = useParams<{ id: string }>();
  const positionId = Number(id);
  const [candidateId, setCandidateId] = useState("");
  const employeesQuery = useEmployees();
  const fitQuery = useEmployeePositionFit(
    Number(candidateId) || 0,
    candidateId ? positionId : null,
  );
  const profileQuery = usePositionCompetencyProfile(positionId);
  const definitionsQuery = useQuery({
    queryKey: ["competencies", "all"],
    queryFn: listCompetenciesForPicker,
  });

  if (profileQuery.isLoading || definitionsQuery.isLoading) {
    return <Skeleton className="h-64 w-full" />;
  }
  if (profileQuery.isError || !profileQuery.data) {
    return <ErrorState error={profileQuery.error} onRetry={() => profileQuery.refetch()} />;
  }
  const profile = profileQuery.data;
  return (
    <div className="space-y-5 panel-enter">
      <header className="flex flex-wrap items-center gap-3">
        <h1 className="text-lg font-semibold">{profile.position_code}</h1>
        {profile.profile_status === "active" ? (
          <Badge variant="success">{t("position:positions.active")}</Badge>
        ) : profile.profile_status === "draft" ? (
          <Badge variant="info">{t("position:positions.draft")}</Badge>
        ) : (
          <Badge variant="muted">{t("position:positions.notConfigured")}</Badge>
        )}
        <span className="ml-auto font-mono text-xs text-muted-foreground">
          {t("position:positions.assessmentProfile")}: {profile.assessment_profile?.name ?? "—"} v
          {profile.assessment_profile?.version ?? "—"}
        </span>
      </header>

      {!profile.configured ? (
        <Panel className="p-4">
          <p className="text-sm text-muted-foreground">{t("position:positions.noProfileHint")}</p>
        </Panel>
      ) : null}

      <div className="grid gap-5 lg:grid-cols-2">
        <Panel className="p-4">
          <h2 className="mb-3 text-sm font-medium">
            {t("position:positions.general")} · {profile.general.length}
          </h2>
          <ul className="space-y-2">
            {profile.general.map((requirement) => (
              <RequirementRow key={requirement.id} item={requirement} />
            ))}
          </ul>
        </Panel>
        <Panel className="p-4">
          <h2 className="mb-3 text-sm font-medium">
            {t("position:positions.professional")} · {profile.professional.length}
          </h2>
          <ul className="space-y-2">
            {profile.professional.map((requirement) => (
              <RequirementRow key={requirement.id} item={requirement} />
            ))}
          </ul>
        </Panel>
      </div>

      {profile.integrity.codes.length > 0 ? (
        <Panel className="p-4">
          <h2 className="mb-2 text-sm font-medium">{t("position:positions.integrity")}</h2>
          <div className="flex flex-wrap gap-1.5">
            {profile.integrity.codes.map((code) => (
              <Badge key={code} variant="muted" data-testid="integrity-code">
                {code}
              </Badge>
            ))}
          </div>
          <p className="mt-2 text-xs text-muted-foreground">
            {t("position:positions.coverage")}: {profile.integrity.coverage.covered_count}/
            {profile.integrity.coverage.required_count} · {t("position:positions.uncovered")}:{" "}
            {profile.integrity.coverage.uncovered_competency_ids.length}
          </p>
        </Panel>
      ) : null}

      <Panel className="p-4">
        <h2 className="mb-3 text-sm font-medium">
          {t("position:positions.papertab")} · {t("position:positions.versions")}
        </h2>
        <ProfileEditor
          profile={profile}
          positionId={positionId}
          definitions={definitionsQuery.data ?? []}
        />
      </Panel>

      <Panel className="p-4">
        <h2 className="mb-3 text-sm font-medium">{t("position:positions.evaluateTalent")}</h2>
        <select
          value={candidateId}
          onChange={(event) => setCandidateId(event.target.value)}
          className="rounded-md border border-border bg-background px-2 py-1.5 text-xs"
        >
          <option value="">{t("position:positions.selectEmployee")}…</option>
          {(employeesQuery.data ?? []).map((employee) => (
            <option key={employee.id} value={employee.id}>
              {employee.name} ({employee.slug})
            </option>
          ))}
        </select>
        {fitQuery.data ? (
          <div className="mt-4">
            <FitAnalysis result={fitQuery.data} employeeId={Number(candidateId)} />
          </div>
        ) : null}
      </Panel>
    </div>
  );
}
