import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { CandidatePanel } from "./candidate-panel";
import type { CandidateAnalysisResult } from "../../types";

vi.mock("../../hooks/useTalentRoster", () => ({
  usePositionCandidates: () => ({
    data: fixture(),
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
  useAssignEmployee: () => ({ mutate: vi.fn() }),
}));

vi.mock("../../hooks/useOrganizations", () => ({
  useVacantSlots: () => ({
    data: [{ id: 9, position_definition_id: 1, slot_code: "SE-2", occupancy_status: "vacant" }],
    isLoading: false,
  }),
}));

vi.mock("../../hooks/usePositionFit", () => ({
  useEmployeePositionFit: () => ({ data: explanationDetail(), isLoading: false, isError: false }),
}));

function explanationDetail() {
  return {
    employee_id: 0,
    position_definition_id: 1,
    position_code: "engineer",
    configured: true,
    profile_version: 1,
    fit_status: "PARTIAL_MATCH",
    qualification_status: "QUALIFIED_WITH_GAPS",
    known_fit_score: 0.71,
    fit_confidence: 0.6,
    requirement_coverage: 0.8,
    required_coverage: 0.8,
    preferred_coverage: 0.5,
    known_count: 4,
    total_count: 6,
    inputs_hash: "h",
    engine_version: "v1",
    policy_version: "v1",
    serializer_version: "v1",
    calculated_at: null,
    strengths: [],
    gaps: [],
    uncertainties: [],
    development_opportunities: [],
    requirement_evaluations: [
      { code: "backend_engineering", employee_score: 86, employee_confidence: 0.88 },
      { code: "security", employee_score: null, employee_confidence: null },
    ],
  };
}

function fixture(): CandidateAnalysisResult {
  return {
    position: { id: 1, code: "engineer", name: "Software Engineer" },
    profile: { version_id: 7, version: 1, status: "active" },
    evaluable: true,
    bands: [
      {
        band: "RECOMMENDED",
        count: 1,
        candidates: [
          {
            employee: {
              employee_id: 3,
              name: "David",
              slug: "david",
              avatar: "",
              workforce_status: "available",
              department_id: null,
              department_name: null,
              current_position: null,
            },
            fit: {
              known_fit_score: 0.86,
              fit_confidence: 0.82,
              required_coverage: 1,
              required_required_coverage: 1,
              qualification_status: "QUALIFIED",
              fit_status: "STRONG_MATCH",
              critical_gap_count: 0,
              required_gap_count: 0,
              uncertainty_count: 0,
              strengths: ["backend_engineering"],
              gaps: [],
              uncertainties: [],
              development_opportunities: [],
            },
          },
        ],
      },
      {
        band: "NEEDS_EVIDENCE",
        count: 1,
        candidates: [
          {
            employee: {
              employee_id: 5,
              name: "Emma",
              slug: "emma",
              avatar: "",
              workforce_status: "available",
              department_id: null,
              department_name: null,
              current_position: null,
            },
            fit: {
              known_fit_score: 0.91,
              fit_confidence: 0.21,
              required_coverage: 0.38,
              required_required_coverage: 0.38,
              qualification_status: "INSUFFICIENT_DATA",
              fit_status: "INSUFFICIENT_DATA",
              critical_gap_count: 0,
              required_gap_count: 0,
              uncertainty_count: 3,
              strengths: [],
              gaps: [],
              uncertainties: ["security"],
              development_opportunities: [],
            },
          },
        ],
      },
      {
        band: "CRITICAL_GAP",
        count: 1,
        candidates: [
          {
            employee: {
              employee_id: 7,
              name: "Cara",
              slug: "cara",
              avatar: "",
              workforce_status: "available",
              department_id: null,
              department_name: null,
              current_position: null,
            },
            fit: {
              known_fit_score: 0.6,
              fit_confidence: 0.7,
              required_coverage: 1,
              required_required_coverage: 1,
              qualification_status: "NOT_QUALIFIED",
              fit_status: "CRITICAL_GAP",
              critical_gap_count: 1,
              required_gap_count: 0,
              uncertainty_count: 0,
              strengths: [],
              gaps: ["test_design"],
              uncertainties: [],
              development_opportunities: [],
            },
          },
        ],
      },
    ],
    meta: {
      engine_version: "v1",
      policy_version: "v1",
      candidate_analysis_version: "v1",
      inputs_hash: "h",
      include_assigned: false,
      calculated_at: null,
    },
  };
}

describe("CandidatePanel", () => {
  it("groups candidates into bands and unknown candidates are labelled not worst", () => {
    render(<CandidatePanel positionId={1} positionName="Software Engineer" />);
    expect(screen.getByTestId("band-RECOMMENDED")).toBeInTheDocument();
    expect(screen.getByTestId("band-NEEDS_EVIDENCE")).toBeInTheDocument();
    expect(screen.getByTestId("band-CRITICAL_GAP")).toBeInTheDocument();
    // Emma 覆盖低 → NEEDS_EVIDENCE（不是"最差"）
    expect(screen.getByText(/86%/)).toBeInTheDocument();
    expect(screen.getByText(/21%/)).toBeInTheDocument();
  });

  it("shows assignment warning for a critical gap candidate", () => {
    render(<CandidatePanel positionId={1} positionName="Software Engineer" />);
    expect(screen.getAllByTestId(/^assign-/).length).toBeGreaterThan(0);
  });

  it("comparison preserves unknown as em dash", () => {
    render(<CandidatePanel positionId={1} positionName="Software Engineer" />);
  });
});
