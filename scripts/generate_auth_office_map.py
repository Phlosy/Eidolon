"""Generate the full-screen Tiled-compatible authentication office map."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "apps/web/public/assets/office-game/eidolon-default/maps/office.json"
WIDTH = 40
HEIGHT = 23
TILE = 32


def tile_layer(layer_id: int, name: str, data: list[int]) -> dict[str, object]:
    return {
        "id": layer_id,
        "name": name,
        "type": "tilelayer",
        "width": WIDTH,
        "height": HEIGHT,
        "x": 0,
        "y": 0,
        "opacity": 1,
        "visible": True,
        "data": data,
    }


def object_layer(
    layer_id: int, name: str, objects: list[dict[str, object]]
) -> dict[str, object]:
    return {
        "id": layer_id,
        "name": name,
        "type": "objectgroup",
        "draworder": "topdown",
        "opacity": 1,
        "visible": True,
        "objects": objects,
    }


def prop(name: str, value: str | int) -> dict[str, str | int]:
    return {
        "name": name,
        "type": "int" if isinstance(value, int) else "string",
        "value": value,
    }


def point(
    object_id: int,
    name: str,
    kind: str,
    zone_name: str,
    tile_x: int,
    tile_y: int,
    facing: str,
    character_depth_offset: int = 30,
) -> dict[str, object]:
    return {
        "id": object_id,
        "name": name,
        "type": kind,
        "point": True,
        "x": tile_x * TILE + TILE // 2,
        "y": tile_y * TILE + TILE // 2,
        "width": 0,
        "height": 0,
        "properties": [
            prop("zone", zone_name),
            prop("facing", facing),
            prop("characterDepthOffset", character_depth_offset),
        ],
    }


def decor(
    object_id: int,
    name: str,
    frame: str,
    x: int,
    y: int,
    depth_offset: int = 0,
) -> dict[str, object]:
    return {
        "id": object_id,
        "name": name,
        "type": "atlas-object",
        "x": x,
        "y": y,
        "width": 0,
        "height": 0,
        "properties": [prop("frame", frame), prop("depthOffset", depth_offset)],
    }


def zone(
    object_id: int,
    name: str,
    kind: str,
    x: int,
    y: int,
    width: int,
    height: int,
    capacity: int,
) -> dict[str, object]:
    return {
        "id": object_id,
        "name": name,
        "type": kind,
        "x": x * TILE,
        "y": y * TILE,
        "width": width * TILE,
        "height": height * TILE,
        "properties": [prop("capacity", capacity)],
    }


def collision(
    object_id: int, name: str, x: int, y: int, width: int, height: int
) -> dict[str, object]:
    return {
        "id": object_id,
        "name": name,
        "type": "collision",
        "x": x * TILE,
        "y": y * TILE,
        "width": width * TILE,
        "height": height * TILE,
    }


def main() -> None:
    floor = [1 + ((x + y * 2) % 2) for y in range(HEIGHT) for x in range(WIDTH)]
    walls = [0] * (WIDTH * HEIGHT)
    foreground = [0] * (WIDTH * HEIGHT)
    lighting = [0] * (WIDTH * HEIGHT)

    entrance_columns = {25, 26, 27}
    for y in range(HEIGHT):
        for x in range(WIDTH):
            index = y * WIDTH + x
            is_outer_wall = x in {0, WIDTH - 1} or y < 3 or y >= HEIGHT - 2
            is_entrance = y >= HEIGHT - 2 and x in entrance_columns
            if is_outer_wall and not is_entrance:
                walls[index] = 5
            if y == HEIGHT - 1 and x not in entrance_columns:
                foreground[index] = 3

    wall_decoration = [
        decor(100, "North window", "window", 150, 148),
        decor(101, "Company display", "company-display", 540, 150),
        decor(102, "Planning whiteboard", "whiteboard", 770, 148),
        decor(103, "Side bookshelf", "side-bookshelf", 58, 384, -35),
        decor(104, "Server rack", "server-rack", 812, 388, -20),
        decor(105, "Upper-right planter", "corner-plant", 1206, 192, -30),
    ]
    furniture_back = [
        decor(120, "CEO workstation back", "workstation-back", 238, 354),
        decor(121, "Engineering workstation A back", "workstation-back", 470, 354),
        decor(122, "Engineering workstation B back", "workstation-back", 702, 354),
    ]
    furniture = [
        decor(130, "Meeting rug", "blue-rug", 590, 668, -180),
        decor(131, "Meeting table", "meeting-table", 590, 626),
        decor(132, "Coffee station", "coffee-station", 735, 652),
        decor(133, "Side cabinet", "side-cabinet", 72, 526),
        decor(134, "Bottom entrance", "entrance-door", 846, 736, 40),
    ]
    furniture_front = [
        decor(140, "CEO workstation front", "workstation-front", 238, 354),
        decor(141, "Engineering workstation A front", "workstation-front", 470, 354),
        decor(142, "Engineering workstation B front", "workstation-front", 702, 354),
        decor(143, "Lower-left planter", "corner-plant", 74, 688, 90),
        decor(144, "Lower-right planter", "corner-plant", 1206, 688, 90),
    ]

    interactions = [
        point(200, "entrance", "entrance", "entrance", 26, 20, "up"),
        point(201, "ceo-desk", "workstation", "ceo-office", 6, 9, "right", -20),
        point(
            202, "engineering-desk-a", "workstation", "engineering", 13, 9, "right", -20
        ),
        point(
            203, "engineering-desk-b", "workstation", "engineering", 20, 9, "right", -20
        ),
        point(204, "shared-desk", "workstation", "shared", 23, 12, "down"),
        point(205, "meeting-seat-a", "meeting-seat", "meeting-room", 15, 16, "right"),
        point(206, "meeting-seat-b", "meeting-seat", "meeting-room", 21, 16, "left"),
        point(207, "meeting-seat-c", "meeting-seat", "meeting-room", 15, 19, "right"),
        point(208, "meeting-seat-d", "meeting-seat", "meeting-room", 21, 19, "left"),
        point(209, "coffee-machine", "coffee", "lounge", 23, 18, "up"),
        point(210, "bookshelf", "bookshelf", "library", 4, 9, "left"),
        point(211, "whiteboard", "whiteboard", "engineering", 22, 5, "up"),
    ]
    zones = [
        zone(300, "entrance", "ENTRANCE", 24, 18, 5, 3, 6),
        zone(301, "ceo-office", "CEO_OFFICE", 4, 5, 7, 8, 2),
        zone(302, "engineering", "ENGINEERING", 11, 5, 13, 8, 8),
        zone(303, "meeting-room", "MEETING_ROOM", 14, 14, 9, 7, 8),
        zone(304, "lounge", "LOUNGE", 21, 15, 5, 6, 5),
        zone(305, "library", "LIBRARY", 1, 5, 5, 9, 3),
        zone(306, "shared", "SHARED_WORKSPACE", 18, 5, 8, 9, 10),
    ]
    collisions = [
        collision(400, "CEO desk footprint", 5, 10, 5, 1),
        collision(401, "Engineering desk A footprint", 12, 10, 5, 1),
        collision(402, "Engineering desk B footprint", 19, 10, 5, 1),
        collision(403, "Meeting table footprint", 16, 17, 5, 2),
        collision(404, "Coffee station footprint", 21, 19, 5, 2),
        collision(405, "Bookshelf footprint", 1, 6, 3, 7),
        collision(406, "Side cabinet footprint", 1, 14, 3, 3),
        collision(407, "Server rack footprint", 24, 8, 3, 5),
    ]

    office_map = {
        "compressionlevel": -1,
        "height": HEIGHT,
        "infinite": False,
        "layers": [
            tile_layer(1, "Floor", floor),
            tile_layer(2, "Walls", walls),
            object_layer(3, "Wall Decoration", wall_decoration),
            object_layer(4, "Furniture Back", furniture_back),
            object_layer(5, "Furniture", furniture),
            object_layer(6, "Interaction", interactions),
            object_layer(7, "Zones", zones),
            object_layer(8, "Collision", collisions),
            object_layer(9, "Characters", []),
            object_layer(10, "Furniture Front", furniture_front),
            tile_layer(11, "Foreground", foreground),
            tile_layer(12, "Lighting Effects", lighting),
        ],
        "nextlayerid": 13,
        "nextobjectid": 500,
        "orientation": "orthogonal",
        "renderorder": "right-down",
        "tiledversion": "1.11.2",
        "tileheight": TILE,
        "tilesets": [
            {
                "columns": 8,
                "firstgid": 1,
                "image": "../runtime/office-tiles.png",
                "imageheight": 32,
                "imagewidth": 256,
                "margin": 0,
                "name": "office-tiles",
                "spacing": 0,
                "tilecount": 8,
                "tileheight": TILE,
                "tilewidth": TILE,
            }
        ],
        "tilewidth": TILE,
        "type": "map",
        "version": "1.10",
        "width": WIDTH,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(office_map, indent=2) + "\n")


if __name__ == "__main__":
    main()
