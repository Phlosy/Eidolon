import { describe, expect, it } from "vitest";
import { passwordIssues } from "./password-policy";

describe("password policy", () => {
  it.each(["abcdefg1", "1234567!", "abcdefg!"])(
    "accepts at least eight characters drawn from two categories: %s",
    (password) => {
      expect(passwordIssues(password)).toEqual([]);
    },
  );

  it("requires at least eight characters", () => {
    expect(passwordIssues("abc123!")).toContain("length");
  });

  it("counts Unicode code points instead of UTF-16 code units", () => {
    expect(passwordIssues("abcdef😀")).toContain("length");
    expect(passwordIssues("abcdefg😀")).toEqual([]);
  });

  it.each(["abcdefgh", "12345678", "!!!!!!!!", "password with spaces"])(
    "rejects passwords drawn from only one category: %s",
    (password) => {
      expect(passwordIssues(password)).toContain("characterTypes");
    },
  );

  it("reports both unmet requirements", () => {
    expect(passwordIssues("short")).toEqual(["length", "characterTypes"]);
  });

  it("uses the shared Unicode whitespace contract for special characters", () => {
    expect(passwordIssues("abcdefg\u0085")).toContain("characterTypes");
    expect(passwordIssues("abcdefg\uFEFF")).toEqual([]);
  });
});
