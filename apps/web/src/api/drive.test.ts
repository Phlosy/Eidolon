import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { uploadDriveFile } from "./drive";

/**
 * Drive 走的是"原始 fetch"（Blob / 文件体），历史上它不带 CSRF 头，
 * 结果所有写操作 403 —— 教程 cloud_docs 这一步就是这么撞墙的。
 * 这里把鉴权头钉住，防止再有第二个绕过 client.ts 的请求路径。
 */
describe("drive 原始请求的鉴权头", () => {
  const originalCookie = document.cookie;

  function setCookie(name: string, value: string) {
    document.cookie = `${name}=${value}; path=/`;
  }

  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ id: 1 }), { status: 201 })),
    );
  });

  afterEach(() => {
    setCookie("eidolon_csrf", "");
    document.cookie = originalCookie;
    vi.unstubAllGlobals();
  });

  it("上传会带上 CSRF 头与会话凭证", async () => {
    setCookie("eidolon_csrf", "token-abc");
    const file = new File(["# hello"], "handbook.md", { type: "text/markdown" });
    await uploadDriveFile({ file, zone: "knowledge" });

    const fetchMock = vi.mocked(fetch);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/v1/drive/files?zone=knowledge&name=handbook.md");
    expect(init.credentials).toBe("include");
    expect((init.headers as Record<string, string>)["X-CSRF-Token"]).toBe("token-abc");
  });

  it("没有 csrf cookie 时也不抛错（后端会给出明确的 403）", async () => {
    setCookie("eidolon_csrf", "");
    const file = new File(["x"], "a.md", { type: "text/markdown" });
    await expect(uploadDriveFile({ file, zone: "knowledge" })).resolves.toBeDefined();
  });
});
