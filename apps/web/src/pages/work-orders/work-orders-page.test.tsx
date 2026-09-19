import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { WorkOrdersPage } from "./work-orders-page";
import type { WorkOrderView } from "../../api/workOrders";

const STATE = vi.hoisted(() => ({
  items: [] as WorkOrderView[],
  accept: vi.fn(),
  submit: vi.fn(),
  pending: false,
  error: null as Error | null,
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
  useSubmitWorkOrder: () => ({
    mutate: STATE.submit,
    isPending: STATE.pending,
    error: STATE.error,
    variables: { orderId: 7 },
  }),
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
    STATE.pending = false;
    STATE.error = null;
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

  it("已领取订单：必交项和摘要都有可见标签，提交携带交付物", async () => {
    STATE.items = [order({ status: "ACCEPTED", is_mine: true, assignee_company_id: 2 })];
    render(<WorkOrdersPage />);

    const textarea = screen.getByRole("textbox", { name: "Delivery summary" });
    fireEvent.change(textarea, { target: { value: "delivered the doc" } });
    fireEvent.change(screen.getByRole("textbox", { name: "readme" }), {
      target: { value: "docs/readme.md" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Submit deliverables" }));
    await waitFor(() => expect(STATE.submit).toHaveBeenCalledTimes(1));
    expect(STATE.submit.mock.calls[0][0]).toEqual({
      orderId: 7,
      input: { summary: "delivered the doc", deliverables: { readme: "docs/readme.md" } },
    });
  });

  it("未通过订单：允许补齐必交项后重新提交", async () => {
    STATE.items = [order({ status: "REJECTED", is_mine: true, assignee_company_id: 2 })];
    render(<WorkOrdersPage />);
    fireEvent.change(screen.getByRole("textbox", { name: "readme" }), {
      target: { value: "corrected readme" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Resubmit deliverables" }));
    await waitFor(() =>
      expect(STATE.submit).toHaveBeenCalledWith({
        orderId: 7,
        input: { summary: "", deliverables: { readme: "corrected readme" } },
      }),
    );
  });

  it("缺少必交项时保留表单且不发起提交", () => {
    STATE.items = [order({ status: "ACCEPTED", is_mine: true })];
    render(<WorkOrdersPage />);
    fireEvent.click(screen.getByRole("button", { name: "Submit deliverables" }));
    expect(screen.getByRole("textbox", { name: "readme" })).toBeInvalid();
    expect(STATE.submit).not.toHaveBeenCalled();
  });

  it("提交中显示状态，错误在订单旁显示且保留输入", () => {
    STATE.items = [order({ status: "ACCEPTED", is_mine: true })];
    const { rerender } = render(<WorkOrdersPage />);
    fireEvent.change(screen.getByRole("textbox", { name: "readme" }), {
      target: { value: "keep this draft" },
    });
    STATE.pending = true;
    rerender(<WorkOrdersPage />);
    expect(screen.getByRole("button", { name: "Submitting…" })).toBeDisabled();
    STATE.pending = false;
    STATE.error = new Error("Submission could not be saved");
    rerender(<WorkOrdersPage />);
    expect(screen.getByRole("alert")).toHaveTextContent("Submission could not be saved");
    expect(screen.getByRole("textbox", { name: "readme" })).toHaveValue("keep this draft");
    expect(screen.getByRole("button", { name: "Submit deliverables" })).not.toBeDisabled();
  });

  it("空市场显示空状态", () => {
    STATE.items = [];
    render(<WorkOrdersPage />);
    expect(screen.getByText(/No open orders|暂无在招订单/)).toBeTruthy();
  });
});
