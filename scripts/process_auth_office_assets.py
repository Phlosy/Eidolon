"""Build the eidolon-default Phaser runtime pack from reviewed source sheets.

Run with apps/server/.venv/bin/python scripts/process_auth_office_assets.py.
The source sheets remain immutable; all output is reproducible under runtime/.
"""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path

from PIL import Image, ImageEnhance


ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "apps/web/public/assets/office-game/eidolon-default"
SOURCE = PACK / "source"
RUNTIME = PACK / "runtime"
FRAME = (32, 48)


def color_distance(a: tuple[int, ...], b: tuple[int, ...]) -> int:
    return sum(abs(a[index] - b[index]) for index in range(3))


def transparent_crop(image: Image.Image, box: tuple[int, int, int, int], threshold: int = 18) -> Image.Image:
    crop = image.crop(box).convert("RGBA")
    width, height = crop.size
    pixels = crop.load()
    pending = deque(
        [(x, 0) for x in range(width)]
        + [(x, height - 1) for x in range(width)]
        + [(0, y) for y in range(1, height - 1)]
        + [(width - 1, y) for y in range(1, height - 1)]
    )
    background: set[tuple[int, int]] = set(pending)

    while pending:
        x, y = pending.popleft()
        current = pixels[x, y]
        for next_x, next_y in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            point = (next_x, next_y)
            if not (0 <= next_x < width and 0 <= next_y < height) or point in background:
                continue
            if color_distance(current, pixels[next_x, next_y]) <= threshold:
                background.add(point)
                pending.append(point)

    for x, y in background:
        pixel = pixels[x, y]
        pixels[x, y] = (*pixel[:3], 0)

    bounds = crop.getbbox()
    return crop.crop(bounds) if bounds else crop


