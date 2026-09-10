import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getPersonEvidence, getPersonProfile, getPersonTimeline } from "./persons";

/**
 * T2.1 人员读面的 URL 契约：include / 分页 / 过滤参数必须显式带上 ——
 * 漏 include 会让"未请求"与"为空"无法区分；漏过滤会把跨维度证据混进下钻列表。
 */
describe("persons API 查询参数", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({}), { status: 200 })),
    );
  });

  afterEach(() => vi.unstubAllGlobals());

  it("profile 缺省不请求大集合（timeline/evidence 为 null）", async () => {
    await getPersonProfile(11);
    const [url] = vi.mocked(fetch).mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/v1/persons/11");
    expect(url).not.toContain("include=");
  });

  it("profile 按 include 请求大集合并带上限", async () => {
    await getPersonProfile(11, {
      include: ["timeline", "evidence"],
      timelineLimit: 10,
      evidenceLimit: 5,
    });
    const [url] = vi.mocked(fetch).mock.calls[0] as [string, RequestInit];
    expect(url).toContain("include=timeline%2Cevidence");
    expect(url).toContain("timeline_limit=10");
    expect(url).toContain("evidence_limit=5");
  });

  it("timeline 支持分页", async () => {
    await getPersonTimeline(11, { limit: 20, offset: 40 });
    const [url] = vi.mocked(fetch).mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/v1/persons/11/timeline?limit=20&offset=40");
  });

  it("evidence 支持来源与能力过滤", async () => {
    await getPersonEvidence(11, {
      sourceType: "edu_course",
      competency: "analysis_problem_solving",
    });
    const [url] = vi.mocked(fetch).mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/v1/persons/11/evidence?");
    expect(url).toContain("source_type=edu_course");
    expect(url).toContain("competency=analysis_problem_solving");
  });
});
