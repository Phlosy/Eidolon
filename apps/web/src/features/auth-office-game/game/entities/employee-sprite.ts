import Phaser from "phaser";
import type { OfficeEventBridge } from "../../bridge/office-event-bridge";
import type { OfficeEmployeeState } from "../../types/office-state";
import {
  resolveEmployeeAnimation,
  type EmployeeAnimationName,
} from "../systems/animation-registry";
import type { GridPoint } from "../systems/navigation-system";

export class EmployeeSprite extends Phaser.GameObjects.Sprite {
  readonly path: GridPoint[] = [];
  targetAnimation: EmployeeAnimationName = "idle";
  hoverPaused = false;

  constructor(
    scene: Phaser.Scene,
    x: number,
    y: number,
    public employee: OfficeEmployeeState,
    public readonly skinId: string,
    private readonly bridge: OfficeEventBridge,
  ) {
    super(scene, x, y, "employees", Number(skinId.split("-").at(-1) ?? 0) * 16);
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

  playActivity(animation: EmployeeAnimationName): void {
    this.targetAnimation = animation;
    this.play(resolveEmployeeAnimation(this.skinId, animation), true);
  }

  replacePath(path: GridPoint[]): void {
    this.path.splice(0, this.path.length, ...path.slice(1));
    if (this.path.length > 0) this.playActivity("walk");
  }

  updateEmployee(employee: OfficeEmployeeState): void {
    this.employee = employee;
  }
}
