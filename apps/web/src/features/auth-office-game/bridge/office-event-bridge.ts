import type { OfficeStateSnapshot } from "../types/office-state";

export type OfficeGameEvents = {
  "office.ready": undefined;
  "office.state.replace": OfficeStateSnapshot;
  "office.employee.focus": { employeeId: string | null };
  "office.employee.selected": { employeeId: string };
  "office.motion.set": { paused: boolean };
};

type OfficeEventName = keyof OfficeGameEvents;
type OfficeListener<K extends OfficeEventName> = (payload: OfficeGameEvents[K]) => void;

export class OfficeEventBridge {
  private readonly listeners = new Map<OfficeEventName, Set<(payload: never) => void>>();

  emit<K extends OfficeEventName>(name: K, payload: OfficeGameEvents[K]): void {
    this.listeners.get(name)?.forEach((listener) => listener(payload as never));
  }

  on<K extends OfficeEventName>(name: K, listener: OfficeListener<K>): () => void {
    const current = this.listeners.get(name) ?? new Set<(payload: never) => void>();
    current.add(listener as (payload: never) => void);
    this.listeners.set(name, current);
    return () => {
      current.delete(listener as (payload: never) => void);
      if (current.size === 0) this.listeners.delete(name);
    };
  }

  clear(): void {
    this.listeners.clear();
  }

  listenerCount(name: OfficeEventName): number {
    return this.listeners.get(name)?.size ?? 0;
  }
}
