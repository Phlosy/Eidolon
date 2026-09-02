import { describe, expect, it } from "vitest";
import { adaptOfficeEmployee, behaviorForStatus, createOfficeSnapshot } from "./office-state-adapter";

describe("office state adapter", () => {
  it("normalizes live employee data without leaking backend shape into Phaser", () => {
    expect(adaptOfficeEmployee({ id: 42, name: "Ada", status: "working" })).toMatchObject({
      id: "42",
      name: "Ada",
      status: "WORKING",
      currentTask: null,
      visualProfile: { skinId: "employee-default", palette: "navy" },
    });
  });

  it("falls back safely for unknown status and preserves snapshot mode", () => {
    const snapshot = createOfficeSnapshot([{ id: "x", name: "Unknown", status: "paused" }], {
      mode: "demo",
      revision: 7,
    });
    expect(snapshot.employees[0].status).toBe("IDLE");
    expect(snapshot).toMatchObject({ mode: "demo", revision: 7 });
  });

  it("maps server statuses to stable behavior states", () => {
    expect(behaviorForStatus("WORKING")).toBe("WORKING");
    expect(behaviorForStatus("RESEARCHING")).toBe("LEARNING");
    expect(behaviorForStatus("OFFLINE")).toBe("OFFLINE");
    expect(behaviorForStatus("ERROR")).toBe("ERROR");
  });
});

