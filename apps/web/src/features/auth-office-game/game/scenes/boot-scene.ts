import Phaser from "phaser";

export class BootScene extends Phaser.Scene {
  constructor() {
    super("auth-office-boot");
  }

  create(): void {
    this.cameras.main.setRoundPixels(true);
    this.scene.start("auth-office-preload");
  }
}

