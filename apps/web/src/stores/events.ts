import { create } from "zustand";
import type { StreamEvent } from "../types";

const MAX_EVENTS = 100;

export type ConnectionState = "connecting" | "open" | "closed";

interface EventStreamState {
  /** Live events pushed by the WS, newest first, capped. */
  events: StreamEvent[];
  connection: ConnectionState;
  /** Advances when the authenticated user/company changes. */
  generation: number;
  pushEvent: (event: StreamEvent) => void;
  setConnection: (state: ConnectionState) => void;
  reset: () => void;
}

export const useEventStreamStore = create<EventStreamState>()((set) => ({
  events: [],
  connection: "closed",
  generation: 0,
  pushEvent: (event) => set((state) => ({ events: [event, ...state.events].slice(0, MAX_EVENTS) })),
  setConnection: (connection) => set({ connection }),
  reset: () =>
    set((state) => ({ events: [], connection: "closed", generation: state.generation + 1 })),
}));
