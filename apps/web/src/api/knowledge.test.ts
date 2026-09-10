import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  listKnowledgeItems,
  proposeKnowledgePromotion,
  reviewKnowledgeProposal,
} from "./knowledge";

describe("knowledge api", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json([], { status: 200 })),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("无参数时请求 /knowledge，不带查询串", async () => {
    await listKnowledgeItems();
    const [url] = vi.mocked(fetch).mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/v1/knowledge");
    expect(url).not.toContain("?");
  });

  it("scope/topic/employeeId 映射为后端查询参数", async () => {
    await listKnowledgeItems({ scope: "department", topic: " onboarding ", employeeId: 7 });
    const [url] = vi.mocked(fetch).mock.calls[0] as [string, RequestInit];
    expect(url).toContain("scope=department");
    expect(url).toContain("topic=onboarding");
    expect(url).toContain("employee_id=7");
  });

  it("发起晋升提案时 POST target_scope", async () => {
    await proposeKnowledgePromotion(3, "company");
    const [url, init] = vi.mocked(fetch).mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/v1/knowledge/3/proposals");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ target_scope: "company" });
  });

  it("审批时 POST approve 布尔值", async () => {
    await reviewKnowledgeProposal(3, false);
    const [url, init] = vi.mocked(fetch).mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/v1/knowledge/3/review");
    expect(JSON.parse(String(init.body))).toEqual({ approve: false });
  });
});
