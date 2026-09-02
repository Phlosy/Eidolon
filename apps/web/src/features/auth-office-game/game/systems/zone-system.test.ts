import { describe, expect, it } from "vitest";
import type { OfficeEmployeeState } from "../../types/office-state";
import {
  OfficeAssignmentService,
  type OfficeInteractionPoint,
  type OfficeZone,
} from "./zone-system";

const zones: OfficeZone[] = [
  { id: "ceo", type: "CEO_OFFICE", bounds: { x: 0, y: 0, width: 4, height: 4 }, capacity: 1 },
  {
    id: "engineering",
    type: "ENGINEERING",
    bounds: { x: 4, y: 0, width: 8, height: 4 },
    capacity: 4,
  },
  { id: "lounge", type: "LOUNGE", bounds: { x: 0, y: 4, width: 4, height: 4 }, capacity: 3 },
];
const points: OfficeInteractionPoint[] = [
  { id: "ceo-desk", type: "workstation", zoneId: "ceo", tile: { x: 2, y: 2 }, facing: "up" },
  {
    id: "eng-desk",
    type: "workstation",
    zoneId: "engineering",
    tile: { x: 6, y: 2 },
    facing: "up",
  },
  { id: "coffee", type: "coffee", zoneId: "lounge", tile: { x: 2, y: 6 }, facing: "left" },
];

function employee(overrides: Partial<OfficeEmployeeState>): OfficeEmployeeState {
  return {
    id: "employee",
    name: "Employee",
    role: "Engineer",
    department: "Engineering",
    status: "WORKING",
    currentTask: null,
    runtime: null,
    meetingId: null,
    quote: "Hello",
    visualProfile: { skinId: "employee-0", palette: "navy" },
    ...overrides,
  };
}

describe("OfficeAssignmentService", () => {
  it("assigns workstations by role and department rather than employee name", () => {
    const service = new OfficeAssignmentService(zones, points);
    expect(service.workstationFor(employee({ id: "a", role: "CEO" }))?.id).toBe("ceo-desk");
    expect(service.workstationFor(employee({ id: "b", department: "Engineering" }))?.id).toBe(
      "eng-desk",
    );
  });

  it("keeps a claimed workstation stable", () => {
    const service = new OfficeAssignmentService(zones, points);
    const member = employee({ id: "stable" });
    expect(service.workstationFor(member)).toBe(service.workstationFor(member));
  });
});
