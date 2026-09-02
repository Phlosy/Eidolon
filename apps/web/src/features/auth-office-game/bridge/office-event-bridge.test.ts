import { describe, expect, it, vi } from "vitest";
import { OfficeEventBridge } from "./office-event-bridge";

describe("OfficeEventBridge", () => {
  it("delivers typed events and releases subscriptions", () => {
    const bridge = new OfficeEventBridge();
    const listener = vi.fn();
    const unsubscribe = bridge.on("office.employee.focus", listener);

    bridge.emit("office.employee.focus", { employeeId: "employee-1" });
    expect(listener).toHaveBeenCalledWith({ employeeId: "employee-1" });
    expect(bridge.listenerCount("office.employee.focus")).toBe(1);

    unsubscribe();
    expect(bridge.listenerCount("office.employee.focus")).toBe(0);
  });
});

