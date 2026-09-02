import Phaser from "phaser";
import type { OfficeEventBridge } from "../../bridge/office-event-bridge";
import type { OfficeEmployeeState, OfficeStateSnapshot } from "../../types/office-state";
import { EmployeeSprite } from "../entities/employee-sprite";
import { OFFICE_TILE_SIZE } from "../config";
import { registerEmployeeAnimations } from "../systems/animation-registry";
import {
  EmployeeBehaviorSystem,
  type BehaviorDirective,
} from "../systems/employee-behavior-system";
import { MeetingSystem } from "../systems/meeting-system";
import { findGridPath, type GridPoint, type NavigationGrid } from "../systems/navigation-system";
import {
  OfficeAssignmentService,
  type InteractionPointType,
  type OfficeInteractionPoint,
  type OfficeZone,
  type OfficeZoneType,
} from "../systems/zone-system";

type RuntimeEmployee = {
  sprite: EmployeeSprite;
  directive: BehaviorDirective;
  destination: GridPoint | null;
};

function objectProperty(object: Phaser.Types.Tilemaps.TiledObject, name: string): unknown {
  return object.properties?.find((property: { name: string }) => property.name === name)?.value;
}

function pixelToTile(value: number | undefined): number {
  return Math.floor((value ?? 0) / OFFICE_TILE_SIZE);
}

function tileCenter(point: GridPoint): Phaser.Math.Vector2 {
  return new Phaser.Math.Vector2(
    point.x * OFFICE_TILE_SIZE + OFFICE_TILE_SIZE / 2,
    point.y * OFFICE_TILE_SIZE + OFFICE_TILE_SIZE / 2,
  );
}

export class OfficeScene extends Phaser.Scene {
  private bridge!: OfficeEventBridge;
  private snapshot!: OfficeStateSnapshot;
  private assignment!: OfficeAssignmentService;
  private meetings!: MeetingSystem;
  private grid!: NavigationGrid;
  private readonly behavior = new EmployeeBehaviorSystem();
  private readonly employees = new Map<string, RuntimeEmployee>();
  private cleanup: Array<() => void> = [];
  private paused = false;

  constructor() {
    super("auth-office-world");
  }

  create(): void {
    this.bridge = this.registry.get("office:bridge") as OfficeEventBridge;
    this.snapshot = this.registry.get("office:initial-state") as OfficeStateSnapshot;
    this.cameras.main.setRoundPixels(true).setBackgroundColor("#2b2130");

    const map = this.make.tilemap({ key: "office-map" });
    const tiles = map.addTilesetImage("office-tiles", "office-tiles");
    if (!tiles) throw new Error("The eidolon-default tileset could not be loaded");
    map.createLayer("Floor", tiles)?.setDepth(0);
    map.createLayer("Walls", tiles)?.setDepth(10);
    map.createLayer("Foreground", tiles)?.setDepth(900);

    this.renderObjectLayer(map, "Wall Decoration", -80);
    this.renderObjectLayer(map, "Furniture", 0);
    this.renderObjectLayer(map, "Furniture Front", 80);

    const zones = this.readZones(map);
    const points = this.readInteractionPoints(map);
    this.assignment = new OfficeAssignmentService(zones, points);
    this.meetings = new MeetingSystem(points.filter((point) => point.type === "meeting-seat"));
    this.grid = this.buildNavigationGrid(map);

    registerEmployeeAnimations(this);
    this.syncEmployees(this.snapshot);
    this.addAmbientEffects();

    this.cleanup = [
      this.bridge.on("office.state.replace", (snapshot) => this.syncEmployees(snapshot)),
      this.bridge.on("office.motion.set", ({ paused }) => this.setMotionPaused(paused)),
      this.bridge.on("office.employee.focus", ({ employeeId }) => this.focusEmployee(employeeId)),
    ];
    this.events.once(Phaser.Scenes.Events.SHUTDOWN, () => this.destroyScene());
    this.bridge.emit("office.ready", undefined);
  }

  update(_: number, delta: number): void {
    if (this.paused) return;
    for (const runtime of this.employees.values()) this.moveEmployee(runtime, delta);
  }

  private renderObjectLayer(
    map: Phaser.Tilemaps.Tilemap,
    layerName: string,
    baseDepth: number,
  ): void {
    const layer = map.getObjectLayer(layerName);
    layer?.objects.forEach((object) => {
      const frame = String(objectProperty(object, "frame") ?? "plant");
      const depthOffset = Number(objectProperty(object, "depthOffset") ?? 0);
      this.add
        .image(object.x ?? 0, object.y ?? 0, "office-objects", frame)
        .setOrigin(0.5, 1)
        .setDepth(baseDepth + (object.y ?? 0) + depthOffset);
    });
  }

  private readZones(map: Phaser.Tilemaps.Tilemap): OfficeZone[] {
    return (
      map.getObjectLayer("Zones")?.objects.map((object) => ({
        id: object.name,
        type: object.type as OfficeZoneType,
        bounds: {
          x: pixelToTile(object.x),
          y: pixelToTile(object.y),
          width: pixelToTile(object.width),
          height: pixelToTile(object.height),
        },
        capacity: Number(objectProperty(object, "capacity") ?? 1),
      })) ?? []
    );
  }

  private readInteractionPoints(map: Phaser.Tilemaps.Tilemap): OfficeInteractionPoint[] {
    return (
      map.getObjectLayer("Interaction")?.objects.map((object) => ({
        id: object.name,
        type: object.type as InteractionPointType,
        zoneId: String(objectProperty(object, "zone") ?? "shared"),
        tile: { x: pixelToTile(object.x), y: pixelToTile(object.y) },
        facing: String(
          objectProperty(object, "facing") ?? "down",
        ) as OfficeInteractionPoint["facing"],
      })) ?? []
    );
  }

