import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  addProfileRequirement,
  cloneProfile,
  createProfileDraft,
  getPositionCompetencyProfile,
  listPositionProfiles,
  listProfileTemplates,
  publishProfileVersion,
  removeProfileRequirement,
  retireProfileVersion,
  updateProfileRequirement,
} from "../api/positionProfiles";

function detailKey(positionId: number) {
  return ["position-profiles", positionId];
}

export function usePositionProfiles() {
  return useQuery({ queryKey: ["position-profiles"], queryFn: listPositionProfiles });
}

export function useProfileTemplates() {
  return useQuery({ queryKey: ["position-profiles", "templates"], queryFn: listProfileTemplates });
}

export function usePositionCompetencyProfile(positionId: number) {
  return useQuery({
    queryKey: detailKey(positionId),
    queryFn: () => getPositionCompetencyProfile(positionId),
  });
}

export function useProfileMutations(positionId: number) {
  const queryClient = useQueryClient();
  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: detailKey(positionId) });
    void queryClient.invalidateQueries({ queryKey: ["position-profiles"] });
    void queryClient.invalidateQueries({ queryKey: ["position-profiles", "templates"] });
  };
  return {
    createDraft: useMutation({
      mutationFn: () => createProfileDraft(positionId),
      onSuccess: invalidate,
    }),
    clone: useMutation({
      mutationFn: (templateVersionId: number) => cloneProfile(positionId, templateVersionId),
      onSuccess: invalidate,
    }),
    addRequirement: useMutation({
      mutationFn: (payload: { versionId: number; body: Record<string, unknown> }) =>
        addProfileRequirement(payload.versionId, payload.body),
      onSuccess: invalidate,
    }),
    updateRequirement: useMutation({
      mutationFn: (payload: {
        versionId: number;
        requirementId: number;
        body: Record<string, unknown>;
      }) => updateProfileRequirement(payload.versionId, payload.requirementId, payload.body),
      onSuccess: invalidate,
    }),
    removeRequirement: useMutation({
      mutationFn: (payload: { versionId: number; requirementId: number }) =>
        removeProfileRequirement(payload.versionId, payload.requirementId),
      onSuccess: invalidate,
    }),
    publish: useMutation({
      mutationFn: (payload: { versionId: number; note: string }) =>
        publishProfileVersion(payload.versionId, payload.note),
      onSuccess: invalidate,
    }),
    retire: useMutation({
      mutationFn: (payload: { versionId: number; note: string }) =>
        retireProfileVersion(payload.versionId, payload.note),
      onSuccess: invalidate,
    }),
  };
}
