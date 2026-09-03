import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { wsUrl } from "../api/client";
import { useEventStreamStore } from "../stores/events";
import type { StreamEvent } from "../types";

const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 15000;

/** Map an event type to the TanStack Query keys it should invalidate. */
// 教程门禁挂在员工/runtime/provider/项目/文档/git 等真实状态上：这些域一有事件
// 就重取教程进度，用户做完动作立刻看到推进，而不是等下一轮轮询。
const TUTORIAL: string[] = ["tutorial"];

function invalidatedKeys(type: string): string[][] {
  const domain = type.split(".")[0];
  switch (domain) {
    case "tutorial":
      return [TUTORIAL, ["events"]];
    case "employee":
      return [["employees"], ["events"], ["runtimes"], TUTORIAL];
    case "runtime":
      return [
        ["employees"],
        ["events"],
        ["runtimes"],
        ["runtime-images"],
        ["runtime-types"],
        TUTORIAL,
      ];
    case "provider":
      return [["providers"], ["events"], TUTORIAL];
    case "project":
      return [["projects"], ["events"], TUTORIAL];
    case "task":
      return [["tasks"], ["projects"], ["employees"], ["events"], TUTORIAL];
    case "artifact":
      return [["artifacts"], ["projects"], ["events"], TUTORIAL];
    case "drive":
    case "document":
      return [["events"], TUTORIAL];
    case "git":
    case "review":
      return [["events"], ["projects"], TUTORIAL];
    case "skill":
    case "learning":
    case "knowledge":
      return [["employees"], ["events"]];
    default:
      return [["events"]];
  }
}

/**
 * WebSocket to /ws/events with simple auto-reconnect (exponential backoff).
 * Pushes events into the zustand buffer for the activity feed and
 * invalidates the relevant query keys so pages refresh live.
 */
export function useEventStream(): void {
  const queryClient = useQueryClient();
  const pushEvent = useEventStreamStore((s) => s.pushEvent);
  const setConnection = useEventStreamStore((s) => s.setConnection);

  useEffect(() => {
    let socket: WebSocket | null = null;
    let attempts = 0;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const connect = () => {
      if (stopped) return;
      setConnection("connecting");
      socket = new WebSocket(wsUrl("/ws/events"));

      socket.onopen = () => {
        attempts = 0;
        setConnection("open");
      };

      socket.onmessage = (msg: MessageEvent<string>) => {
        try {
          const event = JSON.parse(msg.data) as StreamEvent;
          pushEvent(event);
          for (const key of invalidatedKeys(event.type)) {
            void queryClient.invalidateQueries({ queryKey: key });
          }
        } catch {
          // ignore malformed frames
        }
      };

      socket.onclose = () => {
        setConnection("closed");
        if (stopped) return;
        const delay = Math.min(RECONNECT_MAX_MS, RECONNECT_BASE_MS * 2 ** attempts);
        attempts += 1;
        timer = setTimeout(connect, delay);
      };

      socket.onerror = () => {
        socket?.close();
      };
    };

    connect();

    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
      socket?.close();
    };
  }, [queryClient, pushEvent, setConnection]);
}
