import { describe, expect, it } from "vitest";
import { CANDIDATE_BANDS, type CandidateBand } from "./index";

describe("Candidate band contract", () => {
  it("mirrors the backend band set and order", () => {
    expect([...CANDIDATE_BANDS]).toEqual([
      "RECOMMENDED",
      "VIABLE",
      "DEVELOPMENTAL",
      "NEEDS_EVIDENCE",
      "CRITICAL_GAP",
    ]);
  });
  it("rejects an unknown band at compile time", () => {
    const band: CandidateBand = "NEEDS_EVIDENCE";
    expect(band).toBe("NEEDS_EVIDENCE");
    // @ts-expect-error — 后端没有 "TOP_PICK"; 放开会因 unused directive 变红
    const bad: CandidateBand = "TOP_PICK";
    expect(bad).toBeDefined();
  });
});
