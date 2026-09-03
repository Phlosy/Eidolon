import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createGitConnection,
  deleteGitConnection,
  getGitOverview,
  installBuiltinGit,
  startBuiltinGit,
  stopBuiltinGit,
  testGitConnection,
  updateGitConnection,
} from "../api/git";
import type { CreateGitConnectionInput, UpdateGitConnectionInput } from "../types";

/**
 * Builtin Gitea state + external connections. Polls every ~3s while the
 * builtin install is in progress, then settles back to no polling.
 */
export function useGitOverview() {
  return useQuery({
    queryKey: ["git"],
    queryFn: getGitOverview,
    refetchInterval: (query) => (query.state.data?.builtin.status === "installing" ? 3_000 : false),
  });
}

export function useCreateGitConnection() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateGitConnectionInput) => createGitConnection(body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["git"] }),
  });
}

export function useUpdateGitConnection() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: UpdateGitConnectionInput }) =>
      updateGitConnection(id, body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["git"] }),
  });
}

export function useDeleteGitConnection() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => deleteGitConnection(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["git"] }),
  });
}

/** POST /git/connections/{id}/test — result is transient, nothing to invalidate. */
export function useTestGitConnection() {
  return useMutation({ mutationFn: (id: number) => testGitConnection(id) });
}

export function useInstallBuiltinGit() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => installBuiltinGit(),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["git"] }),
  });
}

export function useStartBuiltinGit() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => startBuiltinGit(),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["git"] }),
  });
}

export function useStopBuiltinGit() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => stopBuiltinGit(),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["git"] }),
  });
}
