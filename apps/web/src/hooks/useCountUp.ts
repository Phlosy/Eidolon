import { useEffect, useRef, useState } from "react";

/** True when the user prefers reduced motion (checked once, SSR-safe). */
function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

const DEFAULT_DURATION_MS = 450;

/** easeOutCubic — fast start, soft landing. */
function easeOutCubic(t: number): number {
  return 1 - (1 - t) ** 3;
}

/**
 * Animated count-up for dashboard stats: eases from the currently displayed
 * value to `target` over ~450ms. Re-animates whenever `target` changes.
 * With `prefers-reduced-motion` the value snaps instantly.
 */
export function useCountUp(target: number, durationMs = DEFAULT_DURATION_MS): number {
  // Start at 0 so the first mount animates up; reduced-motion starts at target.
  const [display, setDisplay] = useState(() => (prefersReducedMotion() ? target : 0));
  const displayRef = useRef(target);
  displayRef.current = display;

  useEffect(() => {
    const from = displayRef.current;
    if (from === target || prefersReducedMotion()) {
      setDisplay(target);
      return;
    }
    let raf = 0;
    const start = performance.now();
    const tick = (now: number) => {
      const progress = Math.min(1, (now - start) / durationMs);
      const value = Math.round(from + (target - from) * easeOutCubic(progress));
      setDisplay(value);
      if (progress < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, durationMs]);

  return display;
}
