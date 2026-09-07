import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { PositionsPage } from "./positions-page";
import type { PositionProfileSummary } from "../../types";

vi.mock("../../hooks/usePositionProfiles", () => ({
  usePositionProfiles: () => ({
    data: summariesFixture(),
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
}));

function summariesFixture(): PositionProfileSummary[] {
  return [
    {
      position_definition_id: 1,
      code: "engineer",
      name: "Software Engineer",
      active_version: 1,
      profile_status: "active",
      requirement_count: 12,
      assessment_profile_code: "software_engineer",
    },
    {
      position_definition_id: 2,
      code: "custom_role",
      name: "Custom Role",
      active_version: null,
      profile_status: null,
      requirement_count: 0,
      assessment_profile_code: null,
    },
  ];
}

describe("PositionsPage", () => {
  it("shows active profile status for configured positions", () => {
    render(
      <MemoryRouter>
        <PositionsPage />
      </MemoryRouter>,
    );
    expect(screen.getByText("Software Engineer")).toBeInTheDocument();
    expect(screen.getByTestId("status-active")).toBeInTheDocument();
    expect(screen.getByText(/software_engineer/)).toBeInTheDocument();
  });

  it("renders Not configured (not an empty list) for positions without a profile", () => {
    render(
      <MemoryRouter>
        <PositionsPage />
      </MemoryRouter>,
    );
    expect(screen.getByText("Custom Role")).toBeInTheDocument();
    expect(screen.getByTestId("status-not-configured")).toBeInTheDocument();
    expect(screen.getByText("Not configured")).toBeInTheDocument();
  });
});
