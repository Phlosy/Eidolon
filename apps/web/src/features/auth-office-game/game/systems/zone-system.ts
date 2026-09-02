import type { OfficeEmployeeState } from "../../types/office-state";
import type { GridPoint } from "./navigation-system";

export type OfficeZoneType =
  | "ENTRANCE"
  | "CEO_OFFICE"
  | "ENGINEERING"
  | "MEETING_ROOM"
  | "LOUNGE"
  | "LIBRARY"
  | "SHARED_WORKSPACE";

export type OfficeZone = {
  id: string;
  type: OfficeZoneType;
  bounds: { x: number; y: number; width: number; height: number };
  capacity: number;
  department?: string;
};

export type InteractionPointType =
  | "entrance"
  | "workstation"
  | "meeting-seat"
  | "coffee"
  | "bookshelf"
  | "whiteboard";

export type OfficeInteractionPoint = {
  id: string;
  type: InteractionPointType;
  zoneId: string;
  tile: GridPoint;
  facing: "up" | "down" | "left" | "right";
};

export class OfficeAssignmentService {
  private readonly assignments = new Map<string, OfficeInteractionPoint>();

  constructor(
    private readonly zones: OfficeZone[],
    private readonly points: OfficeInteractionPoint[],
  ) {}

  workstationFor(employee: OfficeEmployeeState): OfficeInteractionPoint | null {
    const existing = this.assignments.get(employee.id);
    if (existing) return existing;

    const role = employee.role.toLowerCase();
    const department = employee.department.toLowerCase();
    let desiredZone: OfficeZoneType = "SHARED_WORKSPACE";
    if (role.includes("ceo") || role.includes("chief") || role.includes("首席")) {
      desiredZone = "CEO_OFFICE";
    } else if (
      department.includes("engineer") ||
      department.includes("engineering") ||
      department.includes("qa") ||
      department.includes("工程")
    ) {
      desiredZone = "ENGINEERING";
    } else if (department.includes("research") || department.includes("研究")) {
      desiredZone = "LIBRARY";
    }

    const zoneIds = new Set(this.zones.filter((zone) => zone.type === desiredZone).map((zone) => zone.id));
    const occupied = new Set([...this.assignments.values()].map((point) => point.id));
    const chosen =
      this.points.find(
        (point) => point.type === "workstation" && zoneIds.has(point.zoneId) && !occupied.has(point.id),
      ) ?? this.points.find((point) => point.type === "workstation" && !occupied.has(point.id));
    if (chosen) this.assignments.set(employee.id, chosen);
    return chosen ?? null;
  }

  pointFor(type: InteractionPointType): OfficeInteractionPoint | null {
    return this.points.find((point) => point.type === type) ?? null;
  }

  release(employeeId: string): void {
    this.assignments.delete(employeeId);
  }
}

