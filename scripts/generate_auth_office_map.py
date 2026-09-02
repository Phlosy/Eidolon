"""Generate the compact Tiled-compatible authentication office map."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "apps/web/public/assets/office-game/eidolon-default/maps/office.json"
WIDTH = 30
HEIGHT = 16
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


def object_layer(layer_id: int, name: str, objects: list[dict[str, object]]) -> dict[str, object]:
    return {
        "id": layer_id,
        "name": name,
        "type": "objectgroup",
        "draworder": "topdown",
        "opacity": 1,
        "visible": True,
        "objects": objects,
    }


def prop(name: str, value: str) -> dict[str, str]:
    return {"name": name, "type": "string", "value": value}


def point(object_id: int, name: str, kind: str, zone: str, tile_x: int, tile_y: int, facing: str) -> dict[str, object]:
    return {
        "id": object_id,
        "name": name,
        "type": kind,
        "point": True,
        "x": tile_x * TILE + TILE // 2,
        "y": tile_y * TILE + TILE // 2,
        "width": 0,
        "height": 0,
        "properties": [prop("zone", zone), prop("facing", facing)],
    }


def decor(object_id: int, name: str, frame: str, x: int, y: int, depth_offset: int = 0) -> dict[str, object]:
    return {
        "id": object_id,
        "name": name,
        "type": "atlas-object",
        "x": x,
        "y": y,
        "width": 0,
        "height": 0,
        "properties": [prop("frame", frame), prop("depthOffset", str(depth_offset))],
    }


def zone(object_id: int, name: str, kind: str, x: int, y: int, width: int, height: int, capacity: int) -> dict[str, object]:
    return {
        "id": object_id,
        "name": name,
        "type": kind,
        "x": x * TILE,
        "y": y * TILE,
        "width": width * TILE,
        "height": height * TILE,
        "properties": [{"name": "capacity", "type": "int", "value": capacity}],
    }


def main() -> None:
    floor = [1 + ((x + y * 2) % 2) for y in range(HEIGHT) for x in range(WIDTH)]
    walls = [0] * (WIDTH * HEIGHT)
    foreground = [0] * (WIDTH * HEIGHT)
    lighting = [0] * (WIDTH * HEIGHT)
    for y in range(HEIGHT):
        for x in range(WIDTH):
            index = y * WIDTH + x
            if y < 2 or x == 0 or x == WIDTH - 1:
                walls[index] = 5
            if y == HEIGHT - 1:
                foreground[index] = 3
    for y in range(10, 15):
        for x in range(10, 18):
            floor[y * WIDTH + x] = 6 if (x + y) % 2 else 7
    for y in range(3, 9):
        for x in range(20, 28):
            floor[y * WIDTH + x] = 8

    furniture_back = [
        decor(100, "North window", "window", 168, 105, -80),
        decor(101, "Research window", "window", 370, 105, -80),
        decor(102, "Meeting window", "window", 675, 105, -80),
        decor(103, "Whiteboard", "whiteboard", 500, 116, -55),
        decor(104, "CEO bookshelf", "bookshelf", 60, 225, -40),
        decor(105, "Server rack", "server-rack", 875, 270, -30),
    ]
    furniture = [
        decor(120, "CEO desk", "desk", 178, 238),
        decor(121, "Engineering desk A", "desk", 405, 245),
        decor(122, "Engineering desk B", "desk", 565, 245),
        decor(123, "Meeting table", "meeting-table", 762, 250),
        decor(124, "Coffee station", "coffee-station", 188, 425),
        decor(125, "Lounge plant", "plant", 330, 432),
        decor(126, "Entrance plant", "plant", 824, 438),
        decor(127, "Entrance door", "door", 914, 455),
    ]
    furniture_front = [decor(140, "Foreground plant", "plant", 56, 474, 90)]

    interactions = [
        point(200, "entrance", "entrance", "entrance", 27, 13, "left"),
        point(201, "ceo-desk", "workstation", "ceo-office", 5, 7, "up"),
        point(202, "engineering-desk-a", "workstation", "engineering", 12, 7, "up"),
        point(203, "engineering-desk-b", "workstation", "engineering", 17, 7, "up"),
        point(204, "shared-desk", "workstation", "shared", 13, 12, "up"),
        point(205, "meeting-seat-a", "meeting-seat", "meeting-room", 22, 6, "right"),
        point(206, "meeting-seat-b", "meeting-seat", "meeting-room", 25, 6, "left"),
        point(207, "meeting-seat-c", "meeting-seat", "meeting-room", 22, 8, "right"),
        point(208, "meeting-seat-d", "meeting-seat", "meeting-room", 25, 8, "left"),
        point(209, "coffee-machine", "coffee", "lounge", 6, 13, "up"),
        point(210, "bookshelf", "bookshelf", "library", 3, 7, "left"),
        point(211, "whiteboard", "whiteboard", "engineering", 15, 4, "up"),
    ]
    zones = [
        zone(300, "Entrance", "ENTRANCE", 25, 10, 4, 5, 6),
        zone(301, "CEO Office", "CEO_OFFICE", 1, 2, 8, 8, 2),
        zone(302, "Engineering", "ENGINEERING", 9, 2, 11, 8, 8),
        zone(303, "Meeting Room", "MEETING_ROOM", 20, 2, 9, 8, 8),
        zone(304, "Lounge", "LOUNGE", 1, 10, 9, 5, 5),
        zone(305, "Library", "LIBRARY", 1, 3, 5, 7, 3),
        zone(306, "Shared Workspace", "SHARED_WORKSPACE", 10, 10, 14, 5, 10),
    ]

    office_map = {
        "compressionlevel": -1,
        "height": HEIGHT,
        "infinite": False,
        "layers": [
            tile_layer(1, "Floor", floor),
            tile_layer(2, "Walls", walls),
            object_layer(3, "Wall Decoration", furniture_back),
            object_layer(4, "Furniture Back", []),
            object_layer(5, "Furniture", furniture),
            object_layer(6, "Interaction", interactions),
            object_layer(7, "Zones", zones),
            object_layer(8, "Characters", []),
            object_layer(9, "Furniture Front", furniture_front),
            tile_layer(10, "Foreground", foreground),
            tile_layer(11, "Lighting Effects", lighting),
        ],
        "nextlayerid": 12,
        "nextobjectid": 400,
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
        "tileheight": TILE,
        "tilewidth": TILE,
        "type": "map",
        "version": "1.10",
        "width": WIDTH,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(office_map, indent=2) + "\n")


if __name__ == "__main__":
    main()
