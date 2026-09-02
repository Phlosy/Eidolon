import { useEffect, useState } from "react";

const TICK_MS = 1000;

/** `mm:ss` under an hour, `h:mm:ss` beyond — for live elapsed timers. */
export function formatElapsed(totalSeconds: number): string {
  const seconds = Math.max(0, Math.floor(totalSeconds));
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  const mm = String(m).padStart(2, "0");
  const ss = String(s).padStart(2, "0");
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

function elapsedSecondsSince(startIso: string): number | null {
  const start = new Date(startIso).getTime();
  if (Number.isNaN(start)) return null;
  return Math.max(0, Math.floor((Date.now() - start) / 1000));
}

/**
 * Live ticking elapsed timer anchored at an ISO timestamp (e.g. a task's
 * created_at). Returns a formatted `mm:ss` / `h:mm:ss` string, or `null`
 * when there is no valid anchor. Ticks once per second.
 */
export function useElapsedSeconds(startIso: string | null | undefined): string | null {
  const [seconds, setSeconds] = useState<number | null>(() =>
    startIso ? elapsedSecondsSince(startIso) : null,
  );

  useEffect(() => {
    if (!startIso) {
      setSeconds(null);
      return;
    }
    setSeconds(elapsedSecondsSince(startIso));
    const timer = setInterval(() => setSeconds(elapsedSecondsSince(startIso)), TICK_MS);
    return () => clearInterval(timer);
  }, [startIso]);

  return seconds == null ? null : formatElapsed(seconds);
}
