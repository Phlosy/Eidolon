import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { CharacterProfile } from "./character-profile";
import type { CultivationCharacterDetail } from "../../api/cultivation";

function makeDetail(
  overrides: Partial<CultivationCharacterDetail> = {},
): CultivationCharacterDetail {
  return {
    id: 1,
    person_id: 11,
    identity_id: "CH-000000000001",
    name: "Ada Cultivar",
    slug: "ada-cultivar",
    origin: "trained",
    owner_company_id: 1,
    lifecycle: "cultivating",
    created_at: "2026-09-01T00:00:00Z",
    program: null,
    programs: [],
    events: [],
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
    ...overrides,
  } as CultivationCharacterDetail;
}

describe("CharacterProfile", () => {
  it("renders personality traits from the registry-backed read model", () => {
    render(<CharacterProfile character={makeDetail()} />);
    // 英文界面下人格标签走 i18n（API 的 label 只作缺键兜底）
    expect(screen.getByText("Curiosity")).toBeInTheDocument();
    expect(screen.getByText("62")).toBeInTheDocument();
  });

  it("shows 'Not assessed' instead of 0 for dimensions without evidence", () => {
    render(<CharacterProfile character={makeDetail()} />);
    expect(screen.getByTestId("competency-execution")).toHaveTextContent("Not assessed");
    expect(screen.getByTestId("competency-execution")).not.toHaveTextContent("0");
  });

  it("renders score with equal-weight confidence and evidence count", () => {
    render(<CharacterProfile character={makeDetail()} />);
    const row = screen.getByTestId("competency-analysis_problem_solving");
    expect(row).toHaveTextContent("71");
    expect(row).toHaveTextContent("confidence 80%");
    expect(row).toHaveTextContent("4 evidence");
  });

  it("counts free-cultivation sessions and knowledge from record events", () => {
    render(
      <CharacterProfile
        character={makeDetail({
          events: [
            {
              id: 1,
              program_id: null,
              kind: "course",
              topic: "Distributed systems",
              outcome: { knowledge_produced: 3, session_ids: [1] },
              evidence_id: 9,
              occurred_at: "2026-09-03T00:00:00Z",
            },
          ],
        })}
      />,
    );
    expect(screen.getByText("Sessions")).toBeInTheDocument();
    expect(screen.getByText("Knowledge")).toBeInTheDocument();
  });
});
