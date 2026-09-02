# Eidolon Pixel Art Style Guide

## Identity

Eidolon's visual language is **Cozy Pixel Corporate RPG**: an original, calm, lived-in office
world that communicates focused work and gentle company life. It may use the broad language of
classic 2D management and role-playing games, but it must not reproduce a commercial game's
characters, proportions, palettes, maps, interface, or distinctive assets.

## Geometry

- Perspective: orthogonal top-down / three-quarter RPG view.
- Tile: 32×32 logical pixels everywhere.
- Character frame: 32×48 logical pixels; feet align to the tile interaction point.
- Logical game resolution: 960×540.
- Walls: two tiles high; doors: one tile wide and two tiles high.
- Desks: two to three tiles wide; chairs and interaction anchors: one tile.
- Rendering: nearest neighbor, anti-aliasing disabled, integer camera coordinates.

## Palette

The runtime pack uses this restrained palette. New assets should choose from it before adding a
new swatch.

| Role | Hex | Use |
| --- | --- | --- |
| Ink | `#2b2130` | Shared outline and deepest occlusion. |
| Deep wood | `#5b3a3a` | Furniture shadow, wall trim. |
| Walnut | `#8b5a3c` | Desks, shelves, doors. |
| Honey | `#c98b52` | Lit wood and warm accents. |
| Cream | `#f1ddbd` | Walls and light upholstery. |
| Paper | `#fff4da` | Documents and small highlights. |
| Moss | `#5f7650` | Plants and calm success cues. |
| Sage | `#8fa47b` | Plant highlights and lounge textiles. |
| Navy | `#33465f` | Screens, blue clothing, cool shadow. |
| Sky | `#7fa6b8` | Windows and secondary highlights. |
| Terracotta | `#b9604c` | Warm status/accent detail. |
| Gold | `#d5aa52` | Active lamps and small focal points. |

Avoid highly saturated primary colors, pure black outlines, airbrushed gradients, glossy 3D
materials, and inconsistent outline temperatures.

## Light, shadow, and texture

- Light direction is upper-left. Each material uses one highlight and one shadow step.
- Contact shadows are short, dark-plum shapes beneath feet and furniture, not soft drop shadows.
- Windows provide a pale-blue secondary light; lamps use honey/gold pools at low opacity.
- Floor variation is expressed by alternate tiles, rugs, cables, and small objects rather than
  noise over every surface.
- Ambient animation is quiet: monitor glow, server LEDs, coffee steam, clock tick, and door swing.

## Character contract

Characters use an original compact adult proportion, readable hair silhouettes, restrained
office clothing, and a consistent dark outline. Avatar images in management UI remain separate
from `game_character_skin`.

Required semantic animations:

- Core: `idle`, `walk`, `work`, `meeting`.
- Directional: `idle_down`, `idle_up`, `idle_left`, `idle_right`, `walk_down`, `walk_up`,
  `walk_left`, `walk_right`.
- Reserved: `type`, `read`, `talk`, `sit`, `think`, `idle_coffee`.

Each animation must have a registry fallback. Unknown direction or activity resolves to
`employee.idle.down` rather than failing the scene.

## Interface and accessibility

- The Canvas contains the world only. Authentication, language, pause, and employee access use
  semantic React controls.
- Character hover may reveal a short role-specific line, but click/focus must provide the same
  result. No essential information is hover-only.
- Motion pauses on explicit request, focus, document hiding, and `prefers-reduced-motion`.
- Status is expressed through motion, icon shape, and short text—not color alone.

