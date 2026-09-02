export type EmployeeFacingDirection = "up" | "down" | "left" | "right";

export type PositionLike = { x: number; y: number };

export function resolveMovementDirection(
  current: PositionLike,
  target: PositionLike,
  fallback: EmployeeFacingDirection,
): EmployeeFacingDirection {
  const deltaX = target.x - current.x;
  const deltaY = target.y - current.y;
  if (deltaX === 0 && deltaY === 0) return fallback;
  if (Math.abs(deltaY) >= Math.abs(deltaX)) return deltaY < 0 ? "up" : "down";
  return deltaX < 0 ? "left" : "right";
}

export function resolveEmployeeDepth(y: number, interactionDepthOffset?: number): number {
  return y + (interactionDepthOffset ?? 30);
}
