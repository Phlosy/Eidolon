import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createProject,
  getProject,
  getProjectGraph,
  getProjectPortfolio,
  getProjectLifecycle,
  getReview,
  completeProjectPhase,
  decideReview,
  createChangeRequest,
  listProjects,
} from "../api/projects";
import type { CreateProjectInput } from "../types";
import type { ReviewDecisionInput } from "../types";

export function useProjects() {
  return useQuery({ queryKey: ["projects"], queryFn: listProjects });
}

export function useProject(id: number) {
  return useQuery({
    queryKey: ["projects", id],
    queryFn: () => getProject(id),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status && !["completed", "cancelled", "rejected"].includes(status) ? 10_000 : false;
    },
  });
}

export function useProjectPortfolio() {
  return useQuery({
    queryKey: ["projects", "portfolio"],
    queryFn: getProjectPortfolio,
    refetchInterval: 15_000,
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

export function useProjectLifecycle(id: number) {
  return useQuery({
    queryKey: ["projects", id, "lifecycle"],
    queryFn: () => getProjectLifecycle(id),
    enabled: Number.isFinite(id) && id > 0,
    refetchInterval: 15_000,
  });
}

export function useCompleteProjectPhase(projectId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (phaseId: number) => completeProjectPhase(projectId, phaseId),
    onSuccess: (data) => {
      queryClient.setQueryData(["projects", projectId, "lifecycle"], data);
      void queryClient.invalidateQueries({ queryKey: ["projects", projectId] });
      void queryClient.invalidateQueries({ queryKey: ["projects", "portfolio"] });
    },
  });
}

export function useReview(reviewId: number) {
  return useQuery({
    queryKey: ["reviews", reviewId],
    queryFn: () => getReview(reviewId),
    enabled: Number.isFinite(reviewId) && reviewId > 0,
  });
}

export function useDecideReview(reviewId: number, projectId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: ReviewDecisionInput) => decideReview(reviewId, input),
    onSuccess: (data) => {
      queryClient.setQueryData(["projects", projectId, "lifecycle"], data);
      void queryClient.invalidateQueries({ queryKey: ["reviews", reviewId] });
      void queryClient.invalidateQueries({ queryKey: ["projects", projectId] });
    },
  });
}

export function useCreateChangeRequest(projectId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: createChangeRequest.bind(null, projectId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["projects", projectId, "lifecycle"] });
    },
  });
}
