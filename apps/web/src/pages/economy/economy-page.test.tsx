import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { EconomyPage } from "./economy-page";
import type { EconomyOverview, PersonalWallet, RewardOptionView } from "../../api/economy";

const STATE = vi.hoisted(() => ({
  overview: null as EconomyOverview | null,
  wallet: null as PersonalWallet | null,
  rewards: [] as RewardOptionView[],
  claim: vi.fn(),
}));

vi.mock("../../hooks/useEconomy", () => ({
  useEconomyOverview: () => ({
    data: STATE.overview,
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
  useMyWallet: () => ({ data: STATE.wallet, isLoading: false, isError: false, error: null }),
  useCompanyTransactions: () => ({
    data: {
      items: [
        {
          transaction_id: 1,
          transaction_type: "transfer",
          currency: "CREDIT",
          status: "posted",
          reference_type: "work_order",
          reference_id: "7",
          reason: "work_order",
          amount: 4000,
          occurred_at: "2026-09-11T00:00:00Z",
          posted_at: "2026-09-11T00:00:00Z",
          entries: [],
        },
      ],
      total: 1,
      limit: 15,
      offset: 0,
    },
    isLoading: false,
    isError: false,
    error: null,
  }),
  useRewards: () => ({ data: { items: STATE.rewards }, isLoading: false, isError: false }),
  useClaimReward: () => ({ mutate: STATE.claim, isPending: false, isError: false }),
}));

function overviewFixture(): EconomyOverview {
  return {
    actor_kind: "company",
    actor_ref: 2,
    currency: "CREDIT",
    posted_balance: 100_000,
    available_balance: 92_000,
    reserved_balance: 8_000,
    income_total: 120_000,
    expense_total: 28_000,
    net_total: 92_000,
    by_category: [
      { category: "STARTER", label: "启动资金", income: 100_000, expense: 0, net: 100_000 },
      { category: "COMPUTE", label: "算力成本", income: 0, expense: 4, net: -4 },
    ],
    compute_paid: 4,
    compute_unpaid: 0,
  };
}

function walletFixture(): PersonalWallet {
  return {
    actor_kind: "user",
    actor_ref: 1,
    currency: "CREDIT",
    posted_balance: 600,
    available_balance: 600,
    reserved_balance: 0,
    accounts: [],
    transactions: { items: [], total: 0, limit: 10, offset: 0 },
  };
}

describe("EconomyPage", () => {
  beforeEach(() => {
    STATE.overview = overviewFixture();
    STATE.wallet = walletFixture();
    STATE.rewards = [
      {
        reward_type: "DAILY_LOGIN",
        label: "每日登录",
        actor_kind: "user",
        actor_ref: 1,
        amount: 100,
        currency: "CREDIT",
        reference_key: "daily:2026-09-11",
        claimable: true,
        reason: "ok",
        policy_version: "econ-1",
        next_eligible_at: null,
        claimed_at: null,
        grant_id: null,
        metadata: {},
      },
    ];
    STATE.claim.mockReset();
  });

  it("展示余额/收支/净额与分类明细（数字原样来自后端）", () => {
    render(<EconomyPage />);
    // 92,000 同时出现在"可花余额"和"净额"（两者相等）→ 用 getAllByText
    expect(screen.getAllByText("92,000").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("120,000")).toBeTruthy();
    expect(screen.getByText("28,000")).toBeTruthy();
    expect(screen.getByText("启动资金")).toBeTruthy();
    expect(screen.getByText("算力成本")).toBeTruthy();
    // 分类明细里的支出会显示 4（算力成本）；净额列有负数行
    expect(screen.getByText("-4")).toBeTruthy();
  });

  it("奖励可领取时点按钮会调用领取（并带上 reference_key）", async () => {
    render(<EconomyPage />);
    // 语言无关：测试环境的默认语言由浏览器检测决定 → 按"可点击的按钮"定位
    const button = screen
      .getAllByRole("button")
      .find((element) => !(element as HTMLButtonElement).disabled)!;
    fireEvent.click(button);
    await waitFor(() => expect(STATE.claim).toHaveBeenCalledTimes(1));
    expect(STATE.claim.mock.calls[0][0]).toEqual({
      rewardType: "DAILY_LOGIN",
      referenceKey: "daily:2026-09-11",
    });
  });

  it("不可领取的奖励按钮禁用（不误导玩家）", () => {
    STATE.rewards = [{ ...STATE.rewards[0], claimable: false, reason: "already_claimed" }];
    render(<EconomyPage />);
    const buttons = screen.getAllByRole("button") as HTMLButtonElement[];
    expect(buttons).toHaveLength(1);
    expect(buttons[0].disabled).toBe(true);
  });

  it("流水表展示业务锚点与金额", () => {
    render(<EconomyPage />);
    expect(screen.getByText("work_order:7")).toBeTruthy();
    expect(screen.getByText("4,000")).toBeTruthy();
  });
});
