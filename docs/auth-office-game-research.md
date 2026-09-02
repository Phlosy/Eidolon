# Eidolon Auth Office Game Research

## Scope

This implementation applies only to the animated office on the left side of the login,
registration, and verification shell. The authenticated `/office` workspace is deliberately
out of scope.

The cover is a small, self-contained 2D company simulation. React owns authentication and
accessible controls; Phaser owns the tilemap, sprites, animation, navigation, and ambient
effects. Because the cover appears before authentication, its initial data source is the
documented Office Demo Mode. The bridge accepts real `OfficeEmployeeState` snapshots and
events later without allowing Phaser to poll APIs or depend on React stores.

## Decisions

| Concern | Decision | Reason |
| --- | --- | --- |
| Renderer | Phaser 3 + TypeScript | Mature Canvas/WebGL scene, animation, input, camera, and tilemap lifecycle. |
| Map | Tiled-compatible JSON, orthogonal, 32×32 tiles | Keeps authored layers and interaction metadata separate from rendering code. |
| Resolution | 960×540 logical pixels | Fits the auth cover while preserving a roomy multi-zone office. |
| Scaling | `Phaser.Scale.FIT` + `CENTER_BOTH` | Preserves the logical aspect ratio inside responsive React layout. |
| Pixel rendering | `pixelArt`, `roundPixels`, nearest-neighbor CSS | Avoids bilinear blur on high-DPI displays. |
| Animation | Sprite sheets with an animation registry | One load per sheet; unknown animation names fall back safely to idle. |
| Navigation | Deterministic four-direction A* grid | Predictable paths are sufficient for a compact office; no physics navmesh is needed. |
| Depth | Layer depth plus sprite `y` sorting | Characters pass behind foreground furniture and in front of rear furniture. |
| Data flow | React → state adapter → event bridge → Phaser | Keeps authentication state and UI concerns outside the game scene. |
| Reduced motion | Static final frame and paused behavior loop | Moving cover media must be pausable and respect user preferences. |

## Primary-source findings

- Phaser's loader accepts Tiled JSON directly through `tilemapTiledJSON`, while its parser
  preserves tile, object, and custom-property data. This is the map contract used here.
- Phaser frame animations are built from sprite-sheet or atlas frames through the Animation
  Manager. Eidolon registers stable semantic names such as `employee.walk.down` rather than
  scattering frame indexes through entity code.
- Phaser's Scale Manager provides `FIT` and `CENTER_BOTH`, so React reserves the aspect-ratio
  box and Phaser scales its internal 960×540 world into it.
- Tiled JSON is kept as source-controlled map data. No in-product map editor is introduced.

References:

- [Phaser Tilemap parser](https://docs.phaser.io/api-documentation/function/tilemaps)
- [Phaser loader](https://docs.phaser.io/phaser/concepts/loader)
- [Phaser animations](https://docs.phaser.io/phaser/concepts/animations)
- [Phaser Scale Manager](https://docs.phaser.io/phaser/concepts/scale-manager)
- [Tiled JSON format](https://doc.mapeditor.org/en/stable/reference/json-map-format/)

## Runtime lifecycle

1. React mounts a stable host element.
2. The cover dynamically imports Phaser and creates exactly one game instance for that host.
3. Demo or future server state is normalized into `OfficeEmployeeState`.
4. The bridge publishes snapshots and discrete events without polling.
5. On unmount, listeners are removed and `game.destroy(true)` releases the canvas, textures,
   scene timers, and input handlers.

## Performance budget

- Phaser is dynamically imported by the auth cover and excluded from the initial application
  module graph until the large-screen cover is mounted.
- The default world supports 20 active employees and reserves entity pooling conventions for
  50+; the demo intentionally renders four readable archetypes.
- Asset files have fixed dimensions and the React host reserves a 16:9 region to avoid layout
  shift.
- Behavior decisions are event-driven and low frequency. Per-frame work is limited to movement,
  depth sorting, and small ambient effects.

