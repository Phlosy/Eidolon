import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";
import { BuiltinGiteaCard } from "./builtin-gitea-card";
import type { GitBuiltinStatus, GitOverview } from "../../types";

function makeBuiltin(
  overrides: Partial<GitOverview["builtin"]> = {},
): GitOverview["builtin"] {
  return {
    docker_available: true,
    status: "not_installed" as GitBuiltinStatus,
    url: null,
    version: null,
    ...overrides,
  };
}

function renderCard(builtin: GitOverview["builtin"]) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <BuiltinGiteaCard builtin={builtin} />
    </QueryClientProvider>,
  );
}

describe("BuiltinGiteaCard", () => {
  it("shows the description and an Install button when not installed", () => {
    renderCard(makeBuiltin({ status: "not_installed" }));
    expect(
      screen.getByText("Self-hosted Git service for employee accounts and project repositories."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Install" })).toBeEnabled();
  });

  it("shows the version and a Stop button when running", () => {
    renderCard(
      makeBuiltin({ status: "running", version: "1.22.3", url: "http://127.0.0.1:26990" }),
    );
    expect(screen.getByText(/1\.22\.3/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Stop" })).toBeEnabled();
    expect(screen.getByRole("link", { name: /Open Gitea/ })).toHaveAttribute(
      "href",
      "http://127.0.0.1:26990",
    );
    expect(screen.queryByRole("button", { name: "Install" })).not.toBeInTheDocument();
  });

  it("shows a Start button when stopped", () => {
    renderCard(makeBuiltin({ status: "stopped" }));
    expect(screen.getByRole("button", { name: "Start" })).toBeEnabled();
    expect(screen.getByText("Stopped")).toBeInTheDocument();
  });

  it("shows a spinner and progress text while installing", () => {
    renderCard(makeBuiltin({ status: "installing" }));
    expect(screen.getByText(/Installing Gitea/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Install" })).not.toBeInTheDocument();
  });

  it("renders muted with a Docker unavailable hint and no actions when Docker is missing", () => {
    renderCard(makeBuiltin({ docker_available: false }));
    expect(screen.getByText(/Docker unavailable/)).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.getByTestId("builtin-gitea-card").className).toContain("opacity-60");
  });

  it("shows a red error state with a hint", () => {
    renderCard(makeBuiltin({ status: "error" }));
    expect(screen.getByText("Error")).toBeInTheDocument();
    expect(screen.getByText(/error state/)).toBeInTheDocument();
  });
});
