import { renderHook, act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useCountUp } from "./useCountUp";
import { formatElapsed, useElapsedSeconds } from "./useElapsedSeconds";

describe("useCountUp", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("eases from 0 to the target within the duration", () => {
    const { result } = renderHook(() => useCountUp(10, 400));
    act(() => vi.advanceTimersByTime(100));
    const midway = result.current;
    expect(midway).toBeGreaterThan(0);
    expect(midway).toBeLessThan(10);
    act(() => vi.advanceTimersByTime(1000));
    expect(result.current).toBe(10);
  });

  it("snaps instantly when the user prefers reduced motion", () => {
    const original = window.matchMedia;
    window.matchMedia = vi.fn().mockReturnValue({
      matches: true,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }) as unknown as typeof window.matchMedia;
    try {
      const { result, rerender } = renderHook(({ target }) => useCountUp(target, 400), {
        initialProps: { target: 0 },
      });
      rerender({ target: 7 });
      // No frames advanced: the reduced-motion path must already be at target.
      expect(result.current).toBe(7);
    } finally {
      window.matchMedia = original;
    }
  });
});

describe("formatElapsed", () => {
  it("formats mm:ss under an hour and h:mm:ss beyond", () => {
    expect(formatElapsed(0)).toBe("00:00");
    expect(formatElapsed(65)).toBe("01:05");
    expect(formatElapsed(3661)).toBe("1:01:01");
    expect(formatElapsed(-5)).toBe("00:00");
  });
});

describe("useElapsedSeconds", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-02T12:00:00Z"));
  });
  afterEach(() => vi.useRealTimers());

  it("ticks upward once per second from the anchor", () => {
    const { result } = renderHook(() => useElapsedSeconds("2026-09-02T11:59:30Z"));
    expect(result.current).toBe("00:30");
    act(() => vi.advanceTimersByTime(5000));
    expect(result.current).toBe("00:35");
  });

  it("returns null without a valid anchor", () => {
    const { result } = renderHook(() => useElapsedSeconds(null));
    expect(result.current).toBeNull();
    act(() => vi.advanceTimersByTime(3000));
    expect(result.current).toBeNull();
  });
});
