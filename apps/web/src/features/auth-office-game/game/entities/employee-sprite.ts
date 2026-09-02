import Phaser from "phaser";
import type { OfficeEventBridge } from "../../bridge/office-event-bridge";
import type { OfficeEmployeeState } from "../../types/office-state";
import {
  resolveEmployeeAnimation,
  shouldMirrorEmployee,
  type EmployeeAnimationName,
} from "../systems/animation-registry";
import type { EmployeeFacingDirection } from "../systems/employee-presentation";
import type { GridPoint } from "../systems/navigation-system";

export class EmployeeSprite extends Phaser.GameObjects.Sprite {
  readonly path: GridPoint[] = [];
  targetAnimation: EmployeeAnimationName = "idle";
  facing: EmployeeFacingDirection = "down";
  hoverPaused = false;

  constructor(
    scene: Phaser.Scene,
    x: number,
    y: number,
    public employee: OfficeEmployeeState,
    public readonly skinId: string,
    private readonly bridge: OfficeEventBridge,
  ) {
    super(scene, x, y, "employees");
    scene.add.existing(this);
    this.setOrigin(0.5, 1).setInteractive({ cursor: "pointer", useHandCursor: true });
    this.playActivity("idle");
    this.on(Phaser.Input.Events.POINTER_OVER, () => {
      this.hoverPaused = true;
      this.anims.pause();
      this.bridge.emit("office.employee.focus", { employeeId: this.employee.id });
    });
    this.on(Phaser.Input.Events.POINTER_OUT, () => {
      this.hoverPaused = false;
      this.anims.resume();
      this.bridge.emit("office.employee.focus", { employeeId: null });
    });
    this.on(Phaser.Input.Events.POINTER_DOWN, () => {
      this.bridge.emit("office.employee.selected", { employeeId: this.employee.id });
    });
  }

  playActivity(
    animation: EmployeeAnimationName,
    direction: EmployeeFacingDirection = this.facing,
  ): void {
    const key = resolveEmployeeAnimation(this.skinId, animation, direction);
    if (this.facing === direction && this.anims.currentAnim?.key === key) return;

    this.targetAnimation = animation;
    this.facing = direction;
    this.setFlipX(shouldMirrorEmployee(direction));
    this.play(key, true);
  }

  replacePath(path: GridPoint[]): void {
    this.path.splice(0, this.path.length, ...path.slice(1));
    if (this.path.length > 0) this.playActivity("walk", this.facing);
  }

  updateEmployee(employee: OfficeEmployeeState): void {
    this.employee = employee;
  }
}