def fit_pixel(source: Image.Image, size: tuple[int, int], padding: int = 0) -> Image.Image:
    available = (max(1, size[0] - padding * 2), max(1, size[1] - padding * 2))
    scale = min(available[0] / source.width, available[1] / source.height)
    resized = source.resize(
        (max(1, round(source.width * scale)), max(1, round(source.height * scale))),
        Image.Resampling.NEAREST,
    )
    output = Image.new("RGBA", size)
    output.alpha_composite(resized, ((size[0] - resized.width) // 2, size[1] - padding - resized.height))
    return output


def make_character_sheet(source: Image.Image) -> tuple[Image.Image, dict[str, dict[str, int]]]:
    rows = [
        {"idle": (18, 24, 116, 252), "walkA": (404, 24, 493, 252), "walkB": (500, 24, 590, 252), "work": (922, 70, 1028, 252), "meeting": (1162, 24, 1268, 252)},
        {"idle": (18, 278, 116, 510), "walkA": (405, 275, 495, 510), "walkB": (500, 275, 590, 510), "work": (920, 320, 1026, 510), "meeting": (1160, 275, 1270, 510)},
        {"idle": (18, 526, 116, 758), "walkA": (405, 525, 495, 758), "walkB": (500, 525, 590, 758), "work": (920, 570, 1026, 758), "meeting": (1160, 525, 1270, 758)},
        {"idle": (18, 772, 116, 1010), "walkA": (405, 770, 495, 1010), "walkB": (500, 770, 590, 1010), "work": (920, 815, 1026, 1010), "meeting": (1160, 770, 1270, 1010)},
    ]
    sheet = Image.new("RGBA", (FRAME[0] * 16, FRAME[1] * len(rows)))
    registry: dict[str, dict[str, int]] = {}

    for row_index, poses in enumerate(rows):
        prepared = {
            name: fit_pixel(transparent_crop(source, box), FRAME, padding=2)
            for name, box in poses.items()
        }
        sequence = [
            prepared["idle"], prepared["idle"], prepared["idle"], prepared["idle"],
            prepared["walkA"], prepared["walkB"], prepared["walkA"], prepared["walkB"],
            prepared["work"], prepared["work"], prepared["work"], prepared["work"],
            prepared["meeting"], prepared["meeting"], prepared["meeting"], prepared["meeting"],
        ]
        for frame_index, frame in enumerate(sequence):
            if frame_index % 4 in (1, 3):
                shifted = Image.new("RGBA", FRAME)
                shifted.alpha_composite(frame, (0, -1))
                frame = shifted
            sheet.alpha_composite(frame, (frame_index * FRAME[0], row_index * FRAME[1]))
        registry[f"employee-{row_index}"] = {"row": row_index, "firstFrame": row_index * 16}

    return sheet, registry


def make_tiles(source: Image.Image) -> Image.Image:
    boxes = [
        (24, 18, 128, 120),
        (146, 18, 252, 120),
        (24, 132, 128, 236),
        (146, 132, 252, 236),
        (292, 48, 386, 180),
        (1174, 38, 1344, 174),
        (946, 38, 1114, 174),
        (1370, 35, 1518, 180),
    ]
    tiles = Image.new("RGBA", (32 * len(boxes), 32))
    for index, box in enumerate(boxes):
        tile = source.crop(box).convert("RGBA").resize((32, 32), Image.Resampling.NEAREST)
        tiles.alpha_composite(ImageEnhance.Color(tile).enhance(0.78), (index * 32, 0))
    return tiles


def make_object_atlas(source: Image.Image) -> tuple[Image.Image, dict[str, dict[str, int]]]:
    definitions = {
        "window": ((520, 16, 748, 216), (128, 96)),
        "door": ((772, 12, 910, 216), (64, 96)),
        "desk": ((14, 245, 250, 456), (128, 96)),
        "meeting-table": ((858, 206, 1214, 450), (192, 128)),
        "bookshelf": ((18, 622, 184, 846), (96, 128)),
        "coffee-station": ((382, 642, 588, 846), (128, 96)),
        "whiteboard": ((920, 636, 1156, 850), (128, 96)),
        "server-rack": ((1178, 610, 1324, 856), (64, 128)),
        "plant": ((1012, 456, 1204, 644), (96, 96)),
    }
    atlas = Image.new("RGBA", (512, 512))
    frames: dict[str, dict[str, int]] = {}
    cursor_x = cursor_y = row_height = 0

    for name, (box, size) in definitions.items():
        if cursor_x + size[0] > atlas.width:
            cursor_x = 0
            cursor_y += row_height
            row_height = 0
        item = fit_pixel(transparent_crop(source, box), size, padding=2)
        atlas.alpha_composite(item, (cursor_x, cursor_y))
        frames[name] = {"x": cursor_x, "y": cursor_y, "w": size[0], "h": size[1]}
        cursor_x += size[0]
        row_height = max(row_height, size[1])

    return atlas.crop((0, 0, atlas.width, cursor_y + row_height)), frames


def main() -> None:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    characters = Image.open(SOURCE / "employee-concepts-v1.png")
    objects = Image.open(SOURCE / "office-objects-concepts-v1.png")

    character_sheet, character_registry = make_character_sheet(characters)
    character_sheet.save(RUNTIME / "employees.png", optimize=True)

    make_tiles(objects).save(RUNTIME / "office-tiles.png", optimize=True)
    object_atlas, frames = make_object_atlas(objects)
    object_atlas.save(RUNTIME / "office-objects.png", optimize=True)

    atlas = {
        "frames": {
            name: {
                "frame": frame,
                "rotated": False,
                "trimmed": False,
                "spriteSourceSize": {"x": 0, "y": 0, "w": frame["w"], "h": frame["h"]},
                "sourceSize": {"w": frame["w"], "h": frame["h"]},
            }
            for name, frame in frames.items()
        },
        "meta": {"image": "office-objects.png", "scale": "1", "format": "RGBA8888"},
    }
    (RUNTIME / "office-objects.json").write_text(json.dumps(atlas, indent=2) + "\n")
    (RUNTIME / "employee-frames.json").write_text(json.dumps(character_registry, indent=2) + "\n")


if __name__ == "__main__":
    main()
