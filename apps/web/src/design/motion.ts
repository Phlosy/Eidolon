export const motion = {
  duration: { fast: 140, normal: 260, slow: 480 },
  easing: { standard: "cubic-bezier(0.2, 0.8, 0.2, 1)", enter: "cubic-bezier(0.16, 1, 0.3, 1)", exit: "cubic-bezier(0.4, 0, 1, 1)" },
} as const;

export function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}
