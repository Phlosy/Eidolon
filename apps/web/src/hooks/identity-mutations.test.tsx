import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import * as projectsApi from "../api/projects";
import * as tutorialApi from "../api/tutorial";
import { AUTH_QUERY_KEY, AuthProvider, useAuth } from "../features/auth/auth-context";
import type { AuthState } from "../types";
import { useCompleteProjectPhase, useDecideReview } from "./useProjects";
import { useStartTutorial } from "./useTutorial";

const identityA = { user: { id: 1 }, company: { id: 1 } } as AuthState;
const identityB = { user: { id: 2 }, company: { id: 2 } } as AuthState;

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => (resolve = done));
  return { promise, resolve };
}

describe("identity-scoped mutation responses", () => {
  it.each([false, true])(
    "only retains in-flight results for the same identity (switch=%s)",
    async (switchIdentity) => {
      const phase = deferred<Awaited<ReturnType<typeof projectsApi.completeProjectPhase>>>();
      const review = deferred<Awaited<ReturnType<typeof projectsApi.decideReview>>>();
      const tutorial = deferred<tutorialApi.TutorialProgress>();
      vi.spyOn(projectsApi, "completeProjectPhase").mockReturnValueOnce(phase.promise);
      vi.spyOn(projectsApi, "decideReview").mockReturnValueOnce(review.promise);
      vi.spyOn(tutorialApi, "startTutorial").mockReturnValueOnce(tutorial.promise);
      const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
      client.setQueryData(AUTH_QUERY_KEY, identityA);
      const { result } = renderHook(
        () => ({
          auth: useAuth(),
          phase: useCompleteProjectPhase(1),
          review: useDecideReview(7, 2),
          tutorial: useStartTutorial(),
        }),
        {
          wrapper: ({ children }: { children: ReactNode }) => (
            <QueryClientProvider client={client}>
              <AuthProvider>{children}</AuthProvider>
            </QueryClientProvider>
          ),
        },
      );
      let pending!: Promise<unknown[]>;
      await act(async () => {
        pending = Promise.all([
          result.current.phase.mutateAsync(1),
          result.current.review.mutateAsync({ decision: "approved", comments: "Approved" }),
          result.current.tutorial.mutateAsync(undefined),
        ]);
      });
      act(() => {
        if (switchIdentity) result.current.auth.setAuth(null);
        result.current.auth.setAuth(switchIdentity ? identityB : { ...identityA });
      });
      const phaseData = { project: { id: 1 } } as Awaited<typeof phase.promise>;
      const reviewData = { project: { id: 2 } } as Awaited<typeof review.promise>;
      const tutorialData = { company_id: 1, status: "active" } as tutorialApi.TutorialProgress;
      await act(async () => {
        phase.resolve(phaseData);
        review.resolve(reviewData);
        tutorial.resolve(tutorialData);
        await pending;
      });
      expect(client.getQueryData(["projects", 1, "lifecycle"])).toEqual(
        switchIdentity ? undefined : phaseData,
      );
      expect(client.getQueryData(["projects", 2, "lifecycle"])).toEqual(
        switchIdentity ? undefined : reviewData,
      );
      expect(client.getQueryData(["tutorial"])).toEqual(switchIdentity ? undefined : tutorialData);
      vi.restoreAllMocks();
    },
  );
});
