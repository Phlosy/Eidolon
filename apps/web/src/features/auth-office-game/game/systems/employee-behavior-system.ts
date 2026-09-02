import type { OfficeBehaviorState, OfficeEmployeeState } from "../../types/office-state";
import { behaviorForStatus } from "../../state/office-state-adapter";
import type { InteractionPointType } from "./zone-system";

export type BehaviorDirective = {
  state: OfficeBehaviorState;
  changed: boolean;
  targetType: InteractionPointType | "current";
  animation: "idle" | "walk" | "work" | "meeting";
};

const directiveForState: Record<OfficeBehaviorState, Omit<BehaviorDirective, "state" | "changed">> = {
  SPAWN: { targetType: "entrance", animation: "walk" },
  IDLE: { targetType: "coffee", animation: "idle" },
  MOVING: { targetType: "current", animation: "walk" },
  WORKING: { targetType: "workstation", animation: "work" },
  MEETING: { targetType: "meeting-seat", animation: "meeting" },
  LEARNING: { targetType: "bookshelf", animation: "work" },
  INTERACTING: { targetType: "whiteboard", animation: "idle" },
  LEAVING: { targetType: "entrance", animation: "walk" },
  OFFLINE: { targetType: "entrance", animation: "idle" },
  ERROR: { targetType: "current", animation: "idle" },
};

export class EmployeeBehaviorSystem {
  private readonly currentState = new Map<string, OfficeBehaviorState>();

  update(employee: OfficeEmployeeState): BehaviorDirective {
    const state = behaviorForStatus(employee.status);
    const changed = this.currentState.get(employee.id) !== state;
    if (changed) this.currentState.set(employee.id, state);
    return { state, changed, ...directiveForState[state] };
  }

  remove(employeeId: string): void {
    this.currentState.delete(employeeId);
  }
}

