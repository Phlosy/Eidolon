import type { OfficeInteractionPoint } from "./zone-system";

export class MeetingSystem {
  private readonly claims = new Map<string, OfficeInteractionPoint>();

  constructor(private readonly seats: OfficeInteractionPoint[]) {}

  claim(employeeId: string): OfficeInteractionPoint | null {
    const existing = this.claims.get(employeeId);
    if (existing) return existing;
    const occupied = new Set([...this.claims.values()].map((seat) => seat.id));
    const seat = this.seats.find((candidate) => !occupied.has(candidate.id)) ?? null;
    if (seat) this.claims.set(employeeId, seat);
    return seat;
  }

  release(employeeId: string): void {
    this.claims.delete(employeeId);
  }
}

