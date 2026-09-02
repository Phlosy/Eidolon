import { describe, expect, it } from "vitest";
import { passwordIssues } from "./password-policy";

describe("password policy", () => {
  it("requires length while allowing password-manager generated values", () => {
    expect(passwordIssues("short")).toContain("length");
    expect(passwordIssues("correct horse battery staple")).toEqual([]);
  });
});
