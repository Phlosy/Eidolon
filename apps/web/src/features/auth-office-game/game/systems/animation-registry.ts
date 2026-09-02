import Phaser from "phaser";
import type { EmployeeFacingDirection } from "./employee-presentation";

export const employeeAnimationNames = ["idle", "walk", "work", "meeting"] as const;
export type EmployeeAnimationName = (typeof employeeAnimationNames)[number];

type AnimationView = "down" | "up" | "side";

const animationLayout: Record<EmployeeAnimationName, Record<AnimationView, number>> = {
  idle: { down: 0, up: 1, side: 2 },
  walk: { down: 3, up: 4, side: 5 },
  work: { down: 6, up: 6, side: 7 },
  meeting: { down: 0, up: 1, side: 2 },
};

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

export function registerEmployeeAnimations(scene: Phaser.Scene, rowCount = 4): void {
  employeeAnimationNames.forEach((name) => {
    for (let row = 0; row < rowCount; row += 1) {
      const skinId = `employee-${row}`;
      (["down", "up", "side"] as const).forEach((view) => {
        const key = resolveEmployeeAnimation(skinId, name, view);
        if (scene.anims.exists(key)) return;
        const first = row * 32 + animationLayout[name][view] * 4;
        scene.anims.create({
          key,
          frames: scene.anims.generateFrameNumbers("employees", { start: first, end: first + 3 }),
          frameRate: name === "walk" ? 8 : name === "work" ? 5 : 3,
          repeat: -1,
        });
      });
    }
  });
}

export function shouldMirrorEmployee(direction: EmployeeFacingDirection): boolean {
  return direction === "left";
}