  private buildNavigationGrid(map: Phaser.Tilemaps.Tilemap): NavigationGrid {
    const blocked = new Set<string>();
    const walls = map.getLayer("Walls")?.data;
    walls?.forEach((row, y) =>
      row.forEach((tile, x) => {
        if (tile.index >= 0) blocked.add(`${x},${y}`);
      }),
    );
    return { width: map.width, height: map.height, blocked };
  }

  private syncEmployees(snapshot: OfficeStateSnapshot): void {
    this.snapshot = snapshot;
    const incomingIds = new Set(snapshot.employees.map((employee) => employee.id));
    for (const [employeeId, runtime] of this.employees) {
      if (!incomingIds.has(employeeId)) {
        runtime.sprite.destroy();
        this.employees.delete(employeeId);
        this.assignment.release(employeeId);
        this.meetings.release(employeeId);
        this.behavior.remove(employeeId);
      }
    }
    snapshot.employees.forEach((employee, index) => this.upsertEmployee(employee, index));
  }

  private upsertEmployee(employee: OfficeEmployeeState, index: number): void {
    let runtime = this.employees.get(employee.id);
    if (!runtime) {
      const entrance = this.assignment.pointFor("entrance")?.tile ?? { x: 27, y: 13 };
      const position = tileCenter({ x: entrance.x, y: Math.max(2, entrance.y - (index % 2)) });
      const skinId = `employee-${index % 4}`;
      const sprite = new EmployeeSprite(
        this,
        position.x,
        position.y,
        employee,
        skinId,
        this.bridge,
      );
      runtime = { sprite, directive: this.behavior.update(employee), destination: null };
      this.employees.set(employee.id, runtime);
    } else {
      runtime.sprite.updateEmployee(employee);
      runtime.directive = this.behavior.update(employee);
    }
    if (employee.status !== "OFFLINE") runtime.sprite.setVisible(true);
    if (runtime.directive.changed || runtime.destination === null) this.planEmployee(runtime);
  }

  private planEmployee(runtime: RuntimeEmployee): void {
    const { employee } = runtime.sprite;
    if (runtime.directive.state !== "MEETING") this.meetings.release(employee.id);
    let point: OfficeInteractionPoint | null = null;
    if (runtime.directive.targetType === "workstation")
      point = this.assignment.workstationFor(employee);
    else if (runtime.directive.targetType === "meeting-seat")
      point = this.meetings.claim(employee.id);
    else if (runtime.directive.targetType !== "current") {
      point = this.assignment.pointFor(runtime.directive.targetType);
    }
    if (!point) {
      runtime.destination = null;
      runtime.sprite.playActivity(runtime.directive.animation);
      return;
    }
    runtime.destination = point.tile;
    const current = {
      x: pixelToTile(runtime.sprite.x),
      y: pixelToTile(runtime.sprite.y),
    };
    const path = findGridPath(current, point.tile, this.grid);
    runtime.sprite.replacePath(path);
    if (path.length <= 1) runtime.sprite.playActivity(runtime.directive.animation);
  }

  private moveEmployee(runtime: RuntimeEmployee, delta: number): void {
    const { sprite } = runtime;
    if (sprite.hoverPaused || sprite.path.length === 0) return;
    const target = tileCenter(sprite.path[0]);
    const distance = Phaser.Math.Distance.Between(sprite.x, sprite.y, target.x, target.y);
    if (distance <= 2) {
      sprite.setPosition(target.x, target.y);
      sprite.path.shift();
      if (sprite.path.length === 0) {
        sprite.playActivity(runtime.directive.animation);
        if (runtime.directive.state === "OFFLINE") sprite.setVisible(false);
      }
    } else {
      const speed = (48 * delta) / 1000;
      sprite.x += ((target.x - sprite.x) / distance) * Math.min(speed, distance);
      sprite.y += ((target.y - sprite.y) / distance) * Math.min(speed, distance);
    }
    sprite.setDepth(sprite.y + 30);
  }

  private addAmbientEffects(): void {
    const serverLight = this.add.circle(872, 216, 3, 0xd5aa52, 0.8).setDepth(300);
    const windowLight = this.add.rectangle(390, 132, 250, 3, 0x7fa6b8, 0.16).setDepth(15);
    this.tweens.add({ targets: serverLight, alpha: 0.25, duration: 900, yoyo: true, repeat: -1 });
    this.tweens.add({ targets: windowLight, alpha: 0.08, duration: 2600, yoyo: true, repeat: -1 });
  }

  private focusEmployee(employeeId: string | null): void {
    for (const [id, runtime] of this.employees) {
      runtime.sprite.setTint(id === employeeId ? 0xfff2bd : 0xffffff);
      if (id !== employeeId) runtime.sprite.clearTint();
    }
  }

  private setMotionPaused(paused: boolean): void {
    this.paused = paused;
    this.tweens
      .getTweens()
      .forEach((tween: Phaser.Tweens.Tween) => (paused ? tween.pause() : tween.resume()));
    for (const runtime of this.employees.values()) {
      if (paused) runtime.sprite.anims.pause();
      else if (!runtime.sprite.hoverPaused) runtime.sprite.anims.resume();
    }
  }

  private destroyScene(): void {
    this.cleanup.forEach((dispose) => dispose());
    this.cleanup = [];
    this.employees.clear();
  }
}
