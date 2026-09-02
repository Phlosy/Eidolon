import { get, post } from "./client";
import type { CreateProjectInput, TutorialProgress } from "../types";

export function getTutorial(): Promise<TutorialProgress> {
  return get<TutorialProgress>("/tutorial");
}

export function getTutorialDefinition(): Promise<{
  id: string;
  version: number;
  title: Record<string, string>;
  stages: Array<{
    id: string;
    title: Record<string, string>;
    steps: Array<{
      id: string;
      route: string;
      target?: string;
      requirement: string;
      optional: boolean;
    }>;
  }>;
}> {
  return get("/tutorial/definition");
}

export function getTutorialCenter(): Promise<
  Array<{ id: string; title: Record<"zh-CN" | "en-US", string>; route: string }>
> {
  return get("/tutorial/center");
}

export function startTutorial(): Promise<TutorialProgress> {
  return post<TutorialProgress>("/tutorial/start");
}

export function skipTutorial(): Promise<TutorialProgress> {
  return post<TutorialProgress>("/tutorial/skip");
}

export function resumeTutorial(): Promise<TutorialProgress> {
  return post<TutorialProgress>("/tutorial/resume");
}

export function pauseTutorial(): Promise<TutorialProgress> {
  return post("/tutorial/pause");
}

export function skipTutorialStep(step: string): Promise<TutorialProgress> {
  return post(`/tutorial/steps/${step}/skip`);
}

export function completeTutorialStep(step: string): Promise<TutorialProgress> {
  return post<TutorialProgress>(`/tutorial/steps/${step}/complete`);
}

export function deferTutorialQa(): Promise<TutorialProgress> {
  return post<TutorialProgress>("/tutorial/steps/hire-qa/defer");
}

export function getClassicSnakeTemplate(): Promise<{ name: string; intake: CreateProjectInput }> {
  return get<{ name: string; intake: CreateProjectInput }>("/tutorial/templates/classic-snake");
}
