import { describe, expect, it } from "vitest";
import { deriveCompanyProgress, deriveProjectProgress } from "./company-metrics";

describe("derived company metrics", () => {
  it("derives stable company level and XP from real aggregate counts", () => {
    expect(
      deriveCompanyProgress({
        employees: 5,
        completedProjects: 3,
        documents: 24,
        completedTasks: 18,
      }),
    ).toEqual({ level: 4, xp: 532, currentLevelXp: 132, nextLevelXp: 200, percent: 66 });
  });

  it("derives project completion from task state without random data", () => {
    expect(
      deriveProjectProgress([
        { status: "done" },
        { status: "done" },
        { status: "in_progress" },
        { status: "todo" },
      ]),
    ).toEqual({ completed: 2, total: 4, percent: 50 });
    expect(deriveProjectProgress([])).toEqual({ completed: 0, total: 0, percent: 0 });
  });
});
