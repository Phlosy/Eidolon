import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createDriveFolder,
  getDriveNode,
  getDriveNodeContent,
  getDriveRevisions,
  listDriveTree,
  updateDriveNode,
  uploadDriveFile,
} from "../api/drive";
import type {
  CreateDriveFolderInput,
  DriveZone,
  UploadDriveFileInput,
  UpdateDriveNodeInput,
} from "../types";

/** Flat node list (all zones when `zone` is omitted); build the tree client-side. */
export function useDriveTree(zone?: DriveZone) {
  return useQuery({
    queryKey: ["drive", "tree", { zone: zone ?? null }],
    queryFn: () => listDriveTree(zone),
  });
}

export function useDriveNode(id: number | null) {
  return useQuery({
    queryKey: ["drive", "node", id],
    queryFn: () => getDriveNode(id!),
    enabled: id != null,
  });
}

export function useDriveRevisions(id: number | null) {
  return useQuery({
    queryKey: ["drive", "node", id, "revisions"],
    queryFn: () => getDriveRevisions(id!),
    enabled: id != null,
  });
}

export function useDriveNodeContent(id: number | null, enabled = true) {
  return useQuery({
    queryKey: ["drive", "node", id, "content"],
    queryFn: () => getDriveNodeContent(id!),
    enabled: id != null && enabled,
  });
}

/** PATCH content edit — invalidates everything under ["drive"] (tree, node, revisions). */
export function useUpdateDriveNode() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: UpdateDriveNodeInput }) =>
      updateDriveNode(id, body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["drive"] }),
  });
}

export function useCreateDriveFolder() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateDriveFolderInput) => createDriveFolder(body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["drive", "tree"] }),
  });
}

export function useUploadDriveFile() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: UploadDriveFileInput) => uploadDriveFile(input),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["drive"] }),
  });
}
