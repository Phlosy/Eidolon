import { describe, expect, it } from "vitest";
import {
  canAdvanceHire,
  defaultPackageIds,
  deriveSlug,
  INITIAL_HIRE_STATE,
  type HireWizardState,
} from "./hire-wizard-steps";
import type { AccessPackage } from "../../types";

const READY: HireWizardState = {
  ...INITIAL_HIRE_STATE,
  name: "Ada",
  title: "Engineer",
  departmentId: 1,
  runtimeType: "mock",
};

function makePackage(id: number, slug: string, builtIn = true): AccessPackage {
  return {
    id,
    slug,
    name: slug,
    description: null,
    built_in: builtIn,
    entitlements: [],
  };
}

describe("canAdvanceHire", () => {
  it("requires a name and title on the identity step", () => {
    expect(canAdvanceHire("identity", INITIAL_HIRE_STATE)).toBe(false);
    expect(canAdvanceHire("identity", { ...INITIAL_HIRE_STATE, name: "Ada" })).toBe(false);
    expect(canAdvanceHire("identity", { ...READY })).toBe(true);
  });

  it("requires a department on the department step", () => {
    expect(canAdvanceHire("department", { ...READY, departmentId: null })).toBe(false);
    expect(canAdvanceHire("department", READY)).toBe(true);
  });

  it("treats manager and packages as optional", () => {
    expect(canAdvanceHire("manager", READY)).toBe(true);
    expect(canAdvanceHire("packages", { ...READY, packageIds: [] })).toBe(true);
  });

  it("requires a runtime type", () => {
    expect(canAdvanceHire("runtime", { ...READY, runtimeType: null })).toBe(false);
    expect(canAdvanceHire("runtime", READY)).toBe(true);
  });

  it("requires a provider and model only when provider configuration is selected", () => {
    expect(canAdvanceHire("provider", READY)).toBe(true);
    // 真实运行时不能“稍后配置”：没有 provider+model 容器起不来
    expect(canAdvanceHire("provider", { ...READY, runtimeType: "hermes" })).toBe(false);
    expect(
      canAdvanceHire("provider", { ...READY, providerMode: "existing", providerId: null }),
    ).toBe(false);
    expect(
      canAdvanceHire("provider", {
        ...READY,
        providerMode: "existing",
        providerId: 7,
        model: "gpt-5.2",
      }),
    ).toBe(true);
    expect(
      canAdvanceHire("provider", {
        ...READY,
        providerMode: "new",
        providerName: "Ada OpenAI",
        model: "",
      }),
    ).toBe(false);
  });

  it("requires a persisted personality on the brain step", () => {
    expect(canAdvanceHire("brain", READY)).toBe(true);
    expect(canAdvanceHire("brain", { ...READY, personality: "  " })).toBe(false);
  });

  it("never blocks on the preview step (unavailable steps only warn)", () => {
    expect(canAdvanceHire("preview", READY)).toBe(true);
  });

  it("cannot finish on the hire step without a name and department", () => {
    expect(canAdvanceHire("hire", READY)).toBe(true);
    expect(canAdvanceHire("hire", { ...READY, name: "  " })).toBe(false);
    expect(canAdvanceHire("hire", { ...READY, departmentId: null })).toBe(false);
  });
});

describe("deriveSlug", () => {
  it("slugifies ASCII names", () => {
    expect(deriveSlug("Ada Lovelace")).toBe("ada-lovelace");
    expect(deriveSlug("  Grace  Hopper ")).toBe("grace-hopper");
  });

  it("yields an empty string for CJK names (user types a slug)", () => {
    expect(deriveSlug("王小明")).toBe("");
  });
});

describe("defaultPackageIds", () => {
  const packages = [
    makePackage(1, "base-employee"),
    makePackage(2, "engineer"),
    makePackage(3, "qa-engineer"),
    makePackage(4, "custom", false),
  ];

  it("pre-checks base-employee plus the role package", () => {
    expect(defaultPackageIds(packages, "engineer")).toEqual([1, 2]);
    expect(defaultPackageIds(packages, "qa_engineer")).toEqual([1, 3]);
  });

  it("falls back to base-employee when the role has no package", () => {
    expect(defaultPackageIds(packages, "ceo")).toEqual([1]);
  });
});
