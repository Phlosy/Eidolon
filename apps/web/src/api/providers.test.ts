import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { listProviderModels, testProvider } from "./providers";

/**
 * 员工级 provider 账号（scope=employee）是**仅属主可见**的：后端
 * get_provider_visible 在没带 employee_id 时按 company-scope 过滤，直接
 * 404 "provider not found"。模型探测/连接测试因此会静默失败 —— 用户看到的是
 * "选了已配好的 DeepSeek 却探测不出模型"。这里把作用域参数钉死。
 */
describe("provider 作用域查询参数", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ models: ["deepseek-chat"], source: "api" }), {
            status: 200,
          }),
      ),
    );
  });

  afterEach(() => vi.unstubAllGlobals());

  it("探测员工级账号的模型时带上 employee_id", async () => {
    await listProviderModels(2, 7);
    const [url] = vi.mocked(fetch).mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/v1/providers/2/models?employee_id=7");
  });

  it("公司级账号（无属主）不附加 employee_id", async () => {
    await listProviderModels(2);
    const [url] = vi.mocked(fetch).mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/v1/providers/2/models");
    expect(url).not.toContain("employee_id");
  });

  it("测试员工级账号连接时带上 employee_id", async () => {
    await testProvider(2, 7);
    const [url, init] = vi.mocked(fetch).mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/v1/providers/2/test?employee_id=7");
    expect(init.method).toBe("POST");
  });
});
