import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  completeTutorialStep,
  deferTutorialQa,
  getClassicSnakeTemplate,
  getTutorial,
  getTutorialDefinition,
  pauseTutorial,
  resumeTutorial,
  skipTutorial,
  startTutorial,
  skipTutorialStep,
} from "../api/tutorial";

export function useTutorial() {
  return useQuery({ queryKey: ["tutorial"], queryFn: getTutorial, refetchInterval: 10_000 });
}

export function useTutorialDefinition() {
  return useQuery({ queryKey: ["tutorial", "definition"], queryFn: getTutorialDefinition });
}

function useTutorialMutation(action: () => ReturnType<typeof startTutorial>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: action,
    onSuccess: (data) => queryClient.setQueryData(["tutorial"], data),
  });
}

export function useStartTutorial() {
  return useTutorialMutation(startTutorial);
}

export function useSkipTutorial() {
  return useTutorialMutation(skipTutorial);
}

export function useResumeTutorial() {
  return useTutorialMutation(resumeTutorial);
}

export function usePauseTutorial() {
  return useTutorialMutation(pauseTutorial);
}

export function useSkipTutorialStep() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: skipTutorialStep,
    onSuccess: (data) => queryClient.setQueryData(["tutorial"], data),
  });
}

export function useCompleteTutorialStep() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: completeTutorialStep,
    onSuccess: (data) => queryClient.setQueryData(["tutorial"], data),
  });
}

export function useDeferTutorialQa() {
  return useTutorialMutation(deferTutorialQa);
}

export function useClassicSnakeTemplate(enabled = true) {
  return useQuery({
    queryKey: ["tutorial", "templates", "classic-snake"],
    queryFn: getClassicSnakeTemplate,
    enabled,
  });
}
