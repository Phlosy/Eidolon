import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { WorkOrderView } from "../../api/workOrders";
import { WorkOrdersPage } from "./work-orders-page";

afterEach(() => vi.unstubAllGlobals());

it("submits corrected required deliverables for a rejected order through the real API client and refreshes settlement", async () => {
  // The first attempt was rejected for a missing report; this page must let
  // its owner supply both required values and observe the settled response.
  let order: WorkOrderView = {
    work_order_id: 7,
    code: "OB-00007",
    kind: "OFFICIAL_BOUNTY",
    title: "Write the onboarding doc",
    description: "",
    requirements: {},
    deliverables: { required_keys: ["readme", "report"] },
    reward_amount: 5_000,
    currency: "CREDIT",
    funding_mode: "system_mint",
    evaluation_mode: "auto",
    status: "REJECTED",
    deadline_at: null,
    issuer_actor_kind: "system",
    accepted_at: null,
    submitted_at: "2026-09-19T00:00:00Z",
    settled_at: null,
    assignee_company_id: 2,
    issuer_company_id: null,
    is_mine: true,
    is_issuer: false,
    escrow: null,
    submission_count: 1,
    payable_amount: 0,
    policy_version: "econ-1",
  };
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith("/work-orders/7/submit") && init?.method === "POST") {
      order = { ...order, status: "SETTLED", submission_count: 2, payable_amount: 5_000 };
      return new Response(JSON.stringify({ ...order, submissions: [], evaluations: [] }));
    }
    if (url.includes("/work-orders?")) {
      return new Response(JSON.stringify({ items: [order], total: 1, limit: 30, offset: 0 }));
    }
    throw new Error(`Unexpected request: ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <WorkOrdersPage />
    </QueryClientProvider>,
  );
  expect(await screen.findByText("Rejected")).toBeInTheDocument();
  fireEvent.change(screen.getByRole("textbox", { name: "Delivery summary" }), {
    target: { value: "Added the missing report" },
  });
  fireEvent.change(screen.getByRole("textbox", { name: "readme" }), {
    target: { value: "docs/readme.md" },
  });
  fireEvent.change(screen.getByRole("textbox", { name: "report" }), {
    target: { value: "docs/report.md" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Resubmit deliverables" }));

  expect(await screen.findByText("Settled")).toBeInTheDocument();
  const submission = fetchMock.mock.calls.find(([url]) => url.endsWith("/7/submit"));
  expect(JSON.parse(String(submission?.[1]?.body))).toEqual({
    summary: "Added the missing report",
    deliverables: { readme: "docs/readme.md", report: "docs/report.md" },
    artifact_refs: [],
    project_id: null,
  });
  expect(screen.queryByRole("button", { name: "Resubmit deliverables" })).not.toBeInTheDocument();
});
