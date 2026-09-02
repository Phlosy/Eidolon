import Phaser from "phaser";

export const employeeAnimationNames = ["idle", "walk", "work", "meeting"] as const;
export type EmployeeAnimationName = (typeof employeeAnimationNames)[number];

export function resolveEmployeeAnimation(skinId: string, animation: string): string {
  const safeAnimation = employeeAnimationNames.includes(animation as EmployeeAnimationName)
    ? animation
    : "idle";
  return `${skinId}.${safeAnimation}`;
}

export function registerEmployeeAnimations(scene: Phaser.Scene, rowCount = 4): void {
  employeeAnimationNames.forEach((name, animationIndex) => {
    for (let row = 0; row < rowCount; row += 1) {
      const skinId = `employee-${row}`;
      const key = resolveEmployeeAnimation(skinId, name);
      if (scene.anims.exists(key)) continue;
      const first = row * 16 + animationIndex * 4;
      scene.anims.create({
        key,
        frames: scene.anims.generateFrameNumbers("employees", { start: first, end: first + 3 }),
        frameRate: name === "walk" ? 7 : 3,
        repeat: -1,
      });
    }
  });
}
