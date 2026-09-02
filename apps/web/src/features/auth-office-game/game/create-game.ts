import Phaser from "phaser";
import type { OfficeEventBridge } from "../bridge/office-event-bridge";
import type { OfficeStateSnapshot } from "../types/office-state";
import { createOfficeGameConfig } from "./config";
import { BootScene } from "./scenes/boot-scene";
import { OfficeScene } from "./scenes/office-scene";
import { PreloadScene } from "./scenes/preload-scene";

export type CreateOfficeGameOptions = {
  parent: HTMLElement;
  bridge: OfficeEventBridge;
  initialState: OfficeStateSnapshot;
};

export function createOfficeGame({
  parent,
  bridge,
  initialState,
}: CreateOfficeGameOptions): Phaser.Game {
  const config = createOfficeGameConfig(parent, [BootScene, PreloadScene, OfficeScene]);
  config.callbacks = {
    preBoot: (game) => {
      game.registry.set("office:bridge", bridge);
      game.registry.set("office:initial-state", initialState);
    },
  };
  return new Phaser.Game(config);
}
