import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AUTH_QUERY_KEY, AuthProvider, useAuth } from "../features/auth/auth-context";
import { useEventStreamStore } from "../stores/events";
import type { AuthState, StreamEvent } from "../types";
import { useEventStream } from "./useEventStream";
import { useDashboardStats } from "./useDashboardStats";

class TestWebSocket {
  static instances: TestWebSocket[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  close = vi.fn(() => this.onclose?.());

  constructor() {
    TestWebSocket.instances.push(this);
  }
}

const identityA = { user: { id: 1 }, company: { id: 1 } } as AuthState;
const identityB = { user: { id: 2 }, company: { id: 2 } } as AuthState;
const eventA: StreamEvent = {
  type: "project.created",
  data: { title: "A private event" },
  ts: "2026-09-19T00:00:00Z",
};

let auth: ReturnType<typeof useAuth>;

function Stream() {
  useEventStream();
  return null;
}

function AuthenticatedStream() {
  auth = useAuth();
  return auth.auth ? <Stream /> : null;
}

function renderStream() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(AUTH_QUERY_KEY, identityA);
  const view = render(
    <QueryClientProvider client={client}>
      <AuthProvider>
        <AuthenticatedStream />
      </AuthProvider>
    </QueryClientProvider>,
  );
  return { ...view, client };
}

describe("useEventStream identity boundaries", () => {
  beforeEach(() => {
    document.cookie = "eidolon_csrf=; path=/; max-age=0";
    TestWebSocket.instances = [];
    vi.stubGlobal("WebSocket", TestWebSocket);
    useEventStreamStore.setState({ events: [], connection: "closed" });
  });

  afterEach(() => vi.unstubAllGlobals());

  it.each([
    "employee.status_changed",
    "runtime.stopped",
    "provider.updated",
    "project.created",
    "task.completed",
    "artifact.created",
    "drive.document_created",
    "document.updated",
    "review.completed",
    "git.updated",
    "skill.updated",
    "learning.completed",
    "knowledge.updated",
  ])("invalidates the dashboard aggregate after %s", (type) => {
    const { client } = renderStream();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    act(() => {
      TestWebSocket.instances[0].onmessage?.({ data: JSON.stringify({ ...eventA, type }) });
    });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["dashboard"] });
  });

  it("refetches a mounted dashboard after a task event and retains data while refreshing", async () => {
    const summary = {
      company_id: 1,
      employees_online: 1,
      employees_total: 1,
      active_projects: 1,
      completed_projects: 0,
      tasks_in_progress: 1,
      tasks_total: 2,
      tasks_completed: 1,
      documents_count: 1,
      total_attempts: 1,
      project_summaries: [{ project_id: 1, tasks_total: 2, tasks_completed: 1, blockers: 0 }],
    };
    let finishRefresh!: (value: Response) => void;
    const pendingRefresh = new Promise<Response>((resolve) => (finishRefresh = resolve));
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(summary)))
      .mockReturnValueOnce(pendingRefresh);
    vi.stubGlobal("fetch", fetchMock);
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: Infinity } },
    });
    client.setQueryData(AUTH_QUERY_KEY, identityA);
    const { result } = renderHook(
      () => {
        useEventStream();
        return useDashboardStats();
      },
      {
        wrapper: ({ children }: { children: ReactNode }) => (
          <QueryClientProvider client={client}>
            <AuthProvider>{children}</AuthProvider>
          </QueryClientProvider>
        ),
      },
    );
    await waitFor(() => expect(result.current.tasksCompleted).toBe(1));
    act(() =>
      TestWebSocket.instances[0].onmessage?.({
        data: JSON.stringify({ ...eventA, type: "task.completed" }),
      }),
    );
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(result.current).toMatchObject({ isLoading: false, tasksCompleted: 1 });
    await act(async () =>
      finishRefresh(
        new Response(
          JSON.stringify({
            ...summary,
            tasks_in_progress: 0,
            tasks_completed: 2,
            total_attempts: 2,
            project_summaries: [{ project_id: 1, tasks_total: 2, tasks_completed: 2, blockers: 0 }],
          }),
        ),
      ),
    );
    await waitFor(() => expect(result.current.tasksCompleted).toBe(2));
    expect(result.current).toMatchObject({ tasksInProgress: 0, runtimeCostUsd: 0.1 });
    expect(fetchMock.mock.calls.every(([path]) => path === "/api/v1/dashboard/summary")).toBe(true);
  });

  it("ignores all callbacks from A immediately at logout and after B connects", async () => {
    const { client } = renderStream();
    const socketA = TestWebSocket.instances[0];
    const oldMessage = socketA.onmessage!;
    const oldOpen = socketA.onopen!;
    const oldClose = socketA.onclose!;
    act(() => {
      oldOpen();
      oldMessage({ data: JSON.stringify(eventA) });
    });
    expect(useEventStreamStore.getState().events).toEqual([eventA]);
    const invalidate = vi.spyOn(client, "invalidateQueries");

    act(() => {
      auth.setAuth(null);
      // Queued frames can run before React has cleaned up the old effect.
      oldMessage({ data: JSON.stringify(eventA) });
      oldOpen();
      oldClose();
    });
    expect(useEventStreamStore.getState()).toMatchObject({ events: [], connection: "closed" });
    expect(invalidate).not.toHaveBeenCalled();
    expect(socketA.close).toHaveBeenCalled();

    act(() => auth.setAuth(identityB));
    await waitFor(() => expect(TestWebSocket.instances).toHaveLength(2));
    const socketB = TestWebSocket.instances[1];
    const eventB = { ...eventA, data: { title: "B event" } };
    act(() => {
      socketB.onopen?.();
      socketB.onmessage?.({ data: JSON.stringify(eventB) });
      oldMessage({ data: JSON.stringify(eventA) });
      oldClose();
    });
    expect(useEventStreamStore.getState()).toMatchObject({ events: [eventB], connection: "open" });
  });

  it("retains the connection and events when the logical identity is unchanged", () => {
    renderStream();
    const socket = TestWebSocket.instances[0];
    act(() => {
      socket.onopen?.();
      socket.onmessage?.({ data: JSON.stringify(eventA) });
      auth.setAuth({ ...identityA, user: { ...identityA.user, display_name: "Updated" } });
    });
    expect(TestWebSocket.instances).toHaveLength(1);
    expect(socket.close).not.toHaveBeenCalled();
    expect(useEventStreamStore.getState()).toMatchObject({ events: [eventA], connection: "open" });
  });

  it("ignores queued callbacks after unmount", () => {
    const { unmount, client } = renderStream();
    const socket = TestWebSocket.instances[0];
    const oldMessage = socket.onmessage!;
    const oldOpen = socket.onopen!;
    const invalidate = vi.spyOn(client, "invalidateQueries");
    unmount();
    act(() => {
      oldMessage({ data: JSON.stringify(eventA) });
      oldOpen();
    });
    expect(useEventStreamStore.getState()).toMatchObject({ events: [], connection: "closed" });
    expect(invalidate).not.toHaveBeenCalled();
  });
});
