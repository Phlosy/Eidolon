import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Decision } from "../../api/types";
import i18n from "../../i18n";
import { renderPage } from "../../test/render";
import { DashboardPage } from "./dashboard-page";

const company = {
  company_id: "c1",
  name: "Tiny Studio",
  slug: "tiny",
  currency: "CNY",
  owner_user_id: "u1",
  created_at: "2026-11-07T00:00:00Z",
  institution: {
    template_id: "software-studio-v1",
    version: 2,
    name: "Studio",
    fingerprint: "f",
    changes: 1,
  },
  headcount: 5,
};
const pending: Decision = {
  decision_id: "d1",
  company_id: "c1",
  gate: "project.deliver",
  status: "pending",
  resolution: {
    decider: "human",
    role: "owner",
    quorum: 1,
    allowed_roles: ["owner"],
    require_reason: false,
  },
  subject: {
    project_id: "p1",
    action: { kind: "project.deliver", project_id: "p1" },
    action_hash: "h",
  },
  proposer: "pm-1",
  author: null,
  amount: null,
  requested_at: "2026-11-07T10:00:00Z",
  signatures: [],
  escalations: 0,
  brief: {
    recommendation: "approve",
    summary: "The notes CLI does what was asked and its tests pass; accept it.",
    rationale: "Three tasks, each reviewed by QA.",
    risks: ["No manual run of the delete command"],
    alternatives: ["Ask for a short demo first"],
    advisor: "pm-1",
    advisor_role: "pm",
    cost: null,
  },
};
const sign = vi.fn();

vi.mock("../../features/auth/auth-context", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../features/auth/auth-context")>();
  return {
    ...actual,
    useAuth: () => ({
      me: {
        user_id: "u1",
        email: "o@x.io",
        display_name: "Owner",
        person_ids: ["owner-1"],
        companies: [{ company_id: "c1", person_id: "owner-1", role: "owner", title: "Owner" }],
        session_id: "s",
      },
      isLoading: false,
      error: null,
      setMe: vi.fn(),
      refresh: vi.fn(),
    }),
  };
});
vi.mock("../../api", () => ({
  companies: {
    one: vi.fn(() => Promise.resolve(company)),
    employees: vi.fn(() => Promise.resolve([{ person_id: "owner-1" }, { person_id: "eng-1" }])),
  },
  work: {
    missions: vi.fn().mockResolvedValue([
      {
        mission_id: "m1",
        title: "Notes CLI",
        goal: "A tiny note keeper.",
        status: "accepted",
        budget: 20000,
        created_at: "2026-11-07T09:00:00Z",
      },
    ]),
    projects: vi.fn(() => Promise.resolve([{ project_id: "p1", status: "active" }])),
    proposeMission: vi.fn(),
  },
  ledger: {
    balances: vi.fn(() =>
      Promise.resolve({ company_id: "c1", spend_micro: 1_500_000, accounts: [] }),
    ),
  },
  decisions: {
    pending: vi.fn(() => Promise.resolve([pending])),
    sign: (...args: unknown[]) => sign(...args),
  },
}));

describe("DashboardPage", () => {
  beforeEach(async () => {
    vi.clearAllMocks();
    await i18n.changeLanguage("en-US");
  });

  it("shows what waits for the owner, the missions and the spend", async () => {
    renderPage(<DashboardPage />);
    expect(await screen.findByRole("heading", { name: "Tiny Studio" })).toBeInTheDocument();
    expect((await screen.findAllByText("project.deliver")).length).toBe(2); // summary + card
    // the brief comes first, in plain words; the bound action folds under Details
    expect(
      screen.getByText("The notes CLI does what was asked and its tests pass; accept it."),
    ).toBeInTheDocument();
    expect(screen.getByText("pm-1 (pm) recommends")).toBeInTheDocument();
    expect(screen.getByText("No manual run of the delete command")).toBeInTheDocument();
    expect(screen.getByText("Details")).toBeInTheDocument();
    // R7-b: the day's summary counts what waits and repeats each brief in one line
    expect(screen.getByText("Today needs you on 1 things")).toBeInTheDocument();
    expect(screen.getByText("Notes CLI")).toBeInTheDocument();
    expect(await screen.findByText("1.50 CNY")).toBeInTheDocument();
    expect(screen.getByText("software-studio-v1 v2")).toBeInTheDocument();
  });

  it("signs a decision by restating the bound action", async () => {
    sign.mockResolvedValue({ ...pending, status: "approved", applied: "project.delivered" });
    renderPage(<DashboardPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
    await waitFor(() =>
      expect(sign).toHaveBeenCalledWith("d1", {
        verdict: "approve",
        reason: null,
        action: { kind: "project.deliver", project_id: "p1" },
      }),
    );
    expect(await screen.findByText("Applied: project.delivered")).toBeInTheDocument();
  });
});
