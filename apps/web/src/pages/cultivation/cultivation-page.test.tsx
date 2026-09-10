import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CultivationPage } from "./cultivation-page";
import type { CultivationCharacter } from "../../api/cultivation";

const STATE = vi.hoisted(() => ({
  characters: [] as CultivationCharacter[],
  create: vi.fn(),
}));

vi.mock("../../hooks/useCultivation", () => ({
  useCultivationCharacters: () => ({
    data: STATE.characters,
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
  useCreateCultivationCharacter: () => ({
    mutate: STATE.create,
    isPending: false,
    isError: false,
    reset: vi.fn(),
  }),
}));

function makeCharacter(overrides: Partial<CultivationCharacter> = {}): CultivationCharacter {
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
    ...overrides,
  };
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/cultivation"]}>
      <CultivationPage />
    </MemoryRouter>,
  );
}

describe("CultivationPage", () => {
  beforeEach(() => {
    STATE.characters = [];
    STATE.create.mockReset();
  });

  it("renders the empty state when no character is in cultivation", () => {
    renderPage();
    expect(screen.getByText("No characters in cultivation yet")).toBeInTheDocument();
  });

  it("renders character cards with identity, origin and stage progress", () => {
    STATE.characters = [
      makeCharacter({
        program: {
          template: "academic",
          current_stage: 2,
          stages_total: 4,
          status: "active",
        },
      }),
    ];
    renderPage();
    expect(screen.getByText("Ada Cultivar")).toBeInTheDocument();
    expect(screen.getByText("CH-000000000001")).toBeInTheDocument();
    expect(screen.getByText("Player-trained")).toBeInTheDocument();
    expect(screen.getByText("Stage 2/4")).toBeInTheDocument();
  });

  it("marks free cultivation characters without a template badge", () => {
    STATE.characters = [makeCharacter({ origin: "blank", name: "Blank Slate" })];
    renderPage();
    expect(screen.getByText("Free cultivation")).toBeInTheDocument();
    expect(screen.getByText("Blank")).toBeInTheDocument();
  });

  it("creates a trained character with the picked template", () => {
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: /New character/ }));
    fireEvent.change(screen.getByTestId("character-name"), { target: { value: "Nova" } });
    fireEvent.click(screen.getByTestId("template-vocational"));
    fireEvent.click(screen.getByRole("button", { name: "Create character" }));

    expect(STATE.create).toHaveBeenCalledTimes(1);
    const [payload] = STATE.create.mock.calls[0] as [Record<string, unknown>];
    expect(payload).toMatchObject({ name: "Nova", origin: "trained", template: "vocational" });
  });

  it("creates a blank character without a template program", () => {
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: /New character/ }));
    fireEvent.change(screen.getByTestId("character-name"), { target: { value: "Raw" } });
    fireEvent.click(screen.getByRole("button", { name: /Blank/ }));
    fireEvent.click(screen.getByRole("button", { name: "Create character" }));

    const [payload] = STATE.create.mock.calls[0] as [Record<string, unknown>];
    expect(payload).toMatchObject({ name: "Raw", origin: "blank" });
    expect(payload).not.toHaveProperty("template");
  });
});
