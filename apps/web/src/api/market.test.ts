import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  createMarketListing,
  delistMarketListing,
  getMarketFit,
  getMarketListings,
  recruitMarketListing,
} from "./market";

/** T2.7 市场客户端 URL/载荷契约：mine / position / recruit 的接线必须显式可查。 */
describe("market API 参数", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({}), { status: 200 })),
    );
  });

  afterEach(() => vi.unstubAllGlobals());

  it("列表支持 mine / position / tier / 搜索组合", async () => {
    await getMarketListings({
      mine: true,
      positionDefinitionId: 4,
      qualityTier: "rare",
      text: "林",
      limit: 60,
    });
    const [url] = vi.mocked(fetch).mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/v1/market/listings?");
    expect(url).toContain("text=%E6%9E%97");
    expect(url).toContain("quality_tier=rare");
    expect(url).toContain("position_definition_id=4");
    expect(url).toContain("mine=true");
    expect(url).toContain("limit=60");
  });

  it("Fit 端点显式带 position_definition_id（可选 profile 版本）", async () => {
    await getMarketFit(3, 4, 2);
    const [url] = vi.mocked(fetch).mock.calls[0] as [string, RequestInit];
    expect(url).toContain(
      "/api/v1/market/listings/3/fit?position_definition_id=4&profile_version_id=2",
    );
  });

  it("挂牌是 POST person_id（不带档位时不出现 quality_tier）", async () => {
    await createMarketListing(70);
    const [url, init] = vi.mocked(fetch).mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/v1/market/listings");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ person_id: 70 });
  });

  it("下架走 DELETE，招募走 POST 且只带显式给出的字段", async () => {
    await delistMarketListing(9);
    const [delUrl, delInit] = vi.mocked(fetch).mock.calls[0] as [string, RequestInit];
    expect(delUrl).toContain("/api/v1/market/listings/9");
    expect(delInit.method).toBe("DELETE");

    await recruitMarketListing(9, { department_id: 2, position_slot_id: 5, title: "工程师" });
    const [postUrl, postInit] = vi.mocked(fetch).mock.calls[1] as [string, RequestInit];
    expect(postUrl).toContain("/api/v1/market/listings/9/recruit");
    expect(postInit.method).toBe("POST");
    expect(JSON.parse(String(postInit.body))).toEqual({
      department_id: 2,
      position_slot_id: 5,
      title: "工程师",
    });
  });
});
