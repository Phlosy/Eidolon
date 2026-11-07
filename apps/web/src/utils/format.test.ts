import { beforeAll, describe, expect, it } from "vitest";
import i18n from "../i18n";
import { formatBytes, formatMicro, formatUnits, shortId } from "./format";
import { slugify } from "./slug";

describe("money formatting", () => {
  beforeAll(async () => {
    await i18n.changeLanguage("en-US");
  });

  it("shows micro-units as currency units with up to four decimals", () => {
    expect(formatMicro(1_234_567, "CNY")).toBe("1.2346 CNY");
    expect(formatMicro(2_000_000)).toBe("2.00");
    expect(formatMicro(null)).toBe("—");
  });

  it("treats institution budgets as minor units (1 unit = 10 000 micro)", () => {
    expect(formatUnits(200_000, "CNY")).toBe("2,000.00 CNY");
  });

  it("formats bytes and ids", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(2048)).toBe("2.0 KB");
    expect(shortId("0123456789abcdef")).toBe("01234567");
    expect(shortId(null)).toBe("—");
  });

  it("slugifies company names the way the API validates them", () => {
    expect(slugify("Tiny Studio!")).toBe("tiny-studio");
    expect(slugify("  Ünïcode  Co  ")).toBe("n-code-co");
    expect(slugify("a".repeat(80))).toHaveLength(64);
  });
});
