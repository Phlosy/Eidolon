import { describe, expect, it } from "vitest";
import { buildProjectSchedule, taskProgress } from "./project-schedule";
import type { Milestone, Project, Task } from "../types";

const project: Project = {
  id: 1,
  company_id: 1,
  name: "Atlas",
  description: null,
  status: "in_progress",
  goal: null,
  owner_id: 2,
  source_order_text: null,
  planned_start_at: null,
  planned_end_at: null,
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-02T00:00:00Z",
};

const milestone: Milestone = {
  id: 10,
  project_id: 1,
  name: "Build",
  description: null,
  status: "in_progress",
  order: 1,
  owner_id: 3,
  planned_start_at: "2026-09-04T00:00:00Z",
  planned_end_at: "2026-09-10T00:00:00Z",
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-02T00:00:00Z",
};

const task = (id: number, sequence: number): Task => ({
  id,
  project_id: 1,
  milestone_id: 10,
  title: `Task ${id}`,
  description: null,
  kind: "development",
  status: "todo",
  priority: 1,
  assignee_id: 4,
  acceptance_criteria: null,
  sequence,
  dependencies: [],
  planned_start_at: null,
  planned_end_at: null,
  actual_start_at: null,
  actual_end_at: null,
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-02T00:00:00Z",
});

describe("project schedule projection", () => {
  it("preserves explicit milestone dates and divides child work into readable slots", () => {
    const schedule = buildProjectSchedule(project, [milestone], [task(100, 1), task(101, 2)]);

    expect(schedule.milestones.get(10)?.start.toISOString()).toBe("2026-09-04T00:00:00.000Z");
    expect(schedule.milestones.get(10)?.end.toISOString()).toBe("2026-09-10T00:00:00.000Z");
    expect(schedule.tasks.get(100)?.start.toISOString()).toBe("2026-09-04T00:00:00.000Z");
    expect(schedule.tasks.get(101)?.start.getTime()).toBeGreaterThan(
      schedule.tasks.get(100)?.start.getTime() ?? 0,
    );
  });

  it("creates a stable fallback horizon for legacy projects without planned dates", () => {
    const schedule = buildProjectSchedule(project, [], []);
    const duration = schedule.project.end.getTime() - schedule.project.start.getTime();
    expect(duration).toBe(18 * 24 * 60 * 60 * 1000);
  });

  it("maps live task states to a deterministic visual progress", () => {
    expect(taskProgress("backlog")).toBe(0);
    expect(taskProgress("in_progress")).toBe(55);
    expect(taskProgress("in_review")).toBe(82);
    expect(taskProgress("done")).toBe(100);
  });
});
