import { describe, expect, it } from "vitest";
import { shouldRemindStep } from "./use-tutorial-engine";

/**
 * 聚光灯交互契约（单个纯函数语义）：
 *   · 未在聚光灯处操作 → 教学卡片点下一步只提醒（shouldRemind=true），不完成；
 *   · 已操作（操作卡自己的下一步也算）→ 放行/自动推进；
 *   · 目标不可见（还没导航到页面）→ 不提醒（那是跳转态）。
 */
describe("shouldRemindStep（engage_to_advance 门禁）", () => {
  it("需要操作且未操作且目标可见 → 提醒", () => {
    expect(shouldRemindStep(true, false, true)).toBe(true);
  });

  it("已操作 → 放行（操作卡片的下一步可覆盖教学卡片的下一步）", () => {
    expect(shouldRemindStep(true, true, true)).toBe(false);
  });

  it("目标不可见 → 放行（交给打开页面/兜底逻辑）", () => {
    expect(shouldRemindStep(true, false, false)).toBe(false);
  });

  it("非 engage 步骤行为不变", () => {
    expect(shouldRemindStep(false, false, true)).toBe(false);
  });
});
