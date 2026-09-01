import { get } from "./client";
import type { Artifact, ArtifactType } from "../types";

export interface ArtifactFilter {
  project_id?: number;
  type?: ArtifactType;
}

export function listArtifacts(filter: ArtifactFilter = {}): Promise<Artifact[]> {
  const params = new URLSearchParams();
  if (filter.project_id !== undefined) params.set("project_id", String(filter.project_id));
  if (filter.type !== undefined) params.set("type", filter.type);
  const qs = params.toString();
  return get<Artifact[]>(`/artifacts${qs ? `?${qs}` : ""}`);
}

export function getArtifact(id: number): Promise<Artifact> {
  return get<Artifact>(`/artifacts/${id}`);
}
