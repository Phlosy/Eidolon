import { create } from "zustand";
import type { StreamEvent } from "../types";

const MAX_EVENTS = 100;

export type ConnectionState = "connecting" | "open" | "closed";

interface EventStreamState {
  /** Live events pushed by the WS, newest first, capped. */
  events: StreamEvent[];
  connection: ConnectionState;
  pushEvent: (event: StreamEvent) => void;
  setConnection: (state: ConnectionState) => void;
}

export const useEventStreamStore = create<EventStreamState>()((set) => ({
  events: [],
  connection: "connecting",
  pushEvent: (event) => set((state) => ({ events: [event, ...state.events].slice(0, MAX_EVENTS) })),
  setConnection: (connection) => set({ connection }),
}));
