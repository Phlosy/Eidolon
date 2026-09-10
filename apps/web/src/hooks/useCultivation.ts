import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  advanceCultivationProgram,
  completeCultivation,
  createCultivationCharacter,
  createFreeSession,
  getCultivationCharacter,
  listCultivationCharacters,
  type CreateCharacterInput,
  type FreeSessionInput,
} from "../api/cultivation";

export function useCultivationCharacters(lifecycle?: string) {
  return useQuery({
    queryKey: ["cultivation", "characters", { lifecycle: lifecycle ?? null }],
    queryFn: () => listCultivationCharacters(lifecycle),
  });
}

export function useCultivationCharacter(id: number | null) {
  return useQuery({
    queryKey: ["cultivation", "character", id],
    queryFn: () => getCultivationCharacter(id!),
    enabled: id != null,
  });
}

export function useCreateCultivationCharacter() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateCharacterInput) => createCultivationCharacter(body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["cultivation"] }),
  });
}

export function useAdvanceCultivationProgram() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (programId: number) => advanceCultivationProgram(programId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["cultivation"] }),
  });
}

export function useFreeSession(characterId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: FreeSessionInput) => createFreeSession(characterId, body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["cultivation"] }),
  });
}

export function useCompleteCultivation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (profileId: number) => completeCultivation(profileId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["cultivation"] }),
  });
}
