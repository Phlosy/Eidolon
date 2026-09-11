import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MarketPage } from "./market-page";
import type { MarketListing } from "../../api/market";

const STATE = vi.hoisted(() => ({
  listings: [] as MarketListing[],
  total: 0,
  list: vi.fn(),
  delist: vi.fn(),
  characters: [] as Array<Record<string, unknown>>,
}));

vi.mock("../../hooks/useMarket", () => ({
  useMarketListings: (filters: Record<string, unknown>) => ({
    data: { items: STATE.listings, total: STATE.total, limit: 60, offset: 0 },
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
    filters,
  }),
  useCreateMarketListing: () => ({
    mutate: STATE.list,
    isPending: false,
    isError: false,
    error: null,
  }),
  useDelistMarketListing: () => ({
    mutate: STATE.delist,
    isPending: false,
    isError: false,
    error: null,
  }),
}));

vi.mock("../../hooks/useCultivation", () => ({
  useCultivationCharacters: () => ({
    data: STATE.characters,
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
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
    isLoading: false,
  }),
}));

function makeListing(overrides: Partial<MarketListing> = {}): MarketListing {
  return {
    listing_id: 1,
    identity_id: "CH-000000000001",
    name: "林舟",
    avatar: "",
    origin: "issued",
    cultivation_state: "ready",
    status: "active",
    quality_tier: "rare",
    listed_at: "2026-09-01T00:00:00Z",
    closed_at: null,
    listed_by: "Eidolon 发行方",
    fit: null,
    ...overrides,
  };
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/market"]}>
      <MarketPage />
    </MemoryRouter>,
  );
}

describe("MarketPage", () => {
  beforeEach(() => {
    STATE.listings = [];
    STATE.total = 0;
    STATE.characters = [];
    STATE.list.mockReset();
    STATE.delist.mockReset();
  });

  it("shows the market description and empty state when nobody is listed", () => {
    renderPage();
    expect(screen.getByText("Talent market")).toBeInTheDocument();
    expect(screen.getByText("No listed talents right now")).toBeInTheDocument();
  });

  it("renders listed talents with identity, tier and lister (no power score)", () => {
    STATE.listings = [makeListing()];
    STATE.total = 1;
    renderPage();
    const card = within(screen.getByTestId("market-listing-1"));
    expect(card.getByText("林舟")).toBeInTheDocument();
    expect(card.getByText("CH-000000000001")).toBeInTheDocument();
    expect(card.getByText("rare")).toBeInTheDocument(); // 档位徽章（筛选下拉里也有 "rare"，故限定在卡片内）
    expect(card.getByText(/Eidolon 发行方/)).toBeInTheDocument();
    expect(screen.getByText("1 listed talents")).toBeInTheDocument();
  });

  it("shows fit summary cells on cards when the list carries a position filter", () => {
    STATE.listings = [
      makeListing({
        fit: {
          position_definition_id: 4,
          fit_status: "STRONG_MATCH",
          qualification_status: "QUALIFIED",
          known_fit_score: 0.93,
          fit_confidence: 0.6,
          requirement_coverage: 1,
          known_count: 2,
          total_count: 2,
        },
      }),
    ];
    STATE.total = 1;
    renderPage();
    expect(screen.getByText("Strong match")).toBeInTheDocument();
    expect(screen.getByText("93%")).toBeInTheDocument();
    expect(screen.getByText("2/2 requirements evaluated")).toBeInTheDocument();
  });

  it("lists a ready own character and delists an active own listing", () => {
    STATE.characters = [
      { id: 7, person_id: 70, identity_id: "CH-OWN00000001", name: "我的角色", lifecycle: "ready" },
    ];
    STATE.listings = [
      makeListing({ listing_id: 9, identity_id: "CH-OWN00000001", name: "我的角色" }),
    ];
    STATE.total = 1;
    renderPage();

    // 已在市 → 出现下架按钮（按 identity_id 对上自己的角色）
    fireEvent.click(screen.getByTestId("delist-9"));
    expect(STATE.delist).toHaveBeenCalledWith(9);
  });

  it("offers listing for a ready character that is not on the market", () => {
    STATE.characters = [
      { id: 8, person_id: 80, identity_id: "CH-OWN00000002", name: "待挂牌", lifecycle: "ready" },
    ];
    renderPage();
    fireEvent.click(screen.getByTestId("list-80"));
    expect(STATE.list).toHaveBeenCalledWith({ personId: 80 });
  });
});
