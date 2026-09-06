"""Build the eidolon-default Phaser runtime pack from reviewed source sheets.

Run with the project conda env:
    conda run -n eidolon python scripts/process_auth_office_assets.py
The source sheets remain immutable; all output is reproducible under runtime/.
"""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path

from PIL import Image, ImageEnhance, ImageOps

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "apps/web/public/assets/office-game/eidolon-default"
SOURCE = PACK / "source"
RUNTIME = PACK / "runtime"
FRAME = (48, 64)
POSE_COLUMNS = 8
EMPLOYEE_ROWS = 4
WALK_POSE_COUNTS = (6, 5, 5)
EMPLOYEE_ANIMATION_LAYOUT = {
    "idle-down": (0, 4),
    "idle-up": (4, 4),
    "idle-side": (8, 4),
    "walk-down": (12, 10),
    "walk-up": (22, 8),
    "walk-side": (30, 8),
    "work-down": (38, 4),
    "work-side": (42, 4),
}
EMPLOYEE_FRAMES_PER_ROW = max(
    start + count for start, count in EMPLOYEE_ANIMATION_LAYOUT.values()
)


def color_distance(a: tuple[int, ...], b: tuple[int, ...]) -> int:
    return sum(abs(a[index] - b[index]) for index in range(3))


def transparent_background(
    image: Image.Image,
    threshold: int = 18,
    *,
    fixed_background: bool = False,
) -> Image.Image:
    output = image.convert("RGBA")
    width, height = output.size
    pixels = output.load()
    pending = deque(
        [(x, 0) for x in range(width)]
        + [(x, height - 1) for x in range(width)]
        + [(0, y) for y in range(1, height - 1)]
        + [(width - 1, y) for y in range(1, height - 1)]
    )
    background: set[tuple[int, int]] = set(pending)
    background_reference = pixels[0, 0]

    while pending:
        x, y = pending.popleft()
        current = pixels[x, y]
        for next_x, next_y in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            point = (next_x, next_y)
            if (
                not (0 <= next_x < width and 0 <= next_y < height)
                or point in background
            ):
                continue
            reference = background_reference if fixed_background else current
            if color_distance(reference, pixels[next_x, next_y]) <= threshold:
                background.add(point)
                pending.append(point)

    for x, y in background:
        pixels[x, y] = (0, 0, 0, 0)

    return output


def transparent_crop(
    image: Image.Image,
    box: tuple[int, int, int, int],
    threshold: int = 18,
    *,
    fixed_background: bool = False,
) -> Image.Image:
    crop = transparent_background(
        image.crop(box), threshold=threshold, fixed_background=fixed_background
    )

    bounds = crop.getbbox()
    return crop.crop(bounds) if bounds else crop


def alpha_crop(image: Image.Image, box: tuple[int, int, int, int]) -> Image.Image:
    """Crop generated art using its embedded alpha mask without erasing interior detail."""
    crop = image.crop(box).convert("RGBA")
    bounds = crop.getbbox()
    return crop.crop(bounds) if bounds else crop


