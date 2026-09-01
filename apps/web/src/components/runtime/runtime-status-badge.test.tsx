import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RuntimeStatusBadge } from "./runtime-status-badge";

describe("RuntimeStatusBadge", () => {
  it("renders crashed in red with a pulse", () => {
    render(<RuntimeStatusBadge status="crashed" />);
    const badge = screen.getByTestId("runtime-status-badge");
    expect(badge).toHaveTextContent("Crashed");
    expect(badge.className).toContain("text-red-600");
    expect(screen.getByTestId("runtime-status-dot")).toHaveClass("bg-red-500", "status-pulse");
  });

  it("renders running in green without a pulse", () => {
    render(<RuntimeStatusBadge status="running" />);
    const badge = screen.getByTestId("runtime-status-badge");
    expect(badge).toHaveTextContent("Running");
    expect(badge.className).toContain("text-emerald-600");
    expect(screen.getByTestId("runtime-status-dot")).not.toHaveClass("status-pulse");
  });

  it("renders unhealthy in amber", () => {
    render(<RuntimeStatusBadge status="unhealthy" />);
    expect(screen.getByTestId("runtime-status-badge").className).toContain("text-amber-600");
  });
});
