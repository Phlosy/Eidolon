import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { WorkOrdersPage } from "./work-orders-page";
import type { WorkOrderView } from "../../api/workOrders";

const STATE = vi.hoisted(() => ({
  items: [] as WorkOrderView[],
  accept: vi.fn(),
  submit: vi.fn(),
}));

vi.mock("../../hooks/useWorkOrders", () => ({
  useWorkOrders: () => ({
    data: { items: STATE.items, total: STATE.items.length, limit: 30, offset: 0 },
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
  useAcceptWorkOrder: () => ({ mutate: STATE.accept, isPending: false }),
  useSubmitWorkOrder: () => ({ mutate: STATE.submit, isPending: false }),
}));

function order(overrides: Partial<WorkOrderView> = {}): WorkOrderView {
  return {
    work_order_id: 7,
    code: "OB-00007",
    kind: "OFFICIAL_BOUNTY",
    title: "Write the onboarding doc",
    description: "",
    requirements: {},
    deliverables: { required_keys: ["readme"] },
    reward_amount: 5_000,
    currency: "CREDIT",
    funding_mode: "system_mint",
    evaluation_mode: "auto",
    status: "OPEN",
    deadline_at: null,
    issuer_actor_kind: "system",
    accepted_at: null,
    submitted_at: null,
    settled_at: null,
    assignee_company_id: null,
    issuer_company_id: null,
    is_mine: false,
    is_issuer: false,
    escrow: null,
    submission_count: 0,
    payable_amount: 0,
    policy_version: "econ-1",
    ...overrides,
  };
}

describe("WorkOrdersPage", () => {
  beforeEach(() => {
    STATE.items = [order()];
    STATE.accept.mockReset();
    STATE.submit.mockReset();
  });

  it("在招订单：显示奖励与必交交付物，领取调用 accept", async () => {
    render(<WorkOrdersPage />);
    expect(screen.getByText("Write the onboarding doc")).toBeTruthy();
    expect(screen.getByText(/5,000/)).toBeTruthy();
    expect(screen.getByText(/readme/)).toBeTruthy();

    const buttons = screen.getAllByRole("button") as HTMLButtonElement[];
    const acceptButton = buttons.find((button) => button.textContent === "领取") ?? buttons[1];
    fireEvent.click(acceptButton);
    await waitFor(() => expect(STATE.accept).toHaveBeenCalledWith(7));
  });

  it("已领取订单：可提交交付物，提交带上 summary", async () => {
    STATE.items = [order({ status: "ACCEPTED", is_mine: true, assignee_company_id: 2 })];
    render(<WorkOrdersPage />);

    const textarea = screen.getByRole("textbox");
    fireEvent.change(textarea, { target: { value: "delivered the doc" } });
    const buttons = screen.getAllByRole("button") as HTMLButtonElement[];
    fireEvent.click(buttons[buttons.length - 1]);
    await waitFor(() => expect(STATE.submit).toHaveBeenCalledTimes(1));
    expect(STATE.submit.mock.calls[0][0]).toEqual({
      orderId: 7,
      input: { summary: "delivered the doc" },
    });
  });

  it("空市场显示空状态", () => {
    STATE.items = [];
    render(<WorkOrdersPage />);
    expect(screen.getByText(/No open orders|暂无在招订单/)).toBeTruthy();
  });
});
