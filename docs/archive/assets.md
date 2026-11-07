# Eidolon Office Asset Registry

## Policy

Only assets with known origin and redistribution permission may enter the repository. Random
image search results, Pinterest content, screenshots, extracted commercial-game art, and assets
with unclear licensing are prohibited.

The built-in `eidolon-default` theme is self-contained and works without a paid pack. Future
third-party packs should be imported through an `OfficeTheme` manifest; restricted or paid files
must remain outside this repository.

## Pipeline

```text
assets/office-game/<theme>/
├── source/       # original source sheets and generation outputs
├── processed/    # normalized, cropped, palette-aligned intermediates
├── runtime/      # optimized tile/sprite sheets loaded by Phaser
├── maps/         # Tiled source and exported JSON
├── manifest.json
└── license.json
```

Generated source art is never used as one full-room background. Runtime tooling extracts and
normalizes discrete character, tile, furniture, and decoration units.

## Registry

| Asset | Author | Source | License | Modification | Redistribution |
| --- | --- | --- | --- | --- | --- |
| `employee-concepts-v1.png` | OpenAI image generation, directed by Eidolon | Project generation on 2026-09-02 | Project-owned generated asset | Source sheet; runtime sprites are cropped, normalized, and palette-aligned | Allowed within Eidolon |
| `office-objects-concepts-v1.png` | OpenAI image generation, directed by Eidolon | Project generation on 2026-09-02 | Project-owned generated asset | Source sheet; runtime objects are cropped, normalized, and palette-aligned | Allowed within Eidolon |
| `employee-directional-v2.png` | OpenAI image generation, directed by Eidolon | Project generation on 2026-09-03 | Project-owned generated asset | Four-direction character poses; runtime sheet is cropped, normalized, and expanded into deterministic animation frames | Allowed within Eidolon |
| `office-layout-additions-v2.png` | OpenAI image generation, directed by Eidolon | Project generation on 2026-09-03 | Project-owned generated asset | Wall display, side bookshelf, entrance, corner plant, layered workstation, rug, and side cabinet source sheet | Allowed within Eidolon |

No external third-party pixel-art files are included in the default pack in this phase.

## Asset manifest contract

Every runtime entry declares:

- `id`, `type`, `version`
- `source`, `author`, `license`, optional `source_url`
- `tile_size` or `sprite_size`
- `runtime_path`
- animation frame names when applicable

The validator rejects missing provenance, inconsistent tile sizes, duplicate IDs, and paths that
escape the selected theme. Unknown optional assets fall back to the theme's declared defaults.

## Source prompts

### Employee source art

> Original cozy 2D pixel corporate RPG employee sheet; four office-worker archetypes; compact
> 32×48 logical proportions; separate idle, walk, typing, and meeting poses; top-down 3/4 view;
> warm restrained palette; transparent background; no text, logo, watermark, anti-aliasing, or
> commercial-game imitation.

### Office object source art

> Original grid-friendly 32×32 and 64×64 cozy corporate office assets; floors, walls, windows,
> desks, chairs, computers, meeting furniture, bookshelf, coffee machine, plants, whiteboard,
> server rack, printer, lamps, documents, bins, and cables; separate asset units on transparent
> background; warm restrained palette; no complete room, people, text, logo, watermark, or
> commercial-game imitation.

Both source images were generated with the built-in ImageGen workflow and copied into the
project's `eidolon-default/source` directory.

### Directional employee source art v2

> Same four employee identities in a strict four-row/eight-column sheet: idle down/up/side,
> walk down/up/side, and seated typing up/side. Larger readable 16-bit proportions; transparent
> background; no desk, monitor, text, watermark, or scenery baked into character frames.

### Office layout additions v2

> Separate 16-bit office props matching the default pack: blank wall display, narrow side
> bookshelf, bottom-wall entrance, corner planter, layerable workstation, blue rug, and side
> cabinet. Transparent background; no characters, logos, labels, watermark, floor, or wall.

The v2 source images were generated with the built-in ImageGen workflow and copied into the
same project source directory. The asset processor keeps source art immutable and rebuilds all
runtime outputs deterministically.
