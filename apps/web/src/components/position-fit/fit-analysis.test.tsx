import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { FitAnalysis } from "./fit-analysis";
import type { PositionFitResult } from "../../types";

vi.mock("../../hooks/useCompetencies", () => ({
  useCompetencyExplanation: () => ({
    data: {
      score: 72,
      confidence: 0.68,
      evidence_count: 17,
      assessment_history: [],
    },
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
}));

function fixture(status: "PARTIAL_MATCH" | "INSUFFICIENT_DATA"): PositionFitResult {
  return {
    employee_id: 1,
    position_definition_id: 2,
    position_code: "engineer",
    configured: true,
    profile_version_id: 7,
    profile_version: 1,
    profile_status: "active",
    assessment_profile_code: "software_engineer",
    fit_status: status,
    qualification_status: status === "PARTIAL_MATCH" ? "QUALIFIED_WITH_GAPS" : "INSUFFICIENT_DATA",
    known_fit_score: status === "PARTIAL_MATCH" ? 0.77 : null,
    overall_fit_score: status === "PARTIAL_MATCH" ? 0.77 : null,
    fit_confidence: status === "PARTIAL_MATCH" ? 0.61 : null,
    requirement_coverage: status === "PARTIAL_MATCH" ? 0.6 : 0,
    required_coverage: status === "PARTIAL_MATCH" ? 0.75 : 0,
    preferred_coverage: 0.5,
    known_count: 3,
    total_count: 5,
    general_fit: 0.8,
    professional_fit: 0.7,
    strengths: [
      {
        requirement_id: 1,
        competency_definition_id: 11,
        code: "execution",
        name: "执行能力",
        domain_code: "general",
        domain_name: "通用能力",
        kind: "general",
        requirement_type: "required",
        critical: false,
        minimum_score: 70,
        target_score: 85,
        minimum_confidence: null,
        weight: 0.15,
        employee_score: 95,
        employee_confidence: 0.9,
        evaluation_status: "MEETS_TARGET",
        reason_code: "MEETS_TARGET",
        gap_type: null,
        is_strength: true,
        is_development_opportunity: false,
        is_unknown: false,
        normalized_fit: 1,
        margin_to_minimum: 25,
        margin_to_target: 10,
      },
    ],
    gaps: [
      {
        requirement_id: 2,
        competency_definition_id: 12,
        code: "backend_engineering",
        name: "后端工程",
        domain_code: "software_engineering",
        domain_name: "软件工程",
        kind: "professional",
        requirement_type: "required",
        critical: true,
        minimum_score: 65,
        target_score: 80,
        minimum_confidence: null,
        weight: 0.15,
        employee_score: 40,
        employee_confidence: 0.9,
        evaluation_status: "BELOW_MINIMUM",
        reason_code: "BELOW_MINIMUM",
        gap_type: "CRITICAL_GAP",
        is_strength: false,
        is_development_opportunity: false,
        is_unknown: false,
        normalized_fit: 0.42,
        margin_to_minimum: -25,
        margin_to_target: -40,
      },
    ],
    uncertainties: [
      {
        requirement_id: 3,
        competency_definition_id: 13,
        code: "security",
        name: "安全工程",
        domain_code: "software_engineering",
        domain_name: "软件工程",
        kind: "professional",
        requirement_type: "preferred",
        critical: false,
        minimum_score: 45,
        target_score: 65,
        minimum_confidence: 0.5,
        weight: 0.1,
        employee_score: 82,
        employee_confidence: 0.16,
        evaluation_status: "INSUFFICIENT_CONFIDENCE",
        reason_code: "CONFIDENCE_BELOW_REQUIREMENT",
        gap_type: "UNRATED",
        is_strength: false,
        is_development_opportunity: false,
        is_unknown: true,
        normalized_fit: null,
        margin_to_minimum: null,
        margin_to_target: null,
      },
      {
        requirement_id: 4,
        competency_definition_id: 14,
        code: "testing",
        name: "测试工程",
        domain_code: "software_engineering",
        domain_name: "软件工程",
        kind: "professional",
        requirement_type: "required",
        critical: false,
        minimum_score: 60,
        target_score: 75,
        minimum_confidence: null,
        weight: 0.1,
        employee_score: null,
        employee_confidence: null,
        evaluation_status: "UNRATED",
        reason_code: "UNRATED",
        gap_type: "REQUIRED_UNCERTAINTY",
        is_strength: false,
        is_development_opportunity: false,
        is_unknown: true,
        normalized_fit: null,
        margin_to_minimum: null,
        margin_to_target: null,
      },
    ],
    development_opportunities: [],
    requirement_evaluations: [],
    engine_version: "v1",
    policy_version: "v1",
    serializer_version: "v1",
    inputs_hash: "abc",
    calculated_at: null,
  };
}

describe("FitAnalysis", () => {
  it("shows fit and confidence together, and unrated as Unrated not 0", () => {
    render(<FitAnalysis result={fixture("PARTIAL_MATCH")} employeeId={1} />);
    expect(screen.getByText("77%")).toBeInTheDocument(); // known fit
    expect(screen.getByText("61%")).toBeInTheDocument(); // confidence
    expect(screen.getByText("75%")).toBeInTheDocument(); // required coverage
    const unratedRow = screen.getByTestId("fit-row-testing");
    expect(unratedRow).toHaveTextContent("Unrated");
    expect(unratedRow).not.toHaveTextContent("Unrated0");
  });

  it("distinguishes critical gap from critical uncertainty", () => {
    render(<FitAnalysis result={fixture("PARTIAL_MATCH")} employeeId={1} />);
    const gapRow = screen.getByTestId("fit-row-backend_engineering");
    expect(gapRow).toHaveTextContent("BELOW_MINIMUM");
    const uncertaintyRow = screen.getByTestId("fit-row-security");
    expect(uncertaintyRow).toHaveTextContent("INSUFFICIENT_CONFIDENCE");
    // 高估低置信不是 0：employee marker 存在且展示 82
    expect(uncertaintyRow).toHaveTextContent("82");
  });

  it("shows employee overlay marker on the fit scale", () => {
    const { container } = render(<FitAnalysis result={fixture("PARTIAL_MATCH")} employeeId={1} />);
    expect(container.querySelectorAll('[data-testid="employee-marker"]').length).toBeGreaterThan(0);
  });

  it("shows Insufficient Data instead of fake precision when coverage is low", () => {
    render(<FitAnalysis result={fixture("INSUFFICIENT_DATA")} employeeId={1} />);
    expect(screen.getByTestId("fit-status")).toHaveTextContent("INSUFFICIENT_DATA");
    expect(screen.getByText(/Evidence coverage insufficient/)).toBeInTheDocument();
  });
});
