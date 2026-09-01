import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createProject, getProject, getProjectGraph, listProjects } from "../api/projects";
import type { CreateProjectInput } from "../types";

export function useProjects() {
  return useQuery({ queryKey: ["projects"], queryFn: listProjects });
}

export function useProject(id: number) {
  return useQuery({
    queryKey: ["projects", id],
    queryFn: () => getProject(id),
  });
}

export function useProjectGraph(id: number) {
  return useQuery({
    queryKey: ["projects", id, "graph"],
    queryFn: () => getProjectGraph(id),
  });
}

export function useCreateProject() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateProjectInput) => createProject(input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
    },
  });
}
