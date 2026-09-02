import Phaser from "phaser";

export const OFFICE_LOGICAL_WIDTH = 1280;
export const OFFICE_LOGICAL_HEIGHT = 720;
export const OFFICE_TILE_SIZE = 32;

export function createOfficeGameConfig(
  parent: HTMLElement,
  scenes: Phaser.Types.Scenes.SceneType[],
): Phaser.Types.Core.GameConfig {
  return {
    type: Phaser.AUTO,
    parent,
    width: OFFICE_LOGICAL_WIDTH,
    height: OFFICE_LOGICAL_HEIGHT,
    backgroundColor: "#2b2130",
    pixelArt: true,
    roundPixels: true,
    antialias: false,
    antialiasGL: false,
    transparent: false,
    render: { pixelArt: true, roundPixels: true, antialias: false },
    scale: {
      mode: Phaser.Scale.FIT,
      autoCenter: Phaser.Scale.CENTER_BOTH,
      width: OFFICE_LOGICAL_WIDTH,
      height: OFFICE_LOGICAL_HEIGHT,
    },
    scene: scenes,
    audio: { noAudio: true },
  };
}
