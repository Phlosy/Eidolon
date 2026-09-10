import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CultivationDetailPage } from "./cultivation-detail-page";
import type { CultivationCharacterDetail } from "../../api/cultivation";

const STATE = vi.hoisted(() => ({
  detail: null as CultivationCharacterDetail | null,
  advance: vi.fn(),
}));

vi.mock("../../hooks/useCultivation", () => ({
  useCultivationCharacter: () => ({
    data: STATE.detail,
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
  useAdvanceCultivationProgram: () => ({
    mutate: STATE.advance,
    isPending: false,
    isError: false,
    error: null,
  }),
  useFreeSession: () => ({
    mutate: vi.fn(),
    isPending: false,
    isError: false,
    error: null,
  }),
}));

function makeDetail(
  overrides: Partial<CultivationCharacterDetail> = {},
): CultivationCharacterDetail {
  return {
    id: 5,
    person_id: 50,
    identity_id: "CH-000000000005",
    name: "Nova",
    slug: "nova",
    origin: "trained",
    owner_company_id: 1,
    lifecycle: "cultivating",
    created_at: "2026-09-01T00:00:00Z",
    program: null,
    programs: [],
    events: [],
    traits: [],
    competencies: { general: [], professional: [] },
    ...overrides,
  } as CultivationCharacterDetail;
}

const ACTIVE_PROGRAM = {
  id: 9,
  template: "academic",
  current_stage: 1,
  stages_total: 4,
  resource_used: { sessions: 5, knowledge: 12 },
  status: "active",
  created_at: "2026-09-01T00:00:00Z",
};

function renderDetail() {
  return render(
    <MemoryRouter initialEntries={["/cultivation/5"]}>
      <Routes>
        <Route path="/cultivation/:id" element={<CultivationDetailPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("CultivationDetailPage", () => {
  beforeEach(() => {
    STATE.detail = makeDetail();
    STATE.advance.mockReset();
  });

  it("shows identity, lifecycle and the record timeline", () => {
    renderDetail();
    expect(screen.getByText("Nova")).toBeInTheDocument();
    expect(screen.getByText(/CH-000000000005/)).toBeInTheDocument();
    expect(screen.getByText("Cultivating")).toBeInTheDocument();
    expect(screen.getByTestId("education-timeline")).toBeInTheDocument();
  });

  it("offers stage advancement while a program is active", () => {
    STATE.detail = makeDetail({ programs: [ACTIVE_PROGRAM] });
    renderDetail();
    expect(screen.getByTestId("program-panel-9")).toHaveTextContent("Stage 1/4");
    expect(screen.getByTestId("program-panel-9")).toHaveTextContent("5 sessions · 12 knowledge");
    expect(screen.getByTestId("advance-program")).toBeInTheDocument();
    // 有进行中的模板 ⇒ 不出现自由养成入口（否则后端必然 409）
    expect(screen.queryByTestId("free-session-form")).not.toBeInTheDocument();
  });

  it("offers the free session form for a blank character", () => {
    STATE.detail = makeDetail({ origin: "blank" });
    renderDetail();
    expect(screen.getByTestId("free-session-form")).toBeInTheDocument();
    expect(screen.getByText(/No training program/)).toBeInTheDocument();
  });

  it("hides both write entries once the character is ready", () => {
    STATE.detail = makeDetail({
      lifecycle: "ready",
      programs: [{ ...ACTIVE_PROGRAM, status: "completed", current_stage: 4 }],
    });
    renderDetail();
    expect(screen.getByTestId("lifecycle-badge")).toHaveTextContent("Ready");
    expect(screen.queryByTestId("advance-program")).not.toBeInTheDocument();
    expect(screen.queryByTestId("free-session-form")).not.toBeInTheDocument();
    expect(screen.getByTestId("program-panel-9")).toHaveTextContent("All stages complete");
  });
});
