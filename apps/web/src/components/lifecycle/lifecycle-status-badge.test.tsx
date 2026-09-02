import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { LifecycleStatusBadge } from "./lifecycle-status-badge";
import { availableActions } from "./action-availability";
import { LIFECYCLE_STATUS_SOURCE } from "../../utils/status";
import type { LifecycleStatus } from "../../types";

const ALL: LifecycleStatus[] = [
  "pending",
  "onboarding",
  "active",
  "transferring",
  "suspended",
  "offboarding",
  "offboarded",
];

describe("LifecycleStatusBadge", () => {
  it("maps every lifecycle status onto a status-token chip", () => {
    // Token mapping (v0.4 spec): active=emerald, onboarding=violet,
    // suspended=amber, offboarded=dim, etc.
    expect(LIFECYCLE_STATUS_SOURCE.active).toBe("working");
    expect(LIFECYCLE_STATUS_SOURCE.onboarding).toBe("learning");
    expect(LIFECYCLE_STATUS_SOURCE.suspended).toBe("reflecting");
    expect(LIFECYCLE_STATUS_SOURCE.offboarded).toBe("offline");
    for (const status of ALL) {
      expect(LIFECYCLE_STATUS_SOURCE[status]).toBeTruthy();
    }
  });

  it("renders the localized label for each status", () => {
    const { unmount } = render(<LifecycleStatusBadge status="active" />);
    expect(screen.getByTestId("lifecycle-badge")).toHaveAttribute("data-status", "active");
    expect(screen.getByTestId("lifecycle-badge").className).toContain("text-status-working");
    unmount();

    render(<LifecycleStatusBadge status="offboarded" />);
    expect(screen.getByTestId("lifecycle-badge").className).toContain("text-status-offline");
  });
});

describe("availableActions", () => {
  it("offers transfer/suspend/offboard while active", () => {
    expect(availableActions("active")).toEqual(["transfer", "suspend", "offboard"]);
  });

  it("offers resume/offboard while suspended", () => {
    expect(availableActions("suspended")).toEqual(["resume", "offboard"]);
  });

  it("offers nothing while a job is running or after offboarding", () => {
    for (const status of [
      "pending",
      "onboarding",
      "transferring",
      "offboarding",
      "offboarded",
    ] as const) {
      expect(availableActions(status)).toEqual([]);
    }
  });
});
