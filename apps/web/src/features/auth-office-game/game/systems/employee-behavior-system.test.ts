import { describe, expect, it } from "vitest";
import type { OfficeEmployeeState } from "../../types/office-state";
import { EmployeeBehaviorSystem } from "./employee-behavior-system";

const base: OfficeEmployeeState = {
  id: "alice",
  name: "Alice",
  role: "CEO",
  department: "Leadership",
  status: "WORKING",
  currentTask: "Review roadmap",
  runtime: "Eidolon",
  meetingId: null,
  quote: "Reviewing.",
  visualProfile: { skinId: "employee-0", palette: "navy" },
};

describe("EmployeeBehaviorSystem", () => {
  it("transitions only when the server-driven behavior changes", () => {
    const system = new EmployeeBehaviorSystem();
    const first = system.update(base);
    const unchanged = system.update({ ...base, currentTask: "Review hiring" });
    const meeting = system.update({ ...base, status: "MEETING", meetingId: "review" });

    expect(first).toMatchObject({ state: "WORKING", changed: true, targetType: "workstation" });
    expect(unchanged).toMatchObject({ state: "WORKING", changed: false });
    expect(meeting).toMatchObject({ state: "MEETING", changed: true, targetType: "meeting-seat" });
  });

  it("sends offline employees toward the entrance and retains error position", () => {
    const system = new EmployeeBehaviorSystem();
    expect(system.update({ ...base, status: "OFFLINE" })).toMatchObject({
      state: "OFFLINE",
      targetType: "entrance",
    });
    expect(system.update({ ...base, status: "ERROR" })).toMatchObject({
      state: "ERROR",
      targetType: "current",
    });
  });
});

