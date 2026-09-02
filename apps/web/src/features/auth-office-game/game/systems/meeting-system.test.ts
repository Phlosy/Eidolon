import { describe, expect, it } from "vitest";
import { MeetingSystem } from "./meeting-system";
import type { OfficeInteractionPoint } from "./zone-system";

const seats: OfficeInteractionPoint[] = [
  { id: "seat-1", type: "meeting-seat", zoneId: "meeting", tile: { x: 1, y: 1 }, facing: "down" },
  { id: "seat-2", type: "meeting-seat", zoneId: "meeting", tile: { x: 2, y: 1 }, facing: "down" },
];

describe("MeetingSystem", () => {
  it("allocates distinct seats and reuses an employee's claim", () => {
    const meetings = new MeetingSystem(seats);
    expect(meetings.claim("alice")?.id).toBe("seat-1");
    expect(meetings.claim("bob")?.id).toBe("seat-2");
    expect(meetings.claim("alice")?.id).toBe("seat-1");
    expect(meetings.claim("charlie")).toBeNull();
  });

  it("releases seats when an employee leaves a meeting", () => {
    const meetings = new MeetingSystem(seats);
    meetings.claim("alice");
    meetings.release("alice");
    expect(meetings.claim("bob")?.id).toBe("seat-1");
  });
});
