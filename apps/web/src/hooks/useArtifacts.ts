import { useQuery } from "@tanstack/react-query";
import { getArtifact, listArtifacts, type ArtifactFilter } from "../api/artifacts";

export function useArtifacts(filter: ArtifactFilter = {}) {
  return useQuery({
    queryKey: ["artifacts", filter],
    queryFn: () => listArtifacts(filter),
  });
}

export function useArtifact(id: number) {
  return useQuery({
    queryKey: ["artifacts", id],
    queryFn: () => getArtifact(id),
  });
}
