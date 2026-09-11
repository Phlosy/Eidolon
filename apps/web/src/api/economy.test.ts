import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  claimReward,
  getCompanyTransactions,
  getComputeUsage,
  getEconomyOverview,
  getMyWallet,
  getRewards,
} from "./economy";
import { acceptWorkOrder, getWorkOrders, submitWorkOrder } from "./workOrders";
import { acceptContract, cancelContract, fulfillContract, getContracts } from "./contracts";

/** M1 经济客户端 URL/载荷契约：读面参数与业务动作必须显式可查。 */
describe("economy API 参数", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({}), { status: 200 })),
    );
  });

  afterEach(() => vi.unstubAllGlobals());

  it("公司报表 / 我的钱包 / 算力用量走各自端点", async () => {
    await getEconomyOverview();
    await getMyWallet(5);
    await getComputeUsage(10, 20);
    const calls = vi.mocked(fetch).mock.calls.map((call) => call[0] as string);
    expect(calls[0]).toContain("/api/v1/economy/overview");
    expect(calls[1]).toContain("/api/v1/economy/wallet/me?limit=5");
    expect(calls[2]).toContain("/api/v1/economy/compute-usage?limit=10&offset=20");
  });

  it("流水支持分页与业务锚点过滤", async () => {
    await getCompanyTransactions({
      limit: 25,
      offset: 50,
      transactionType: "transfer",
      referenceType: "work_order",
      referenceId: "7",
    });
    const [url] = vi.mocked(fetch).mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/v1/economy/transactions?");
    expect(url).toContain("limit=25");
    expect(url).toContain("offset=50");
    expect(url).toContain("transaction_type=transfer");
    expect(url).toContain("reference_type=work_order");
    expect(url).toContain("reference_id=7");
  });

  it("领奖 POST 携带 reference_key（成就/教程要指定具体来源）", async () => {
    await getRewards();
    await claimReward("ACHIEVEMENT", "first_project");
    const [url, init] = vi.mocked(fetch).mock.calls[1] as [string, RequestInit];
    expect(url).toContain("/api/v1/economy/rewards/ACHIEVEMENT/claim");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ reference_key: "first_project" });
  });

  it("工作订单：列表 mine 过滤 + 领取/提交端点", async () => {
    await getWorkOrders({ mine: true, limit: 30 });
    await acceptWorkOrder(3);
    await submitWorkOrder(3, { summary: "done", deliverables: { readme: "x" } });
    const calls = vi.mocked(fetch).mock.calls;
    expect(calls[0][0] as string).toContain("/api/v1/work-orders?");
    expect(calls[0][0] as string).toContain("mine=true");
    expect(calls[1][0] as string).toContain("/api/v1/work-orders/3/accept");
    expect(calls[2][0] as string).toContain("/api/v1/work-orders/3/submit");
    expect(JSON.parse(String((calls[2][1] as RequestInit).body))).toEqual({
      summary: "done",
      deliverables: { readme: "x" },
      artifact_refs: [],
      project_id: null,
    });
  });

  it("合同：列表过滤 + 接受/交付/取消三个业务动作", async () => {
    await getContracts({ status: "FUNDED", contractType: "work" });
    await acceptContract(5);
    await fulfillContract(5);
    await cancelContract(6);
    const calls = vi.mocked(fetch).mock.calls;
    expect(calls[0][0] as string).toContain("/api/v1/contracts?");
    expect(calls[0][0] as string).toContain("status=FUNDED");
    expect(calls[0][0] as string).toContain("contract_type=work");
    expect(calls[1][0] as string).toContain("/api/v1/contracts/5/accept");
    expect(calls[2][0] as string).toContain("/api/v1/contracts/5/fulfill");
    expect(calls[3][0] as string).toContain("/api/v1/contracts/6/cancel");
  });
});
