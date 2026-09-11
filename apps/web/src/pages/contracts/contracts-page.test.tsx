import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ContractsPage } from "./contracts-page";
import type { ContractView } from "../../api/contracts";

const STATE = vi.hoisted(() => ({
  items: [] as ContractView[],
  accept: vi.fn(),
  fulfill: vi.fn(),
  cancel: vi.fn(),
}));

vi.mock("../../hooks/useContracts", () => ({
  useContracts: () => ({
    data: { items: STATE.items, total: STATE.items.length, limit: 30, offset: 0 },
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
  useAcceptContract: () => ({ mutate: STATE.accept, isPending: false }),
  useFulfillContract: () => ({ mutate: STATE.fulfill, isPending: false }),
  useCancelContract: () => ({ mutate: STATE.cancel, isPending: false }),
}));

function contract(overrides: Partial<ContractView> = {}): ContractView {
  return {
    contract_id: 5,
    code: "CW-00005",
    contract_type: "work",
    title: "Build the module",
    subject: "",
    terms: {},
    consideration_amount: 10_000,
    currency: "CREDIT",
    status: "PENDING_ACCEPTANCE",
    issuer_company_id: 2,
    contractor_company_id: 3,
    reference_type: "",
    reference_id: "",
    effective_at: null,
    expires_at: null,
    fulfilled_at: null,
    settled_at: null,
    settlement_transaction_id: null,
    policy_version: "econ-1",
    is_issuer: false,
    is_contractor: true,
    escrow: {
      escrow_id: 9,
      status: "FUNDED",
      amount: 10_000,
      currency: "CREDIT",
      account_balance: 10_000,
      payee_company_id: null,
    },
    settlement: {},
    ...overrides,
  };
}

describe("ContractsPage", () => {
  beforeEach(() => {
    STATE.items = [contract()];
    STATE.accept.mockReset();
    STATE.fulfill.mockReset();
    STATE.cancel.mockReset();
  });

  it("承接方看到接受按钮并调用 accept", async () => {
    render(<ContractsPage />);
    expect(screen.getByText("Build the module")).toBeTruthy();
    expect(screen.getByText(/10,000/)).toBeTruthy();
    const buttons = screen.getAllByRole("button") as HTMLButtonElement[];
    const acceptButton = buttons.find((button) => button.textContent === "接受") ?? buttons[1];
    fireEvent.click(acceptButton);
    await waitFor(() => expect(STATE.accept).toHaveBeenCalledWith(5));
  });

  it("资金已就位时显示交付并结算（多腿结算明细展开可见）", async () => {
    STATE.items = [
      contract({
        status: "FUNDED",
        settlement: {},
      }),
    ];
    render(<ContractsPage />);
    const buttons = screen.getAllByRole("button") as HTMLButtonElement[];
    const fulfillButton =
      buttons.find((button) => button.textContent === "交付并结算") ?? buttons[1];
    fireEvent.click(fulfillButton);
    await waitFor(() => expect(STATE.fulfill).toHaveBeenCalledWith(5));
  });

  it("已结算合同展开显示结算拆分（对价/手续费/净额/财政与销毁）", async () => {
    STATE.items = [
      contract({
        status: "SETTLED",
        settlement_transaction_id: 20,
        settlement: { gross: 10_000, fee: 300, net: 9_700, treasury: 180, burn: 120 },
      }),
    ];
    render(<ContractsPage />);
    fireEvent.click(screen.getByRole("button", { name: "Build the module" }));
    expect(await screen.findByText("9,700")).toBeTruthy();
    expect(screen.getByText("300")).toBeTruthy();
    expect(screen.getByText("180 / 120")).toBeTruthy();
  });

  it("空列表显示空状态", () => {
    STATE.items = [];
    render(<ContractsPage />);
    expect(screen.getByText(/No contracts yet|暂无合同/)).toBeTruthy();
  });
});
