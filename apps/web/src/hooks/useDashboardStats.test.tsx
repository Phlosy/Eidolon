import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { DashboardSummary } from "../api/dashboard";
import { AUTH_QUERY_KEY, AuthProvider, useAuth } from "../features/auth/auth-context";
import type { AuthState } from "../types";
import { useDashboardStats } from "./useDashboardStats";

const identity = { user: { id: 1 }, company: { id: 1 } } as AuthState;
const emptySummary: DashboardSummary = {
  company_id: 1,
  employees_online: 0,
  employees_total: 0,
  active_projects: 0,
  completed_projects: 0,
  tasks_in_progress: 0,
  tasks_total: 0,
  tasks_completed: 0,
  documents_count: 0,
  total_attempts: 0,
  project_summaries: [],
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => (resolve = done));
  return { promise, resolve };
}

function renderStats(auth: AuthState | null = identity) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  client.setQueryData(AUTH_QUERY_KEY, auth);
  return {
    client,
    ...renderHook(useDashboardStats, {
      wrapper: ({ children }: { children: ReactNode }) => (
        <QueryClientProvider client={client}>
          <AuthProvider>{children}</AuthProvider>
        </QueryClientProvider>
      ),
    }),
  };
}

function mockCompany(employeesCount: number, projectsCount: number) {
  // Serve the former fan-out routes too: a regression fails the request budget,
  // rather than failing because the mock no longer knows those endpoints.
  const employees = Array.from({ length: employeesCount }, (_, i) => ({
    id: i + 1,
    status: i === 0 ? "offline" : "idle",
  }));
  const projects = Array.from({ length: projectsCount }, (_, i) => ({
    id: i + 1,
    status: "in_progress",
  }));
  const tasks = ["done", "in_progress", "in_review", "failed", "rejected", "blocked"].map(
    (status) => ({ status }),
  );
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input).replace("/api/v1", "");
    let body: unknown;
    if (path === "/employees") body = employees;
    else if (path === "/projects") body = projects;
    else if (path === "/drive/tree") body = [{ kind: "folder" }, { kind: "document" }];
    else if (/^\/projects\/\d+$/.test(path)) body = { id: Number(path.split("/")[2]), tasks };
    else if (/^\/employees\/\d+\/performance$/.test(path)) body = { attempts: 3 };
    else if (path === "/dashboard/summary")
      body = {
        company_id: 1,
        employees_online: Math.max(0, employeesCount - 1),
        employees_total: employeesCount,
        active_projects: projectsCount,
        completed_projects: 0,
        tasks_in_progress: projectsCount * 2,
        tasks_total: projectsCount * 6,
        tasks_completed: projectsCount,
        documents_count: 1,
        total_attempts: employeesCount * 3,
        project_summaries: projects
          .slice()
          .reverse()
          .slice(0, 4)
          .map(({ id }) => ({ project_id: id, tasks_total: 6, tasks_completed: 1, blockers: 2 })),
      };
    else throw new Error(`Unexpected request: ${path}`);
    return new Response(JSON.stringify(body), { status: 200 });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => vi.unstubAllGlobals());

describe("useDashboardStats request budget", () => {
  it.each([
    [2, 1],
    [10, 8],
    [50, 30],
  ])(
    "measures cold-cache requests for %i employees and %i projects",
    async (employees, projects) => {
      const fetchMock = mockCompany(employees, projects);
      const { result } = renderStats();
      await waitFor(() => expect(result.current.isLoading).toBe(false));
      expect(result.current).toMatchObject({
        isError: false,
        employeesOnline: employees - 1,
        employeesTotal: employees,
        activeProjects: projects,
        tasksInProgress: projects * 2,
        tasksTotal: projects * 6,
        tasksCompleted: projects,
        artifactsCount: 1,
        runtimeCostUsd: employees * 3 * 0.05,
      });
      expect(fetchMock).toHaveBeenCalledTimes(1);
      expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/dashboard/summary");
    },
  );
});

describe("useDashboardStats state and identity", () => {
  it("loads one complete summary and preserves the existing cost and XP formula", async () => {
    const pending = deferred<Response>();
    const fetchMock = vi.fn(() => pending.promise);
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderStats();
    expect(result.current).toMatchObject({ isLoading: true, isError: false, employeesTotal: 0 });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await act(async () =>
      pending.resolve(
        new Response(
          JSON.stringify({
            ...emptySummary,
            employees_total: 4,
            completed_projects: 1,
            documents_count: 2,
            tasks_completed: 8,
            total_attempts: 36,
            project_summaries: [{ project_id: 9, tasks_total: 9, tasks_completed: 1, blockers: 2 }],
          }),
        ),
      ),
    );
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.runtimeCostUsd).toBe(36 * 0.05);
    expect(result.current.companyProgress).toEqual({
      xp: 288,
      level: 3,
      currentLevelXp: 88,
      nextLevelXp: 200,
      percent: 44,
    });
    expect(result.current.projectSummaries).toEqual([
      { project_id: 9, tasks_total: 9, tasks_completed: 1, blockers: 2 },
    ]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("surfaces aggregate errors without waiting on detail or performance requests", async () => {
    const fetchMock = vi.fn(
      async () => new Response(JSON.stringify({ detail: "unavailable" }), { status: 503 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderStats();
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.isLoading).toBe(false);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("supports the anonymous default-company query and empty metrics", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify(emptySummary))),
    );
    const { result, client } = renderStats(null);
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current).toMatchObject({
      employeesTotal: 0,
      employeesOnline: 0,
      activeProjects: 0,
      tasksTotal: 0,
      tasksCompleted: 0,
      tasksInProgress: 0,
      runtimeCostUsd: 0,
      artifactsCount: 0,
      projectSummaries: [],
      companyProgress: { level: 2, xp: 0, percent: 0 },
    });
    expect(client.getQueryData(["dashboard", "summary", null, null])).toEqual(emptySummary);
  });

  it.each([
    { user: { id: 2 }, company: { id: 2 } },
    { user: { id: 3 }, company: { id: 1 } },
  ])("discards an old in-flight response when identity becomes %j", async (nextIdentity) => {
    const first = deferred<Response>();
    const second = deferred<Response>();
    const fetchMock = vi
      .fn()
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);
    vi.stubGlobal("fetch", fetchMock);
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: Infinity } },
    });
    client.setQueryData(AUTH_QUERY_KEY, identity);
    const { result } = renderHook(() => ({ stats: useDashboardStats(), auth: useAuth() }), {
      wrapper: ({ children }: { children: ReactNode }) => (
        <QueryClientProvider client={client}>
          <AuthProvider>{children}</AuthProvider>
        </QueryClientProvider>
      ),
    });
    act(() => result.current.auth.setAuth(nextIdentity as AuthState));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(result.current.stats).toMatchObject({ isLoading: true, employeesTotal: 0 });
    await act(async () =>
      second.resolve(
        new Response(
          JSON.stringify({
            ...emptySummary,
            company_id: nextIdentity.company.id,
            employees_total: 2,
          }),
        ),
      ),
    );
    await waitFor(() => expect(result.current.stats.employeesTotal).toBe(2));
    await act(async () =>
      first.resolve(new Response(JSON.stringify({ ...emptySummary, employees_total: 99 }))),
    );
    expect(result.current.stats.employeesTotal).toBe(2);
    expect(client.getQueryData(["dashboard", "summary", 1, 1])).toBeUndefined();
    expect(
      client.getQueryData(["dashboard", "summary", nextIdentity.user.id, nextIdentity.company.id]),
    ).toMatchObject({ employees_total: 2 });
  });
});
