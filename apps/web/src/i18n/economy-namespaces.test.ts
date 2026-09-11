import { describe, expect, it } from "vitest";
import economyZh from "./locales/zh-CN/economy.json";
import economyEn from "./locales/en-US/economy.json";
import workOrdersZh from "./locales/zh-CN/workOrders.json";
import workOrdersEn from "./locales/en-US/workOrders.json";
import contractsZh from "./locales/zh-CN/contracts.json";
import contractsEn from "./locales/en-US/contracts.json";
import navZh from "./locales/zh-CN/nav.json";
import navEn from "./locales/en-US/nav.json";
import { NAMESPACES } from "./index";

/**
 * M1.9 经济界面的 i18n 契约：中英键集合必须**逐键一致**（缺键会静默回落到另一种语言），
 * 且命名空间已注册。状态/类型这类枚举文案尤其容易漏（新增状态时必须补两种语言）。
 */
function keyPaths(value: unknown, prefix = ""): string[] {
  if (value == null || typeof value !== "object") return [prefix];
  return Object.entries(value as Record<string, unknown>).flatMap(([key, child]) =>
    keyPaths(child, prefix ? `${prefix}.${key}` : key),
  );
}

describe("economy i18n", () => {
  it("注册了三个新命名空间", () => {
    expect(NAMESPACES).toContain("economy");
    expect(NAMESPACES).toContain("workOrders");
    expect(NAMESPACES).toContain("contracts");
  });

  it.each([
    ["economy", economyZh, economyEn],
    ["workOrders", workOrdersZh, workOrdersEn],
    ["contracts", contractsZh, contractsEn],
  ])("%s 中英键集合一致", (_name, zh, en) => {
    expect(keyPaths(en).sort()).toEqual(keyPaths(zh).sort());
  });

  it("导航补充了 economy / workOrders / contracts", () => {
    for (const key of ["economy", "workOrders", "contracts"]) {
      expect(navZh).toHaveProperty(key);
      expect(navEn).toHaveProperty(key);
    }
    expect((navZh as { sections: Record<string, string> }).sections.economy).toBeTruthy();
    expect((navEn as { sections: Record<string, string> }).sections.economy).toBeTruthy();
  });

  it("工作订单覆盖后端全部状态文案（新增状态必须补翻译）", () => {
    const statuses = [
      "OPEN",
      "ACCEPTED",
      "IN_PROGRESS",
      "SUBMITTED",
      "REVIEWING",
      "APPROVED",
      "REJECTED",
      "SETTLED",
      "CANCELLED",
      "EXPIRED",
      "DISPUTED",
    ];
    for (const status of statuses) {
      expect((workOrdersZh as { status: Record<string, string> }).status[status]).toBeTruthy();
      expect((workOrdersEn as { status: Record<string, string> }).status[status]).toBeTruthy();
    }
  });

  it("合同覆盖后端全部状态与类型文案", () => {
    const statuses = [
      "DRAFT",
      "PENDING_ACCEPTANCE",
      "ACTIVE",
      "FUNDED",
      "FULFILLED",
      "SETTLING",
      "SETTLED",
      "CANCELLED",
      "EXPIRED",
      "FAILED",
      "DISPUTED",
    ];
    const types = ["work", "talent", "service", "procurement", "research"];
    for (const status of statuses) {
      expect((contractsEn as { status: Record<string, string> }).status[status]).toBeTruthy();
    }
    for (const type of types) {
      expect((contractsZh as { type: Record<string, string> }).type[type]).toBeTruthy();
      expect((contractsEn as { type: Record<string, string> }).type[type]).toBeTruthy();
    }
  });
});
