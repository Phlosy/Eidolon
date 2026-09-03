import importlib.util
from pathlib import Path

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


def test_runtime_employee_frames_keep_complete_character_silhouettes() -> None:
    source = Image.open(SOURCE / "employee-directional-v2.png")

    sheet, _ = make_character_sheet(source)
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

    assert min(opaque_pixels_per_frame) >= 900
    assert runtime_sheet.size == sheet.size
    assert runtime_sheet.tobytes() == sheet.tobytes()
