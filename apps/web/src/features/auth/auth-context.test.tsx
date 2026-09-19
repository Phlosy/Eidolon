import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { getAuthState } from "../../api/auth";
import { ApiError } from "../../api/client";
import { ActivityTicker } from "../../pages/office/activity-ticker";
import { useEventStreamStore } from "../../stores/events";
import type { AuthState } from "../../types";
import { AUTH_QUERY_KEY, AuthProvider, hasSessionHint, useAuth } from "./auth-context";

vi.mock("../../api/auth", () => ({ getAuthState: vi.fn() }));

function Probe() {
  const { auth, isLoading } = useAuth();
  return (
    <span data-testid="probe">{isLoading ? "loading" : (auth?.user?.email ?? "anonymous")}</span>
  );
}

function renderProvider(
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } }),
  children?: ReactNode,
) {
  return render(
    <QueryClientProvider client={client}>
      <AuthProvider>
        <Probe />
        {children}
      </AuthProvider>
    </QueryClientProvider>,
  );
}

const authState = {
  user: { id: 1, email: "ada@eidolon.test" },
  company: { id: 1 },
  membership: { role: "OWNER" },
} as unknown as AuthState;

const otherAuthState = {
  ...authState,
  user: { ...authState.user, id: 2, email: "bob@eidolon.test" },
  company: { ...authState.company, id: 2 },
};
const oldEvent = {
  type: "project.created",
  data: { title: "A private activity" },
  ts: "2026-09-19T00:00:00Z",
};

function IdentityControls({ next = otherAuthState }: { next?: AuthState }) {
  const { setAuth, refresh } = useAuth();
  return (
    <>
      <button onClick={() => setAuth(null)}>Log out</button>
      <button onClick={() => setAuth(next)}>Set identity</button>
      <button onClick={() => void refresh()}>Refresh identity</button>
    </>
  );
}

function ProjectsProbe() {
  const { auth } = useAuth();
  const projects = useQuery({
    queryKey: ["projects"],
    queryFn: async () => [`${auth?.user.email} projects`],
    enabled: Boolean(auth),
  });
  return <div data-testid="projects">{projects.data?.join(", ")}</div>;
}

function seedIdentity() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 30_000 } },
  });
  client.setQueryData(AUTH_QUERY_KEY, authState);
  client.setQueryData(["projects"], ["A private project"]);
  useEventStreamStore.setState({ events: [oldEvent], connection: "open" });
  return client;
}

