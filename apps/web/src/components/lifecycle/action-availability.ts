import type { LifecycleStatus } from "../../types";

export type LifecycleAction = "transfer" | "suspend" | "resume" | "offboard";

/** Which lifecycle actions are available for a given lifecycle status. Pure; unit-tested. */
export function availableActions(status: LifecycleStatus): LifecycleAction[] {
  switch (status) {
    case "active":
      return ["transfer", "suspend", "offboard"];
    case "suspended":
      return ["resume", "offboard"];
    default:
      // pending/onboarding/transferring/offboarding: a job is running.
      // offboarded: terminal.
      return [];
  }
}
