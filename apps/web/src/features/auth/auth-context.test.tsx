import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { getAuthState } from "../../api/auth";
import type { AuthState } from "../../types";
import { AuthProvider, hasSessionHint, useAuth } from "./auth-context";

vi.mock("../../api/auth", () => ({ getAuthState: vi.fn() }));

function Probe() {
  const { auth, isLoading } = useAuth();
  return (
    <span data-testid="probe">{isLoading ? "loading" : (auth?.user?.email ?? "anonymous")}</span>
  );
}

function renderProvider() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <AuthProvider>
        <Probe />
      </AuthProvider>
    </QueryClientProvider>,
  );
}

const authState = {
  user: { email: "ada@eidolon.test" },
  company: { id: "c1" },
  membership: { role: "OWNER" },
} as unknown as AuthState;

describe("AuthProvider session probe", () => {
  beforeEach(() => {
    vi.mocked(getAuthState).mockReset();
    document.cookie = "eidolon_csrf=; path=/; max-age=0";
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
});
