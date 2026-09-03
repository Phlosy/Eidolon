import Phaser from "phaser";
import type { EmployeeFacingDirection } from "./employee-presentation";

export const employeeAnimationNames = ["idle", "walk", "work", "meeting"] as const;
export type EmployeeAnimationName = (typeof employeeAnimationNames)[number];

type AnimationView = "down" | "up" | "side";
type AnimationFrameRange = { start: number; count: number };

const animationLayout: Record<EmployeeAnimationName, Record<AnimationView, AnimationFrameRange>> = {
  idle: {
    down: { start: 0, count: 4 },
    up: { start: 4, count: 4 },
    side: { start: 8, count: 4 },
  },
  walk: {
    down: { start: 12, count: 10 },
    up: { start: 22, count: 8 },
    side: { start: 30, count: 8 },
  },
  work: {
    down: { start: 38, count: 4 },
    up: { start: 38, count: 4 },
    side: { start: 42, count: 4 },
  },
  meeting: {
    down: { start: 0, count: 4 },
    up: { start: 4, count: 4 },
    side: { start: 8, count: 4 },
  },
};

const employeeFramesPerRow = Math.max(
  ...Object.values(animationLayout).flatMap((views) =>
    Object.values(views).map(({ start, count }) => start + count),
  ),
);

function animationView(direction: string): AnimationView {
  if (direction === "up" || direction === "down") return direction;
  if (direction === "left" || direction === "right" || direction === "side") return "side";
  return "down";
}

export function resolveEmployeeAnimation(
  skinId: string,
  animation: string,
  direction: string,
): string {
  const safeAnimation = employeeAnimationNames.includes(animation as EmployeeAnimationName)
    ? (animation as EmployeeAnimationName)
    : "idle";
  const view = animationView(direction);
  return `${skinId}.${safeAnimation}.${view}`;
}

function resolveEmployeeAnimationFrames(
  row: number,
  animation: EmployeeAnimationName,
  view: AnimationView,
): { start: number; end: number } {
  const range = animationLayout[animation][view];
  const start = row * employeeFramesPerRow + range.start;
  return { start, end: start + range.count - 1 };
}

export function registerEmployeeAnimations(scene: Phaser.Scene, rowCount = 4): void {
  employeeAnimationNames.forEach((name) => {
    for (let row = 0; row < rowCount; row += 1) {
      const skinId = `employee-${row}`;
      (["down", "up", "side"] as const).forEach((view) => {
        const key = resolveEmployeeAnimation(skinId, name, view);
        if (scene.anims.exists(key)) return;
        const frames = resolveEmployeeAnimationFrames(row, name, view);
        scene.anims.create({
          key,
          frames: scene.anims.generateFrameNumbers("employees", frames),
          frameRate: name === "walk" ? 10 : name === "work" ? 5 : 3,
          repeat: -1,
        });
      });
    }
  });
}

export function shouldMirrorEmployee(direction: EmployeeFacingDirection): boolean {
  return direction === "left";
}
