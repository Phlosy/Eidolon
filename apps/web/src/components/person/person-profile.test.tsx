import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PersonProfile } from "./person-profile";
import type { PersonProfile as PersonProfileModel } from "../../api/persons";

function makeProfile(overrides: Partial<PersonProfileModel> = {}): PersonProfileModel {
  return {
    identity: {
      person_id: 11,
      name: "Ada Cultivar",
      slug: "ada-cultivar",
      avatar: "",
      identity_id: "CH-000000000011",
      origin: "trained",
      cultivation_state: "ready",
      owner_company_id: 1,
      created_at: "2026-09-01T00:00:00Z",
    },
    traits: [
      {
        code: "curiosity",
        label: "好奇心",
        description: "",
        value: 0.62,
        display: 62,
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
          score: 71,
          confidence: 0.8,
          evidence_count: 4,
          status: "rated",
          trend: 3,
          trend_direction: "up",
          last_assessed_at: "2026-09-02T00:00:00Z",
        },
        {
          competency_definition_id: 2,
          domain_id: 1,
          domain_code: "general",
          domain_name: "General",
          code: "execution",
          name: "Execution",
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
      ],
      professional: [],
    },
    knowledge_summary: {
      total: 3,
      by_scope: { private: 2, company: 1 },
      top_topics: [{ topic: "Distributed systems", count: 2 }],
    },
    timeline: null,
    evidence: null,
    ...overrides,
  } as PersonProfileModel;
}

describe("PersonProfile", () => {
  it("renders identity, traits and competencies from the shared read model", () => {
    render(<PersonProfile profile={makeProfile()} />);
    expect(screen.getByText("Ada Cultivar")).toBeInTheDocument();
    expect(screen.getByText(/CH-000000000011/)).toBeInTheDocument();
    // 人格标签走 person 词表（英文界面下）
    expect(screen.getByText("Curiosity")).toBeInTheDocument();
    expect(screen.getByText("62")).toBeInTheDocument();
    expect(screen.getByTestId("competency-analysis_problem_solving")).toHaveTextContent("71");
    expect(screen.getByTestId("competency-analysis_problem_solving")).toHaveTextContent(
      "confidence 80%",
    );
  });

  it("shows 'Not assessed' rather than 0 for unevaluated dimensions", () => {
    render(<PersonProfile profile={makeProfile()} />);
    const row = screen.getByTestId("competency-execution");
    expect(row).toHaveTextContent("Not assessed");
    expect(row).not.toHaveTextContent("0 evidence");
  });

  it("renders knowledge summary with scope counts and topics (no content)", () => {
    render(<PersonProfile profile={makeProfile()} />);
    expect(screen.getByText("3 items")).toBeInTheDocument();
    expect(screen.getByText("Private 2")).toBeInTheDocument();
    expect(screen.getByText("Company 1")).toBeInTheDocument();
    expect(screen.getByTestId("knowledge-topics")).toHaveTextContent("Distributed systems");
  });

  it("handles employee-only persons without a cultivation profile", () => {
    render(
      <PersonProfile
        profile={makeProfile({
          identity: {
            ...makeProfile().identity,
            identity_id: null,
            origin: null,
            cultivation_state: null,
            owner_company_id: null,
          },
        })}
      />,
    );
    expect(screen.getByText(/No identity ID yet/)).toBeInTheDocument();
  });

  it("renders injected timeline/evidence slots (paging stays with the caller)", () => {
    render(
      <PersonProfile
        profile={makeProfile()}
        slots={{ timeline: <div data-testid="slot-timeline">timeline</div> }}
      />,
    );
    expect(screen.getByTestId("slot-timeline")).toBeInTheDocument();
  });
});
