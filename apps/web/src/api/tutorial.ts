import { get, post } from "./client";
import type { CreateProjectInput, TutorialProgress } from "../types";

export function getTutorial(): Promise<TutorialProgress> {
  return get<TutorialProgress>("/tutorial");
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

export function completeTutorialStep(step: string): Promise<TutorialProgress> {
  return post<TutorialProgress>(`/tutorial/steps/${step}/complete`);
}

export function deferTutorialQa(): Promise<TutorialProgress> {
  return post<TutorialProgress>("/tutorial/steps/hire-qa/defer");
}

export function getClassicSnakeTemplate(): Promise<{ name: string; intake: CreateProjectInput }> {
  return get<{ name: string; intake: CreateProjectInput }>("/tutorial/templates/classic-snake");
}
