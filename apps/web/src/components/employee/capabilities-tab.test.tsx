import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { CapabilitiesTab } from "./capabilities-tab";
import type { EmployeeCapabilities, TraitView } from "../../types";

vi.mock("../../hooks/useCompetencies", () => ({
  useEmployeeCapabilities: () => ({
    data: capabilitiesFixture(),
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
  useEmployeeTraits: () => ({
    data: traitsFixture(),
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
  useEmployeeCompetencyEvidence: () => ({
    data: [],
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
  useCompetencyExplanation: () => ({
    data: explanationFixture(),
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
}));

function capabilitiesFixture(): EmployeeCapabilities {
  return {
    general: [
      {
        competency_definition_id: 1,
        domain_id: 1,
        domain_code: "general",
        domain_name: "通用能力",
        code: "execution",
        name: "执行能力",
        description: "",
        kind: "general",
        score: null,
        confidence: null,
        evidence_count: 0,
        status: "unrated",
        trend: null,
        trend_direction: "unknown",
        last_assessed_at: null,
      },
      {
        competency_definition_id: 2,
        domain_id: 1,
        domain_code: "general",
        domain_name: "通用能力",
        code: "communication",
        name: "沟通表达",
        description: "",
        kind: "general",
        score: 72,
        confidence: 0.68,
        evidence_count: 17,
        status: "assessed",
        trend: 4,
        trend_direction: "up",
        last_assessed_at: "2026-09-01T00:00:00Z",
      },
    ],
    professional: [],
  };
}

function explanationFixture() {
  return {
    competency_definition_id: 2,
    code: "execution",
    name: "执行能力",
    domain_code: "general",
    domain_name: "通用能力",
    score: 72,
    confidence: 0.68,
    evidence_count: 17,
    status: "assessed",
    trend: 4,
    trend_direction: "up",
    last_assessed_at: "2026-09-01T00:00:00Z",
    assessment_history: [
      {
        run_id: 9,
        profile_code: "software_engineer",
        profile_version: 1,
        assessment_type: "project_end",
        triggered_by: "project_end",
        window_from: null,
        window_to: "2026-09-01T00:00:00Z",
        score: 72,
        previous_score: 68,
        trend: 4,
        status: "assessed",
        evidence_count: 17,
        created_at: "2026-09-01T12:00:00Z",
      },
    ],
    recent_evidence: [
      {
        id: 1,
        source_kind: "task",
        source_id: 3,
        source_ref: "task:3 (Implement Auth)",
        signal: 85,
        strength: 1,
        reliability: 0.7,
        occurred_at: "2026-09-01T00:00:00Z",
      },
    ],
    source_distribution: [
      { source_kind: "task", count: 12 },
      { source_kind: "review", count: 5 },
    ],
    recent_criterion_results: [
      {
        criterion_code: "delivery_reliability",
        criterion_name: "Delivery Reliability",
        observed: 82,
        confidence: 0.7,
        contribution: 49.2,
        evidence_count: 8,
      },
    ],
    relevant_skills: [],
  };
}

function traitsFixture(): TraitView[] {
  const dims = [
    ["curiosity", "好奇心", true],
    ["warmth", "热情与亲和", false],
    ["independence", "独立倾向", false],
  ] as const;
  return dims.map(([code, label, affects_execution], index) => ({
    code,
    label,
    description: `${label} 行为描述`,
    value: 0.5 + index * 0.1,
    display: 50 + index * 10,
    affects_execution,
  }));
}

describe("CapabilitiesTab", () => {
  it("shows all eight-first-version trait dimensions with descriptions, not bonuses", () => {
    render(<CapabilitiesTab employeeId={1} />);
    expect(screen.getByText("好奇心")).toBeInTheDocument();
    expect(screen.getByText("热情与亲和")).toBeInTheDocument();
    expect(screen.getByText("独立倾向")).toBeInTheDocument();
    expect(screen.getAllByText("Not affecting execution yet")).toHaveLength(2);
  });

  it("never renders an unrated competency as a 0 score", () => {
    render(<CapabilitiesTab employeeId={1} />);
    const execution = screen.getByText("执行能力").closest("li");
    expect(execution).not.toHaveTextContent("0");
    expect(execution).toHaveTextContent("Unrated");
  });

  it("renders assessed competency with score, confidence and trend side by side", () => {
    render(<CapabilitiesTab employeeId={1} />);
    const communication = screen.getByText("沟通表达").closest("li");
    expect(communication).toHaveTextContent("72");
    expect(communication).toHaveTextContent("Confidence 68%");
    expect(communication).toHaveTextContent("+4");
    expect(communication).toHaveTextContent("17 Evidence");
  });

  it("opens explanation panel on competency click with traceable history", () => {
    render(<CapabilitiesTab employeeId={1} />);
    fireEvent.click(screen.getByTestId("competency-row-execution"));
    expect(screen.getByText("Competency detail · 执行能力")).toBeInTheDocument();
    // 历史 run 可见（profile 版本 + trend）
    const history = screen.getByText(/software_engineer v1/);
    expect(history).toBeInTheDocument();
    expect(screen.getAllByText(/\+4/).length).toBeGreaterThan(0);
    // 来源分布可见
    expect(screen.getByText(/task=12 review=5/)).toBeInTheDocument();
    // 最近证据可反查
    expect(screen.getByText("task:3 (Implement Auth)")).toBeInTheDocument();
  });
});
