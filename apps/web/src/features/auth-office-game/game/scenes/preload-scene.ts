import Phaser from "phaser";

const ASSET_ROOT = "/assets/office-game/eidolon-default";

export class PreloadScene extends Phaser.Scene {
  constructor() {
    super("auth-office-preload");
  }

  preload(): void {
    const progress = this.add.graphics().setDepth(1000);
    this.load.on(Phaser.Loader.Events.PROGRESS, (value: number) => {
      progress
        .clear()
        .fillStyle(0xf1ddbd, 0.85)
        .fillRect(448, 354, 384 * value, 8);
    });
    this.load.once(Phaser.Loader.Events.COMPLETE, () => progress.destroy());
    this.load.tilemapTiledJSON("office-map", `${ASSET_ROOT}/maps/office.json`);
    this.load.image("office-tiles", `${ASSET_ROOT}/runtime/office-tiles.png`);
    this.load.atlas(
      "office-objects",
      `${ASSET_ROOT}/runtime/office-objects.png`,
      `${ASSET_ROOT}/runtime/office-objects.json`,
    );
    this.load.spritesheet("employees", `${ASSET_ROOT}/runtime/employees.png`, {
      frameWidth: 48,
      frameHeight: 64,
    });
  }

  create(): void {
    this.scene.start("auth-office-world");
  }
}
