import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MarketListingPage } from "./market-listing-page";
import type { MarketCandidate } from "../../api/market";

const STATE = vi.hoisted(() => ({
  candidate: null as MarketCandidate | null,
  fit: null as Record<string, unknown> | null,
  recruit: vi.fn(),
  recruitState: {
    isPending: false,
    isError: false,
    isSuccess: false,
    error: null as unknown,
    data: null as unknown,
  },
}));

vi.mock("../../hooks/useMarket", () => ({
  useMarketListing: () => ({
    data: STATE.candidate,
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
  useMarketFit: () => ({
    data: STATE.fit,
    isLoading: false,
    isError: false,
    error: null,
  }),
  useRecruitListing: () => ({
    mutate: STATE.recruit,
    isPending: STATE.recruitState.isPending,
    isError: STATE.recruitState.isError,
    isSuccess: STATE.recruitState.isSuccess,
    error: STATE.recruitState.error,
    data: STATE.recruitState.data,
  }),
}));

vi.mock("../../hooks/usePositionProfiles", () => ({
  usePositionProfiles: () => ({
    data: [
      {
        position_definition_id: 4,
        code: "engineer",
        name: "Engineer",
        active_version: 1,
        profile_status: "active",
        requirement_count: 2,
        assessment_profile_code: null,
      },
    ],
  }),
}));

vi.mock("../../hooks/useSystem", () => ({
  useCompany: () => ({
    data: { id: 1, name: "TestCo", departments: [{ id: 2, name: "Engineering", slug: "eng" }] },
  }),
}));

vi.mock("../../hooks/useOrganizations", () => ({
  useVacantSlots: () => ({
    data: [{ id: 5, slot_code: "ENG-4", department_id: 2, position_name: "Engineer" }],
  }),
}));

function makeCandidate(): MarketCandidate {
  return {
    listing: {
      listing_id: 3,
      status: "active",
      quality_tier: "rare",
      listed_at: "2026-09-01T00:00:00Z",
      listed_by: "星海科技",
    },
    identity: {
      identity_id: "CH-000000000003",
      name: "苏禾",
      avatar: "",
      origin: "issued",
      cultivation_state: "ready",
    },
    traits: [
      {
        code: "curiosity",
        label: "好奇心",
        description: "",
        value: 0.6,
        display: 60,
        affects_execution: true,
      },
    ],
    competencies: {
      general: [
        {
          competency_definition_id: 1,
          domain_id: 1,
          domain_code: "general",
          domain_name: "General",
          code: "analysis_problem_solving",
          name: "Analysis & Problem Solving",
          description: "",
          kind: "general",
          score: 82,
          confidence: 0.7,
          evidence_count: 8,
          status: "assessed",
          trend: null,
          trend_direction: "unknown",
          last_assessed_at: null,
        },
      ],
      professional: [],
    },
    knowledge_summary: {
      total: 4,
      by_scope: { private: 4 },
      top_topics: [{ topic: "分布式系统", count: 3 }],
    },
    timeline: [
      {
        id: 11,
        program_id: 1,
        kind: "course",
        topic: "非规范自学",
        outcome: { knowledge_produced: 3 },
        evidence_id: 9,
        occurred_at: "2026-09-02T00:00:00Z",
      },
    ],
    evidence: [
      {
        id: 9,
        competency_code: "analysis_problem_solving",
        competency_name: "分析与问题解决",
        source_kind: "edu_course",
        source_ref: "learning_session://9",
        assessment_run_id: null,
        signal: 78,
        quality: 0.5,
        occurred_at: "2026-09-02T00:00:00Z",
      },
    ],
    market_state: "listed",
  };
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/market/3"]}>
      <Routes>
        <Route path="/market/:listingId" element={<MarketListingPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("MarketListingPage", () => {
  beforeEach(() => {
    STATE.candidate = makeCandidate();
    STATE.fit = null;
    STATE.recruit.mockReset();
    STATE.recruitState = {
      isPending: false,
      isError: false,
      isSuccess: false,
      error: null,
      data: null,
    };
  });

  it("renders the candidate record: identity, traits, competencies, timeline and evidence", () => {
    renderPage();
    expect(screen.getByText("苏禾")).toBeInTheDocument();
    expect(screen.getByText(/CH-000000000003/)).toBeInTheDocument();
    expect(screen.getByText("Curiosity")).toBeInTheDocument();
    expect(screen.getByTestId("competency-analysis_problem_solving")).toHaveTextContent("82");
    expect(screen.getByTestId("education-timeline")).toHaveTextContent("3 knowledge items");
    expect(screen.getByTestId("market-evidence")).toHaveTextContent("learning_session://9");
    expect(screen.getByTestId("market-knowledge-topics")).toHaveTextContent("分布式系统");
  });

  it("keeps unevaluated competencies as 'Not assessed' (never 0)", () => {
    STATE.candidate = {
      ...makeCandidate(),
      competencies: {
        general: [
          {
            ...makeCandidate().competencies.general[0],
            score: null,
            confidence: null,
            evidence_count: 0,
            status: "unrated",
          },
        ],
        professional: [],
      },
    };
    renderPage();
    expect(screen.getByTestId("competency-analysis_problem_solving")).toHaveTextContent(
      "Not assessed",
    );
  });

  it("shows fit score/confidence/gaps after picking a position", () => {
    STATE.fit = {
      listing_id: 3,
      position_definition_id: 4,
      position_code: "engineer",
      configured: true,
      profile_version_id: 1,
      profile_version: 1,
      fit_status: "PARTIAL_MATCH",
      qualification_status: "QUALIFIED_WITH_GAPS",
      known_fit_score: 0.72,
      overall_fit_score: null,
      fit_confidence: 0.55,
      requirement_coverage: 0.5,
      required_coverage: 0.5,
      preferred_coverage: 0,
      known_count: 1,
      total_count: 2,
      general_fit: null,
      professional_fit: null,
      strengths: [],
      gaps: [
        {
          code: "execution",
          name: "Execution",
          domain_code: "general",
          kind: "general",
          requirement_type: "required",
          critical: false,
          minimum_score: 55,
          target_score: 75,
          minimum_confidence: 0.4,
          candidate_score: 40,
          candidate_confidence: 0.6,
          evaluation_status: "BELOW_MINIMUM",
          reason_code: "BELOW_MINIMUM",
          gap_type: "REQUIRED_GAP",
          is_unknown: false,
          is_strength: false,
          is_development_opportunity: false,
          margin_to_minimum: -15,
          margin_to_target: -35,
        },
      ],
      uncertainties: [],
      development_opportunities: [],
      requirement_evaluations: [],
      engine_version: "p8-fit-1",
      policy_version: "p8-policy-1",
      serializer_version: "v1",
      calculated_at: null,
    };
    renderPage();
    fireEvent.change(screen.getByTestId("market-fit-position"), { target: { value: "4" } });
    expect(screen.getByText("Partial match")).toBeInTheDocument();
    expect(screen.getByText("72%")).toBeInTheDocument();
    expect(screen.getByText("55%")).toBeInTheDocument();
    expect(screen.getByTestId("fit-requirement-execution")).toHaveTextContent("BELOW_MINIMUM");
  });

  it("recruits through the dialog with the chosen department and slot", () => {
    renderPage();
    fireEvent.click(screen.getByTestId("open-recruit"));
    fireEvent.change(screen.getByTestId("recruit-department"), { target: { value: "2" } });
    fireEvent.change(screen.getByTestId("recruit-slot"), { target: { value: "5" } });
    fireEvent.change(screen.getByTestId("recruit-title"), {
      target: { value: "Backend Engineer" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Confirm recruit/ }));

    expect(STATE.recruit).toHaveBeenCalledTimes(1);
    const [payload] = STATE.recruit.mock.calls[0] as [
      { listingId: number; body: Record<string, unknown> },
    ];
    expect(payload.listingId).toBe(3);
    expect(payload.body).toMatchObject({
      department_id: 2,
      position_slot_id: 5,
      title: "Backend Engineer",
    });
  });

  it("surfaces a stale-listing failure with a human message", () => {
    STATE.recruitState = {
      isPending: false,
      isError: true,
      isSuccess: false,
      error: new Error("Request failed with status 409"),
      data: null,
    };
    renderPage();
    fireEvent.click(screen.getByTestId("open-recruit"));
    expect(screen.getByRole("alert")).toHaveTextContent("Recruit failed");
  });
});