describe("AuthProvider session probe", () => {
  beforeEach(() => {
    vi.mocked(getAuthState).mockReset();
    document.cookie = "eidolon_csrf=; path=/; max-age=0";
    useEventStreamStore.setState({ events: [], connection: "closed" });
  });

  it("does not call /auth/me when no session cookie exists", () => {
    expect(hasSessionHint()).toBe(false);
    renderProvider();
    expect(screen.getByTestId("probe")).toHaveTextContent("anonymous");
    expect(getAuthState).not.toHaveBeenCalled();
  });

  it("probes /auth/me when the csrf hint cookie is present", async () => {
    document.cookie = "eidolon_csrf=token-value; path=/";
    expect(hasSessionHint()).toBe(true);
    vi.mocked(getAuthState).mockResolvedValue(authState);

    renderProvider();
    expect(await screen.findByText("ada@eidolon.test")).toBeInTheDocument();
    expect(getAuthState).toHaveBeenCalledTimes(1);
  });

  it("clears A's cached and pending queries and live events through logout and B login", async () => {
    const client = seedIdentity();
    let resolveOldQuery!: (value: string[]) => void;
    const pendingQuery = client
      .fetchQuery({
        queryKey: ["employees"],
        queryFn: () => new Promise<string[]>((resolve) => (resolveOldQuery = resolve)),
      })
      .catch(() => undefined);
    renderProvider(
      client,
      <>
        <IdentityControls />
        <ProjectsProbe />
        <ActivityTicker />
      </>,
    );
    expect(screen.getByText("A private project")).toBeInTheDocument();
    expect(screen.getByText("A private activity")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Log out" }));
    expect(client.getQueryData(["projects"])).toBeUndefined();
    expect(useEventStreamStore.getState()).toMatchObject({ events: [], connection: "closed" });
    expect(await screen.findByText("anonymous")).toBeInTheDocument();
    expect(screen.queryByText("A private activity")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Set identity" }));
    expect(await screen.findByText("bob@eidolon.test projects")).toBeInTheDocument();
    await act(async () => {
      resolveOldQuery(["A private employee"]);
      await pendingQuery;
    });
    expect(client.getQueryData(["employees"])).toBeUndefined();
    expect(screen.queryByText(/A private/)).not.toBeInTheDocument();

    act(() =>
      useEventStreamStore.getState().pushEvent({ ...oldEvent, data: { title: "B activity" } }),
    );
    expect(screen.getByText("B activity")).toBeInTheDocument();
  });

  it.each([
    ["user", { ...authState, user: otherAuthState.user }],
    ["company", { ...authState, company: otherAuthState.company }],
  ])("clears identity-scoped data when the %s changes without logout", async (_kind, next) => {
    const client = seedIdentity();
    renderProvider(
      client,
      <>
        <IdentityControls next={next} />
        <ProjectsProbe />
        <ActivityTicker />
      </>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Set identity" }));
    expect(client.getQueryData(["projects"])).toBeUndefined();
    expect(useEventStreamStore.getState()).toMatchObject({ events: [], connection: "closed" });
    expect(screen.queryByText(/A private/)).not.toBeInTheDocument();
    await waitFor(() => expect(client.getQueryData(AUTH_QUERY_KEY)).toEqual(next));
  });

  it("preserves caches and events for profile updates and refreshes of the same user/company", async () => {
    const client = seedIdentity();
    const updated = { ...authState, user: { ...authState.user, display_name: "Ada Lovelace" } };
    vi.mocked(getAuthState).mockResolvedValue(updated);
    renderProvider(client, <IdentityControls next={updated} />);
    fireEvent.click(screen.getByRole("button", { name: "Set identity" }));
    fireEvent.click(screen.getByRole("button", { name: "Refresh identity" }));
    await waitFor(() => expect(getAuthState).toHaveBeenCalledTimes(1));
    expect(client.getQueryData(["projects"])).toEqual(["A private project"]);
    expect(useEventStreamStore.getState()).toMatchObject({
      events: [oldEvent],
      connection: "open",
    });
    expect(client.getQueryData(AUTH_QUERY_KEY)).toEqual(updated);
  });

  it("also clears private data when /auth/me changes identity or expires", async () => {
    const client = seedIdentity();
    vi.mocked(getAuthState).mockResolvedValueOnce(otherAuthState);
    renderProvider(client, <IdentityControls />);
    fireEvent.click(screen.getByRole("button", { name: "Refresh identity" }));
    expect(await screen.findByText("bob@eidolon.test")).toBeInTheDocument();
    expect(client.getQueryData(["projects"])).toBeUndefined();
    expect(useEventStreamStore.getState().events).toEqual([]);

    client.setQueryData(["projects"], ["B private project"]);
    vi.mocked(getAuthState).mockRejectedValueOnce(new ApiError(401, "Session expired"));
    fireEvent.click(screen.getByRole("button", { name: "Refresh identity" }));
    expect(await screen.findByText("anonymous")).toBeInTheDocument();
    expect(client.getQueryData(["projects"])).toBeUndefined();
  });

  it("ignores an old in-flight /auth/me response after a new login", async () => {
    const client = seedIdentity();
    let resolveOldAuth!: (value: AuthState) => void;
    vi.mocked(getAuthState).mockReturnValue(new Promise((resolve) => (resolveOldAuth = resolve)));
    renderProvider(client, <IdentityControls />);
    fireEvent.click(screen.getByRole("button", { name: "Refresh identity" }));
    fireEvent.click(screen.getByRole("button", { name: "Log out" }));
    await screen.findByText("anonymous");
    fireEvent.click(screen.getByRole("button", { name: "Set identity" }));
    await screen.findByText("bob@eidolon.test");
    client.setQueryData(["projects"], ["B private project"]);
    await act(async () => resolveOldAuth(authState));
    expect(client.getQueryData(AUTH_QUERY_KEY)).toEqual(otherAuthState);
    expect(client.getQueryData(["projects"])).toEqual(["B private project"]);
  });
});