def fit_pixel(
    source: Image.Image, size: tuple[int, int], padding: int = 0
) -> Image.Image:
    available = (max(1, size[0] - padding * 2), max(1, size[1] - padding * 2))
    scale = min(available[0] / source.width, available[1] / source.height)
    resized = source.resize(
        (max(1, round(source.width * scale)), max(1, round(source.height * scale))),
        Image.Resampling.NEAREST,
    )
    output = Image.new("RGBA", size)
    output.alpha_composite(
        resized, ((size[0] - resized.width) // 2, size[1] - padding - resized.height)
    )
    return output


def keep_rows(source: Image.Image, start: int, end: int) -> Image.Image:
    output = Image.new("RGBA", source.size)
    output.alpha_composite(source.crop((0, start, source.width, end)), (0, start))
    return output


def quantize_pixel_alpha(
    source: Image.Image, cutoff: int = 128, *, clear_transparent_rgb: bool = False
) -> Image.Image:
    """Make generated pixel art fully opaque or transparent, with no ghost pixels."""
    output = source.copy().convert("RGBA")
    alpha = output.getchannel("A").point(lambda value: 0 if value <= cutoff else 255)
    output.putalpha(alpha)
    if clear_transparent_rgb:
        output.paste((0, 0, 0, 0), mask=ImageOps.invert(alpha))
    return output


def remove_light_edge_matte(
    source: Image.Image,
    *,
    minimum_lightness: int = 218,
    maximum_chroma: int = 18,
    passes: int = 3,
) -> Image.Image:
    """Remove border-connected neutral pixels left by a flattened checkerboard matte."""
    output = quantize_pixel_alpha(source, clear_transparent_rgb=True)
    width, height = output.size

    for _ in range(passes):
        pixels = output.load()
        removals: list[tuple[int, int]] = []
        for y in range(height):
            for x in range(width):
                red, green, blue, alpha = pixels[x, y]
                if alpha == 0:
                    continue
                touches_transparency = any(
                    not (0 <= next_x < width and 0 <= next_y < height)
                    or pixels[next_x, next_y][3] == 0
                    for next_x, next_y in (
                        (x - 1, y),
                        (x + 1, y),
                        (x, y - 1),
                        (x, y + 1),
                    )
                )
                is_light_neutral = (
                    max(red, green, blue) - min(red, green, blue) <= maximum_chroma
                    and (red + green + blue) / 3 >= minimum_lightness
                )
                if touches_transparency and is_light_neutral:
                    removals.append((x, y))
        if not removals:
            break
        for x, y in removals:
            pixels[x, y] = (0, 0, 0, 0)

    return output


def assert_binary_alpha(source: Image.Image, name: str) -> None:
    partial_pixels = sum(source.getchannel("A").histogram()[1:255])
    if partial_pixels:
        raise ValueError(
            f"{name} contains {partial_pixels} partially transparent pixels"
        )


def animation_frames(
    pose: Image.Image, *, mirror_stride: bool = False
) -> list[Image.Image]:
    frames: list[Image.Image] = []
    for index in range(4):
        frame = ImageOps.mirror(pose) if mirror_stride and index in (1, 3) else pose
        if index in (1, 3):
            shifted = Image.new("RGBA", FRAME)
            shifted.alpha_composite(frame, (0, -1))
            frame = shifted
        frames.append(frame)
    return frames


def projection_runs(
    image: Image.Image, *, axis: str, minimum_ink: int = 8
) -> list[tuple[int, int]]:
    alpha = image.getchannel("A")
    length = image.width if axis == "x" else image.height
    counts: list[int] = []
    for index in range(length):
        line = (
            alpha.crop((index, 0, index + 1, image.height))
            if axis == "x"
            else alpha.crop((0, index, image.width, index + 1))
        )
        counts.append(line.histogram()[255])

    runs: list[tuple[int, int]] = []
    start: int | None = None
    for index, active in enumerate(
        [count >= minimum_ink for count in counts] + [False]
    ):
        if active and start is None:
            start = index
        elif not active and start is not None:
            runs.append((start, index))
            start = None
    return runs


def fit_walk_poses(poses: list[Image.Image]) -> list[Image.Image]:
    available_width = FRAME[0] - 4
    available_height = FRAME[1] - 4
    scale = min(
        available_width / max(pose.width for pose in poses),
        available_height / max(pose.height for pose in poses),
    )
    fitted: list[Image.Image] = []
    for pose in poses:
        resized = pose.resize(
            (max(1, round(pose.width * scale)), max(1, round(pose.height * scale))),
            Image.Resampling.NEAREST,
        )
        frame = Image.new("RGBA", FRAME)
        frame.alpha_composite(
            resized,
            ((FRAME[0] - resized.width) // 2, FRAME[1] - 2 - resized.height),
        )
        fitted.append(remove_light_edge_matte(frame))
    return fitted


def extract_walk_poses(source: Image.Image) -> list[list[Image.Image]]:
    foreground = remove_light_edge_matte(
        transparent_background(source, threshold=72, fixed_background=True)
    )
    column_runs = projection_runs(foreground, axis="x")
    row_runs = projection_runs(foreground, axis="y")
    expected_columns = sum(WALK_POSE_COUNTS)
    if len(column_runs) != expected_columns or len(row_runs) != EMPLOYEE_ROWS:
        raise ValueError(
            "walk-cycle sheet must contain "
            f"{expected_columns} columns and {EMPLOYEE_ROWS} rows; "
            f"found {len(column_runs)} columns and {len(row_runs)} rows"
        )

    rows: list[list[Image.Image]] = []
    for row_start, row_end in row_runs:
        raw_poses = []
        for column_start, column_end in column_runs:
            box = (
                max(0, column_start - 5),
                max(0, row_start - 5),
                min(foreground.width, column_end + 5),
                min(foreground.height, row_end + 5),
            )
            raw_poses.append(alpha_crop(foreground, box))
        rows.append(fit_walk_poses(raw_poses))
    return rows


def ping_pong_cycle(poses: list[Image.Image]) -> list[Image.Image]:
    if len(poses) < 3:
        raise ValueError("walk cycles require at least three distinct key poses")
    return poses + poses[-2:0:-1]


def make_character_sheet(
    source: Image.Image,
    walk_source: Image.Image,
) -> tuple[Image.Image, dict[str, dict[str, int]]]:
    cell_width = source.width // POSE_COLUMNS
    cell_height = source.height // EMPLOYEE_ROWS
    sheet = Image.new(
        "RGBA", (FRAME[0] * EMPLOYEE_FRAMES_PER_ROW, FRAME[1] * EMPLOYEE_ROWS)
    )
    registry: dict[str, dict[str, int]] = {}
    walk_rows = extract_walk_poses(walk_source)

    for row_index in range(EMPLOYEE_ROWS):
        poses: list[Image.Image] = []
        for column in range(POSE_COLUMNS):
            box = (
                column * cell_width + 8,
                row_index * cell_height + 8,
                (column + 1) * cell_width - 8,
                (row_index + 1) * cell_height - 8,
            )
            poses.append(
                remove_light_edge_matte(
                    fit_pixel(
                        transparent_crop(
                            source, box, threshold=72, fixed_background=True
                        ),
                        FRAME,
                        padding=2,
                    )
                )
            )

        down_count, up_count, side_count = WALK_POSE_COUNTS
        walk_poses = walk_rows[row_index]
        sequence = (
            animation_frames(poses[0])
            + animation_frames(poses[1])
            + animation_frames(poses[2])
            + ping_pong_cycle(walk_poses[:down_count])
            + ping_pong_cycle(walk_poses[down_count : down_count + up_count])
            + ping_pong_cycle(walk_poses[-side_count:])
            + animation_frames(poses[6])
            + animation_frames(poses[7])
        )
        if len(sequence) != EMPLOYEE_FRAMES_PER_ROW:
            raise ValueError(
                f"employee row generated {len(sequence)} frames; "
                f"expected {EMPLOYEE_FRAMES_PER_ROW}"
            )
        for frame_index, frame in enumerate(sequence):
            sheet.alpha_composite(frame, (frame_index * FRAME[0], row_index * FRAME[1]))
        registry[f"employee-{row_index}"] = {
            "row": row_index,
            "firstFrame": row_index * EMPLOYEE_FRAMES_PER_ROW,
            "frameCount": EMPLOYEE_FRAMES_PER_ROW,
        }

    return remove_light_edge_matte(sheet), registry


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
        tile = (
            source.crop(box).convert("RGBA").resize((32, 32), Image.Resampling.NEAREST)
        )
        tiles.alpha_composite(ImageEnhance.Color(tile).enhance(0.78), (index * 32, 0))
    return quantize_pixel_alpha(tiles)


def make_object_atlas(
    source: Image.Image, additions: Image.Image
) -> tuple[Image.Image, dict[str, dict[str, int]]]:
    definitions = {
        "window": ((520, 16, 748, 216), (128, 96)),
        "meeting-table": ((858, 206, 1214, 450), (192, 128)),
        "coffee-station": ((382, 642, 552, 846), (128, 96)),
        "whiteboard": ((920, 636, 1156, 850), (128, 96)),
        "server-rack": ((1178, 610, 1324, 856), (64, 128)),
        "plant": ((1012, 456, 1204, 644), (96, 96)),
    }
    addition_definitions = {
        "company-display": ((32, 126, 476, 396), (320, 128)),
        "side-bookshelf": ((548, 48, 748, 520), (96, 192)),
        "entrance-door": ((830, 60, 1124, 504), (128, 192)),
        "corner-plant": ((1194, 106, 1514, 504), (128, 128)),
        "blue-rug": ((846, 646, 1296, 922), (256, 160)),
        "side-cabinet": ((1322, 548, 1528, 930), (96, 128)),
    }
    atlas = Image.new("RGBA", (1024, 768))
    frames: dict[str, dict[str, int]] = {}
    cursor_x = cursor_y = row_height = 0

    for name, (box, size) in definitions.items():
        if cursor_x + size[0] > atlas.width:
            cursor_x = 0
            cursor_y += row_height
            row_height = 0
        item = quantize_pixel_alpha(
            fit_pixel(transparent_crop(source, box), size, padding=2)
        )
        atlas.alpha_composite(item, (cursor_x, cursor_y))
        frames[name] = {"x": cursor_x, "y": cursor_y, "w": size[0], "h": size[1]}
        cursor_x += size[0]
        row_height = max(row_height, size[1])

    addition_items = {
        name: quantize_pixel_alpha(
            fit_pixel(alpha_crop(additions, box), size, padding=2)
        )
        for name, (box, size) in addition_definitions.items()
    }
    workstation = quantize_pixel_alpha(
        fit_pixel(
            alpha_crop(additions, (26, 528, 446, 920)),
            (192, 128),
            padding=2,
        )
    )
    addition_items["workstation-back"] = keep_rows(workstation, 0, 70)
    addition_items["workstation-front"] = keep_rows(workstation, 74, workstation.height)

    for name, item in addition_items.items():
        size = item.size
        if cursor_x + size[0] > atlas.width:
            cursor_x = 0
            cursor_y += row_height
            row_height = 0
        atlas.alpha_composite(item, (cursor_x, cursor_y))
        frames[name] = {"x": cursor_x, "y": cursor_y, "w": size[0], "h": size[1]}
        cursor_x += size[0]
        row_height = max(row_height, size[1])

    return atlas.crop((0, 0, atlas.width, cursor_y + row_height)), frames


def main() -> None:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    characters = Image.open(SOURCE / "employee-directional-v2.png")
    walk_cycles = Image.open(SOURCE / "employee-walk-cycles-v3.png")
    objects = Image.open(SOURCE / "office-objects-concepts-v1.png")
    additions = Image.open(SOURCE / "office-layout-additions-v2.png")

    character_sheet, character_registry = make_character_sheet(characters, walk_cycles)
    assert_binary_alpha(character_sheet, "employee sprite sheet")
    character_sheet.save(RUNTIME / "employees.png", optimize=True)

    tiles = make_tiles(objects)
    assert_binary_alpha(tiles, "office tileset")
    tiles.save(RUNTIME / "office-tiles.png", optimize=True)
    object_atlas, frames = make_object_atlas(objects, additions)
    assert_binary_alpha(object_atlas, "office object atlas")
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
    (RUNTIME / "employee-frames.json").write_text(
        json.dumps(character_registry, indent=2) + "\n"
    )


if __name__ == "__main__":
    main()
