import { del, get, patch, post } from "./client";
import type {
  CompetencyDefinition,
  PositionCompetencyProfile,
  PositionProfileSummary,
  ProfileRequirement,
  ProfileTemplate,
} from "../types";

export function listPositionProfiles(): Promise<PositionProfileSummary[]> {
  return get<PositionProfileSummary[]>("/position-profiles");
}

export function listProfileTemplates(): Promise<ProfileTemplate[]> {
  return get<ProfileTemplate[]>("/position-profiles/templates");
}

export function getPositionCompetencyProfile(
  positionId: number,
): Promise<PositionCompetencyProfile> {
  return get<PositionCompetencyProfile>(`/position-definitions/${positionId}/competency-profile`);
}

export function createProfileDraft(positionId: number): Promise<PositionCompetencyProfile> {
  return post<PositionCompetencyProfile>(
    `/position-definitions/${positionId}/competency-profile/versions`,
  );
}

export function cloneProfile(
  positionId: number,
  templateVersionId: number,
): Promise<PositionCompetencyProfile> {
  return post<PositionCompetencyProfile>(
    `/position-definitions/${positionId}/competency-profile/clone`,
    { template_version_id: templateVersionId },
  );
}

export function addProfileRequirement(
  versionId: number,
  payload: Record<string, unknown>,
): Promise<ProfileRequirement> {
  return post<ProfileRequirement>(`/position-profile-versions/${versionId}/requirements`, payload);
}

export function updateProfileRequirement(
  versionId: number,
  requirementId: number,
  payload: Record<string, unknown>,
): Promise<ProfileRequirement> {
  return patch<ProfileRequirement>(
    `/position-profile-versions/${versionId}/requirements/${requirementId}`,
    payload,
  );
}

export function removeProfileRequirement(versionId: number, requirementId: number): Promise<void> {
  return del<void>(`/position-profile-versions/${versionId}/requirements/${requirementId}`);
}

export function publishProfileVersion(versionId: number, note = ""): Promise<unknown> {
  return post(`/position-profile-versions/${versionId}/publish`, { note });
}

export function retireProfileVersion(versionId: number, note = ""): Promise<unknown> {
  return post(`/position-profile-versions/${versionId}/retire`, { note });
}

export function listCompetenciesForPicker(): Promise<CompetencyDefinition[]> {
  return get<CompetencyDefinition[]>("/competencies");
}
