import { describe, expect, it } from "vitest";
import {
  POSITION_PROFILE_STATUSES,
  POSITION_REQUIREMENT_TYPES,
  type PositionRequirementType,
} from "./index";

/**
 * P7 契约：后端 `REQUIREMENT_TYPES` / `PROFILE_STATUS_*`（app/services/position_profile.py）
 * 与前端运行时常量逐字一致；类型负例由 @ts-expect-error 在 `tsc --noEmit` 时兜底。
 */
describe("Position profile enum contract", () => {
  it("requirement types mirror the backend", () => {
    expect([...POSITION_REQUIREMENT_TYPES]).toEqual(["required", "preferred"]);
  });

  it("profile version statuses mirror the backend", () => {
    expect([...POSITION_PROFILE_STATUSES]).toEqual(["draft", "active", "retired"]);
  });

  it("rejects a non-existent requirement type at compile time", () => {
    const value: PositionRequirementType = "required";
    expect(value).toBe("required");
    // @ts-expect-error — 后端没有 "optional_strong" 这一层；放开 union 会让 tsc 因 unused 指令变红
    const bad: PositionRequirementType = "optional_strong";
    expect(bad).toBeDefined();
  });
});
