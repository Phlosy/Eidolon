import { describe, expect, it } from "vitest";
import { ACCESS_SOURCE_LAYERS, type EntitlementSource } from "./index";

/**
 * P4d 契约：后端 `EntitlementSourceOut.layer`（apps/server/app/schemas/lifecycle.py）
 * 与前端 `AccessSourceLayer` 必须逐字一致。这里三层锁死：
 *
 * 1. 运行时常量断言：改掉 "person" / "position" 其中之一，这个测试红；
 * 2. 类型级负例：`layer: "staff"` 必须编译失败 —— @ts-expect-error 一旦失效，
 *    `tsc --noEmit`（CI lint 门禁）会报 "Unused '@ts-expect-error' directive"；
 * 3. 后端侧对等测试见 apps/server/tests/test_access_layer_contract.py（读 OpenAPI）。
 */
describe("AccessSourceLayer contract", () => {
  it("ACCESS_SOURCE_LAYERS mirrors the backend layer enum exactly", () => {
    expect([...ACCESS_SOURCE_LAYERS]).toEqual(["person", "position"]);
  });

  it("union type derives from the constant (single source of truth)", () => {
    const layer: (typeof ACCESS_SOURCE_LAYERS)[number] = "person";
    expect(layer).toBe("person");
  });

  it("rejects a non-existent backend layer at compile time", () => {
    const sources: EntitlementSource[] = [
      { package_id: 1, package_name: "Base Employee", layer: "person" },
      // @ts-expect-error — 后端没有 "staff" 这一层；若有人放开 union，tsc 会因 unused 指令变红
      { package_id: 2, package_name: "Engineering", layer: "staff" },
    ];
    expect(sources).toHaveLength(2);
    // 正例可用：position 是合法层（职位级权限，P4d 引入）。
    const viaPosition: EntitlementSource = {
      package_id: 3,
      package_name: "Engineering Team",
      layer: "position",
    };
    expect(viaPosition.layer).toBe("position");
  });
});
