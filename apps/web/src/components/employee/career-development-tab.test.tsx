import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { CareerDevelopmentTab } from "./career-development-tab";
import type { CareerOverview } from "../../types";

vi.mock("../../hooks/useCareer", () => ({
  useCareerOverview: () => ({
    data: overviewFixture(),
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
  useCareerReadiness: () => ({
    data: {
      readiness_status: "CRITICAL_GAPS",
      required_gaps: ["test_design"],
      position_fit: {
        known_fit_score: 0.6,
        fit_confidence: 0.7,
        required_coverage: 1,
        qualification_status: "NOT_QUALIFIED",
        fit_status: "CRITICAL_GAP",
      },
    },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  }),
  useCareerMutations: () => ({
    createPlan: { mutate: vi.fn() },
    activatePlan: { mutate: vi.fn() },
    addLearningPriority: { mutate: vi.fn() },
    promote: { mutate: vi.fn() },
  }),
}));

vi.mock("../../hooks/useOrganizations", () => ({
  useVacantSlots: () => ({
    data: [{ id: 12, position_definition_id: 2, slot_code: "TL-1", occupancy_status: "vacant" }],
    isLoading: false,
  }),
}));

function overviewFixture(): CareerOverview {
  return {
    employee_id: 1,
    current_position: {
      definition_id: 1,
      code: "engineer",
      name: "Software Engineer",
      since: "2026-01-01",
    },
    next_positions: [
      {
        target_position: { id: 2, code: "team_lead", name: "Team Lead" },
        transition_type: "promotion",
        readiness_status: "DEVELOPMENT_NEEDED",
        position_fit: {
          known_fit_score: 0.78,
          fit_confidence: 0.69,
          required_coverage: 0.8,
          qualification_status: "QUALIFIED_WITH_GAPS",
          fit_status: "PARTIAL_MATCH",
        },
        required_gaps: ["architecture"],
        uncertainties: ["management"],
      },
    ],
    plans: [
      {
        id: 7,
        title: "发展计划：Team Lead",
        status: "active",
        target_position_definition_id: 2,
        item_count: 3,
        completed_count: 1,
        created_at: "2026-08-01",
      },
    ],
    timeline: [
      {
        type: "promoted",
        at: "2026-09-01",
        title: "promoted",
        reason: "ok",
        source: "career_event",
      },
      {
        type: "assessment_completed",
        at: "2026-08-15",
        title: "assessment #12",
        reason: "manual",
        source: "assessment_run",
      },
    ],
  };
}

describe("CareerDevelopmentTab", () => {
  it("shows next position readiness without inventing a single score", () => {
    render(<CareerDevelopmentTab employeeId={1} />);
    expect(screen.getByText("Team Lead")).toBeInTheDocument();
    expect(screen.getByText("DEVELOPMENT_NEEDED")).toBeInTheDocument();
    // Fit 与 Confidence 并列（不合并成一个综合分）
    expect(screen.getByText(/78%/)).toBeInTheDocument();
    expect(screen.getByText(/69%/)).toBeInTheDocument();
    // Required Gap 与 Evidence 不足（uncertainties）分开展示
    expect(screen.getByText(/architecture/)).toBeInTheDocument();
    expect(screen.getByText(/management/)).toBeInTheDocument();
  });

  it("lists development plans with progress, not XP", () => {
    render(<CareerDevelopmentTab employeeId={1} />);
    expect(screen.getByText("发展计划：Team Lead")).toBeInTheDocument();
    expect(screen.getByText("1/3")).toBeInTheDocument();
  });

  it("renders career timeline from real sources", () => {
    render(<CareerDevelopmentTab employeeId={1} />);
    expect(screen.getByText(/promoted/)).toBeInTheDocument();
    expect(screen.getByText(/assessment #12/)).toBeInTheDocument();
  });
});
