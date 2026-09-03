import importlib.util
from itertools import pairwise
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "apps/web/public/assets/office-game/eidolon-default/source"
RUNTIME = ROOT / "apps/web/public/assets/office-game/eidolon-default/runtime"
PROCESSOR_PATH = ROOT / "scripts/process_auth_office_assets.py"

PROCESSOR_SPEC = importlib.util.spec_from_file_location(
    "process_auth_office_assets", PROCESSOR_PATH
)
assert PROCESSOR_SPEC is not None and PROCESSOR_SPEC.loader is not None
PROCESSOR = importlib.util.module_from_spec(PROCESSOR_SPEC)
PROCESSOR_SPEC.loader.exec_module(PROCESSOR)
make_object_atlas = PROCESSOR.make_object_atlas
make_character_sheet = PROCESSOR.make_character_sheet
make_tiles = PROCESSOR.make_tiles
FRAME = PROCESSOR.FRAME
EMPLOYEE_FRAMES_PER_ROW = PROCESSOR.EMPLOYEE_FRAMES_PER_ROW
EMPLOYEE_ANIMATION_LAYOUT = PROCESSOR.EMPLOYEE_ANIMATION_LAYOUT


@pytest.fixture(scope="module")
def generated_character_sheet():
    with (
        Image.open(SOURCE / "employee-directional-v2.png") as source,
        Image.open(SOURCE / "employee-walk-cycles-v3.png") as walk_source,
    ):
        return make_character_sheet(source, walk_source)


def test_checked_in_office_tiles_match_binary_pipeline() -> None:
    source = Image.new("RGBA", (1536, 1024), (120, 80, 40, 200))
    synthetic_tiles = make_tiles(source)
    generated_tiles = make_tiles(Image.open(SOURCE / "office-objects-concepts-v1.png"))
    runtime_tiles = Image.open(RUNTIME / "office-tiles.png")

    alpha_values = {
        value for value, count in enumerate(synthetic_tiles.getchannel("A").histogram()) if count
    }
    assert alpha_values <= {0, 255}
    assert runtime_tiles.size == generated_tiles.size
    assert runtime_tiles.tobytes() == generated_tiles.tobytes()


def test_runtime_atlas_preserves_opaque_workstation_details() -> None:
    objects = Image.open(SOURCE / "office-objects-concepts-v1.png")
    additions = Image.open(SOURCE / "office-layout-additions-v2.png")

    atlas, frames = make_object_atlas(objects, additions)
    runtime_atlas = Image.open(RUNTIME / "office-objects.png")
    workstation = frames["workstation-back"]

    monitor_screen = (workstation["x"] + 80, workstation["y"] + 25)
    lamp_shade = (workstation["x"] + 128, workstation["y"] + 28)

    assert atlas.getpixel(monitor_screen)[3] == 255
    assert atlas.getpixel(lamp_shade)[3] == 255
    assert runtime_atlas.size == atlas.size
    assert runtime_atlas.tobytes() == atlas.tobytes()


def test_runtime_employee_frames_keep_complete_character_silhouettes(
    generated_character_sheet,
) -> None:
    sheet, registry = generated_character_sheet
    runtime_sheet = Image.open(RUNTIME / "employees.png")
    opaque_pixels_per_frame = [
        sheet.crop(
            (
                column * FRAME[0],
                row * FRAME[1],
                (column + 1) * FRAME[0],
                (row + 1) * FRAME[1],
            )
        )
        .getchannel("A")
        .histogram()[255]
        for row in range(PROCESSOR.EMPLOYEE_ROWS)
        for column in range(sheet.width // FRAME[0])
    ]

    assert min(opaque_pixels_per_frame) >= 500
    assert sheet.width == FRAME[0] * EMPLOYEE_FRAMES_PER_ROW
    assert {entry["frameCount"] for entry in registry.values()} == {EMPLOYEE_FRAMES_PER_ROW}
    assert runtime_sheet.size == sheet.size
    assert runtime_sheet.tobytes() == sheet.tobytes()


def test_runtime_employee_walks_use_distinct_continuous_directional_frames(
    generated_character_sheet,
) -> None:
    sheet, _ = generated_character_sheet
    for row in range(PROCESSOR.EMPLOYEE_ROWS):
        for direction in ("walk-down", "walk-up", "walk-side"):
            start, count = EMPLOYEE_ANIMATION_LAYOUT[direction]
            frames = [
                sheet.crop(
                    (
                        column * FRAME[0],
                        row * FRAME[1],
                        (column + 1) * FRAME[0],
                        (row + 1) * FRAME[1],
                    )
                )
                for column in range(start, start + count)
            ]
            frame_bytes = [frame.tobytes() for frame in frames]
            assert len(set(frame_bytes)) >= 5
            assert len({frame.getbbox()[3] for frame in frames}) == 1
            assert all(
                current != following
                for current, following in pairwise(frame_bytes + frame_bytes[:1])
            )


def test_runtime_employee_edges_have_no_light_matte_or_transparent_rgb(
    generated_character_sheet,
) -> None:
    sheet, _ = generated_character_sheet
    pixels = sheet.load()

    for y in range(sheet.height):
        for x in range(sheet.width):
            red, green, blue, alpha = pixels[x, y]
            if alpha == 0:
                assert (red, green, blue) == (0, 0, 0), (x, y)
                continue
            neighbors = (
                (x - 1, y),
                (x + 1, y),
                (x, y - 1),
                (x, y + 1),
            )
            touches_transparency = any(
                not (0 <= next_x < sheet.width and 0 <= next_y < sheet.height)
                or pixels[next_x, next_y][3] == 0
                for next_x, next_y in neighbors
            )
            is_light_neutral = (
                max(red, green, blue) - min(red, green, blue) <= 18
                and (red + green + blue) / 3 >= 218
            )
            assert not (touches_transparency and is_light_neutral), (x, y)
