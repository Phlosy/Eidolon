import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";
import { GitConnectionCard } from "./git-connection-card";
import type { GitConnection } from "../../types";

function makeConnection(overrides: Partial<GitConnection> = {}): GitConnection {
  return {
    id: 1,
    name: "Company GitLab",
    platform_type: "gitlab",
    base_url: "https://gitlab.example.com",
    has_credential: true,
    credential_mask: "glpat-••••abcd",
    enabled: true,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function renderCard(connection: GitConnection) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <GitConnectionCard connection={connection} />
    </QueryClientProvider>,
  );
}

describe("GitConnectionCard", () => {
  it("shows the masked credential, never a raw token", () => {
    renderCard(makeConnection());
    expect(screen.getByText(/glpat-••••abcd/)).toBeInTheDocument();
  });

  it("shows the platform badge and base URL", () => {
    renderCard(makeConnection());
    expect(screen.getByTestId("platform-badge-gitlab")).toHaveTextContent("GitLab");
    expect(screen.getByText(/https:\/\/gitlab\.example\.com/)).toBeInTheDocument();
  });

  it("labels a Gitea connection with the Gitea badge", () => {
    renderCard(makeConnection({ platform_type: "gitea", name: "Internal Gitea" }));
    expect(screen.getByTestId("platform-badge-gitea")).toHaveTextContent("Gitea");
  });

  it("shows a no-credential placeholder when no token is stored", () => {
    renderCard(makeConnection({ has_credential: false, credential_mask: null }));
    expect(screen.getByText(/No credential/)).toBeInTheDocument();
  });

  it("shows the disabled badge for a disabled connection", () => {
    renderCard(makeConnection({ enabled: false }));
    expect(screen.getByText("Disabled")).toBeInTheDocument();
  });

  it("offers Test Connection and Delete actions", () => {
    renderCard(makeConnection());
    expect(screen.getByRole("button", { name: "Test Connection" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Delete" })).toBeEnabled();
  });
});
