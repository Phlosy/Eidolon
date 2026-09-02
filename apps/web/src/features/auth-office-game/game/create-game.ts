import Phaser from "phaser";
import type { OfficeEventBridge } from "../bridge/office-event-bridge";
import type { OfficeStateSnapshot } from "../types/office-state";
import { createOfficeGameConfig } from "./config";

export type CreateOfficeGameOptions = {
  parent: HTMLElement;
  bridge: OfficeEventBridge;
  initialState: OfficeStateSnapshot;
};

class AuthOfficeBootScene extends Phaser.Scene {
  constructor() {
    super("auth-office-boot");
  }

  create(): void {
    const bridge = this.registry.get("office:bridge") as OfficeEventBridge;
    this.cameras.main.setRoundPixels(true);
    bridge.emit("office.ready", undefined);
  }
}

export function createOfficeGame({
  parent,
  bridge,
  initialState,
}: CreateOfficeGameOptions): Phaser.Game {
  const game = new Phaser.Game(createOfficeGameConfig(parent, [AuthOfficeBootScene]));
  game.registry.set("office:bridge", bridge);
  game.registry.set("office:initial-state", initialState);
  return game;
}

