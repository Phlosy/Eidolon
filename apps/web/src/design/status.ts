import type { EmployeeStatus } from "../types";

export const employeeStatusTone: Record<EmployeeStatus, string> = {
  working: "status-working", researching: "status-researching", learning: "status-learning",
  reflecting: "status-reflecting", meeting: "status-meeting", idle: "status-idle",
  error: "status-error", offline: "status-offline",
};

export const activeEmployeeStatuses = new Set<EmployeeStatus>(["working", "researching", "learning", "reflecting", "meeting"]);
