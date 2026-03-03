"""
Home configuration store.

Manages the current home configuration (rooms, devices, residents) in memory.
Provides built-in templates and room types. Device type catalog comes
exclusively from S5-HES-Agent (118 types with behavioral implementations).
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional

from loguru import logger


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Room:
    id: str
    name: str
    room_type: str
    area: float = 20.0
    floor: int = 0
    x: float = 0
    y: float = 0
    width: float = 140
    height: float = 100
    devices: list[str] = field(default_factory=list)


@dataclass
class Device:
    id: str
    name: str
    device_type: str
    room_id: str
    agent_managed: bool = True
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass
class Resident:
    id: str
    name: str
    resident_type: str = "adult"
    age: int = 30
    schedule: dict[str, Any] = field(default_factory=dict)


@dataclass
class HomeConfig:
    home_id: str
    home_name: str
    template: str = "custom"
    rooms: list[Room] = field(default_factory=list)
    devices: list[Device] = field(default_factory=list)
    residents: list[Resident] = field(default_factory=list)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


# ---------------------------------------------------------------------------
# Built-in catalogs
# ---------------------------------------------------------------------------

# Room types -- HES RoomType enum (15) + ABC extensions (4)
ROOM_TYPES: list[str] = [
    # HES RoomType enum
    "living_room", "bedroom", "master_bedroom", "kitchen", "bathroom",
    "office", "garage", "hallway", "entrance", "dining_room",
    "basement", "attic", "laundry", "garden", "balcony",
    # ABC extensions (used in ABC templates)
    "patio", "nursery", "guest_room", "media_room",
]

# 6 built-in templates
BUILTIN_TEMPLATES: dict[str, dict[str, Any]] = {
    "studio_apartment": {
        "name": "Studio Apartment",
        "description": "Compact single-room layout with kitchenette",
        "rooms": [
            {"name": "Main Room", "room_type": "living_room", "area": 30.0},
            {"name": "Bathroom", "room_type": "bathroom", "area": 5.0},
            {"name": "Kitchenette", "room_type": "kitchen", "area": 8.0},
        ],
        "default_devices": [
            {"device_type": "thermostat", "room": "Main Room"},
            {"device_type": "smart_light", "room": "Main Room"},
            {"device_type": "smart_lock", "room": "Main Room"},
            {"device_type": "motion_sensor", "room": "Main Room"},
            {"device_type": "smoke_detector", "room": "Kitchenette"},
        ],
    },
    "one_bedroom": {
        "name": "1-Bedroom Apartment",
        "description": "Standard one-bedroom layout",
        "rooms": [
            {"name": "Living Room", "room_type": "living_room", "area": 25.0},
            {"name": "Bedroom", "room_type": "bedroom", "area": 15.0},
            {"name": "Kitchen", "room_type": "kitchen", "area": 10.0},
            {"name": "Bathroom", "room_type": "bathroom", "area": 6.0},
            {"name": "Hallway", "room_type": "hallway", "area": 5.0},
        ],
        "default_devices": [
            {"device_type": "thermostat", "room": "Living Room"},
            {"device_type": "smart_light", "room": "Living Room"},
            {"device_type": "smart_light", "room": "Bedroom"},
            {"device_type": "smart_lock", "room": "Hallway"},
            {"device_type": "motion_sensor", "room": "Hallway"},
            {"device_type": "smoke_detector", "room": "Kitchen"},
            {"device_type": "security_camera", "room": "Hallway"},
        ],
    },
    "two_bedroom": {
        "name": "2-Bedroom Apartment",
        "description": "Two-bedroom layout for couples or small families",
        "rooms": [
            {"name": "Living Room", "room_type": "living_room", "area": 30.0},
            {"name": "Master Bedroom", "room_type": "bedroom", "area": 18.0},
            {"name": "Second Bedroom", "room_type": "bedroom", "area": 14.0},
            {"name": "Kitchen", "room_type": "kitchen", "area": 12.0},
            {"name": "Bathroom", "room_type": "bathroom", "area": 7.0},
            {"name": "Hallway", "room_type": "hallway", "area": 6.0},
        ],
        "default_devices": [
            {"device_type": "thermostat", "room": "Living Room"},
            {"device_type": "smart_light", "room": "Living Room"},
            {"device_type": "smart_light", "room": "Master Bedroom"},
            {"device_type": "smart_light", "room": "Second Bedroom"},
            {"device_type": "smart_lock", "room": "Hallway"},
            {"device_type": "motion_sensor", "room": "Hallway"},
            {"device_type": "motion_sensor", "room": "Living Room"},
            {"device_type": "smoke_detector", "room": "Kitchen"},
            {"device_type": "co_detector", "room": "Kitchen"},
            {"device_type": "security_camera", "room": "Hallway"},
        ],
    },
    "three_bedroom": {
        "name": "3-Bedroom Home",
        "description": "Three-bedroom house for families",
        "rooms": [
            {"name": "Living Room", "room_type": "living_room", "area": 35.0},
            {"name": "Master Bedroom", "room_type": "bedroom", "area": 20.0},
            {"name": "Bedroom 2", "room_type": "bedroom", "area": 15.0},
            {"name": "Bedroom 3", "room_type": "bedroom", "area": 14.0},
            {"name": "Kitchen", "room_type": "kitchen", "area": 15.0},
            {"name": "Bathroom 1", "room_type": "bathroom", "area": 8.0},
            {"name": "Bathroom 2", "room_type": "bathroom", "area": 6.0},
            {"name": "Hallway", "room_type": "hallway", "area": 8.0},
            {"name": "Garage", "room_type": "garage", "area": 25.0},
        ],
        "default_devices": [
            {"device_type": "thermostat", "room": "Living Room"},
            {"device_type": "hvac_controller", "room": "Living Room"},
            {"device_type": "smart_light", "room": "Living Room"},
            {"device_type": "smart_light", "room": "Master Bedroom"},
            {"device_type": "smart_light", "room": "Bedroom 2"},
            {"device_type": "smart_light", "room": "Bedroom 3"},
            {"device_type": "smart_light", "room": "Kitchen"},
            {"device_type": "smart_lock", "room": "Hallway"},
            {"device_type": "smart_lock", "room": "Garage"},
            {"device_type": "motion_sensor", "room": "Hallway"},
            {"device_type": "motion_sensor", "room": "Garage"},
            {"device_type": "smoke_detector", "room": "Kitchen"},
            {"device_type": "co_detector", "room": "Kitchen"},
            {"device_type": "security_camera", "room": "Hallway"},
            {"device_type": "security_camera", "room": "Garage"},
        ],
    },
    "family_house": {
        "name": "Family Home",
        "description": "Large family home with nursery and full appliances",
        "rooms": [
            {"name": "Living Room", "room_type": "living_room", "area": 40.0},
            {"name": "Master Bedroom", "room_type": "bedroom", "area": 22.0},
            {"name": "Kids Room", "room_type": "bedroom", "area": 16.0},
            {"name": "Nursery", "room_type": "nursery", "area": 12.0},
            {"name": "Guest Room", "room_type": "guest_room", "area": 14.0},
            {"name": "Kitchen", "room_type": "kitchen", "area": 18.0},
            {"name": "Dining Room", "room_type": "dining_room", "area": 15.0},
            {"name": "Bathroom 1", "room_type": "bathroom", "area": 9.0},
            {"name": "Bathroom 2", "room_type": "bathroom", "area": 6.0},
            {"name": "Hallway", "room_type": "hallway", "area": 10.0},
            {"name": "Garage", "room_type": "garage", "area": 30.0},
            {"name": "Laundry", "room_type": "laundry", "area": 8.0},
        ],
        "default_devices": [
            {"device_type": "thermostat", "room": "Living Room"},
            {"device_type": "thermostat", "room": "Master Bedroom"},
            {"device_type": "hvac_controller", "room": "Living Room"},
            {"device_type": "smart_light", "room": "Living Room"},
            {"device_type": "smart_light", "room": "Master Bedroom"},
            {"device_type": "smart_light", "room": "Kids Room"},
            {"device_type": "smart_light", "room": "Nursery"},
            {"device_type": "smart_light", "room": "Kitchen"},
            {"device_type": "smart_light", "room": "Dining Room"},
            {"device_type": "smart_lock", "room": "Hallway"},
            {"device_type": "smart_lock", "room": "Garage"},
            {"device_type": "motion_sensor", "room": "Hallway"},
            {"device_type": "motion_sensor", "room": "Nursery"},
            {"device_type": "motion_sensor", "room": "Garage"},
            {"device_type": "smoke_detector", "room": "Kitchen"},
            {"device_type": "smoke_detector", "room": "Nursery"},
            {"device_type": "co_detector", "room": "Kitchen"},
            {"device_type": "security_camera", "room": "Hallway"},
            {"device_type": "security_camera", "room": "Garage"},
            {"device_type": "smart_plug", "room": "Kitchen"},
            {"device_type": "smart_washer", "room": "Laundry"},
        ],
    },
    "smart_mansion": {
        "name": "Smart Mansion",
        "description": "Luxury estate with full smart home coverage",
        "rooms": [
            {"name": "Grand Living Room", "room_type": "living_room", "area": 60.0},
            {"name": "Master Suite", "room_type": "bedroom", "area": 30.0},
            {"name": "Bedroom 2", "room_type": "bedroom", "area": 20.0},
            {"name": "Bedroom 3", "room_type": "bedroom", "area": 20.0},
            {"name": "Bedroom 4", "room_type": "bedroom", "area": 18.0},
            {"name": "Guest Suite", "room_type": "guest_room", "area": 22.0},
            {"name": "Kitchen", "room_type": "kitchen", "area": 25.0},
            {"name": "Dining Room", "room_type": "dining_room", "area": 20.0},
            {"name": "Media Room", "room_type": "media_room", "area": 25.0},
            {"name": "Office", "room_type": "office", "area": 18.0},
            {"name": "Bathroom 1", "room_type": "bathroom", "area": 12.0},
            {"name": "Bathroom 2", "room_type": "bathroom", "area": 8.0},
            {"name": "Bathroom 3", "room_type": "bathroom", "area": 8.0},
            {"name": "Hallway", "room_type": "hallway", "area": 15.0},
            {"name": "Garage", "room_type": "garage", "area": 50.0},
            {"name": "Basement", "room_type": "basement", "area": 40.0},
            {"name": "Patio", "room_type": "patio", "area": 35.0},
            {"name": "Laundry", "room_type": "laundry", "area": 10.0},
        ],
        "default_devices": [
            {"device_type": "thermostat", "room": "Grand Living Room"},
            {"device_type": "thermostat", "room": "Master Suite"},
            {"device_type": "thermostat", "room": "Guest Suite"},
            {"device_type": "hvac_controller", "room": "Grand Living Room"},
            {"device_type": "hvac_controller", "room": "Basement"},
            {"device_type": "smart_light", "room": "Grand Living Room"},
            {"device_type": "smart_light", "room": "Master Suite"},
            {"device_type": "smart_light", "room": "Bedroom 2"},
            {"device_type": "smart_light", "room": "Bedroom 3"},
            {"device_type": "smart_light", "room": "Bedroom 4"},
            {"device_type": "smart_light", "room": "Guest Suite"},
            {"device_type": "smart_light", "room": "Kitchen"},
            {"device_type": "smart_light", "room": "Dining Room"},
            {"device_type": "smart_light", "room": "Media Room"},
            {"device_type": "smart_light", "room": "Office"},
            {"device_type": "smart_light", "room": "Patio"},
            {"device_type": "smart_lock", "room": "Hallway"},
            {"device_type": "smart_lock", "room": "Garage"},
            {"device_type": "smart_lock", "room": "Office"},
            {"device_type": "motion_sensor", "room": "Hallway"},
            {"device_type": "motion_sensor", "room": "Garage"},
            {"device_type": "motion_sensor", "room": "Patio"},
            {"device_type": "motion_sensor", "room": "Basement"},
            {"device_type": "smoke_detector", "room": "Kitchen"},
            {"device_type": "smoke_detector", "room": "Basement"},
            {"device_type": "smoke_detector", "room": "Garage"},
            {"device_type": "co_detector", "room": "Kitchen"},
            {"device_type": "co_detector", "room": "Basement"},
            {"device_type": "security_camera", "room": "Hallway"},
            {"device_type": "security_camera", "room": "Garage"},
            {"device_type": "security_camera", "room": "Patio"},
            {"device_type": "smart_plug", "room": "Kitchen"},
            {"device_type": "smart_plug", "room": "Office"},
            {"device_type": "smart_plug", "room": "Media Room"},
            {"device_type": "smart_refrigerator", "room": "Kitchen"},
            {"device_type": "smart_washer", "room": "Laundry"},
        ],
    },
}


# ---------------------------------------------------------------------------
# Layout helpers
# ---------------------------------------------------------------------------

_CANVAS_W = 1200
_CANVAS_H = 800
_GRID = 20
_PAD = 20
_MARGIN = 40


def _snap(val: float) -> float:
    """Snap a value to the nearest grid line."""
    return round(val / _GRID) * _GRID


def _auto_layout_rooms(rooms: list[Room]) -> None:
    """Assign x, y, width, height to rooms using a flow layout.

    Sizes are scaled proportionally to room area (base: 20 sqm -> 140x100).
    Rooms are placed left-to-right, wrapping to the next row when needed.
    """
    if not rooms:
        return

    # Scale dimensions based on area
    for room in rooms:
        scale = math.sqrt(room.area / 20.0)
        room.width = max(80, min(300, _snap(140 * scale)))
        room.height = max(60, min(220, _snap(100 * scale)))

    # Flow layout: left to right, wrap on overflow
    x = _MARGIN
    y = _MARGIN
    row_height = 0

    for room in rooms:
        if x + room.width > _CANVAS_W - _MARGIN and x > _MARGIN:
            x = _MARGIN
            y += row_height + _PAD
            row_height = 0
        room.x = x
        room.y = y
        row_height = max(row_height, room.height)
        x += room.width + _PAD


# ---------------------------------------------------------------------------
# HomeStore
# ---------------------------------------------------------------------------

def _gen_id() -> str:
    return uuid.uuid4().hex[:8]


class HomeStore:
    """In-memory store for the current home configuration."""

    def __init__(self) -> None:
        self._current: Optional[HomeConfig] = None

    # --- Accessors ---

    def get_current_home(self) -> HomeConfig | None:
        return self._current

    def set_current_home(self, config: HomeConfig) -> None:
        self._current = config

    def clear(self) -> None:
        self._current = None

    # --- Template-based creation ---

    def create_from_template(
        self,
        template_id: str,
        home_name: str = "",
    ) -> HomeConfig:
        """Create a HomeConfig from a built-in template."""
        tpl = BUILTIN_TEMPLATES.get(template_id)
        if tpl is None:
            raise ValueError(
                f"Unknown template '{template_id}'. "
                f"Available: {list(BUILTIN_TEMPLATES.keys())}"
            )

        home_id = _gen_id()
        home_name = home_name or tpl["name"]

        # Build rooms
        rooms: list[Room] = []
        room_name_to_id: dict[str, str] = {}
        for r in tpl["rooms"]:
            rid = _gen_id()
            room = Room(
                id=rid,
                name=r["name"],
                room_type=r["room_type"],
                area=r.get("area", 20.0),
                floor=r.get("floor", 0),
            )
            rooms.append(room)
            room_name_to_id[r["name"]] = rid

        # Auto-layout: assign positions & sizes so rooms don't overlap
        _auto_layout_rooms(rooms)

        # Build devices
        devices: list[Device] = []
        for d in tpl.get("default_devices", []):
            room_name = d["room"]
            room_id = room_name_to_id.get(room_name, "")
            dev_type = d["device_type"]
            dev_id = _gen_id()
            device = Device(
                id=dev_id,
                name=f"{dev_type}_{dev_id}",
                device_type=dev_type,
                room_id=room_id,
                agent_managed=True,
            )
            devices.append(device)
            # Add device ref to its room
            for room in rooms:
                if room.id == room_id:
                    room.devices.append(dev_id)
                    break

        config = HomeConfig(
            home_id=home_id,
            home_name=home_name,
            template=template_id,
            rooms=rooms,
            devices=devices,
            residents=[],
        )
        self._current = config
        logger.info(
            f"Home created from template '{template_id}': "
            f"{len(rooms)} rooms, {len(devices)} devices"
        )
        return config

    # --- Restore from saved session dict ---

    def restore_from_dict(self, config: dict[str, Any]) -> HomeConfig | None:
        """Reconstruct HomeConfig from a saved home_config dict.

        This is the inverse of to_session_dict() — called during session
        resume to repopulate the in-memory HomeStore from the persisted
        home_config.json snapshot.
        """
        if not config or not config.get("devices"):
            return None

        rooms = []
        room_devices: dict[str, list[str]] = {}
        for r in config.get("rooms", []):
            rid = r.get("id", _gen_id())
            room = Room(
                id=rid,
                name=r.get("name", ""),
                room_type=r.get("room_type", ""),
                area=r.get("area", 20.0),
                floor=r.get("floor", 0),
                x=r.get("x", 0),
                y=r.get("y", 0),
                width=r.get("width", 140),
                height=r.get("height", 100),
                devices=[],
            )
            rooms.append(room)
            room_devices[rid] = room.devices

        devices = []
        for d in config.get("devices", []):
            did = d.get("id", _gen_id())
            dev = Device(
                id=did,
                name=d.get("name", ""),
                device_type=d.get("device_type", ""),
                room_id=d.get("room_id", ""),
                agent_managed=True,
                properties=d.get("properties", {}),
            )
            devices.append(dev)
            if dev.room_id in room_devices:
                room_devices[dev.room_id].append(did)

        residents = []
        for res in config.get("residents", []):
            residents.append(Resident(
                id=res.get("id", _gen_id()),
                name=res.get("name", ""),
                resident_type=res.get("resident_type", "adult"),
                age=res.get("age", 30),
                schedule=res.get("schedule", {}),
            ))

        home = HomeConfig(
            home_id=config.get("home_id", _gen_id()),
            home_name=config.get("home_name", "Restored Home"),
            template=config.get("template", "custom"),
            rooms=rooms,
            devices=devices,
            residents=residents,
            created_at=config.get("created_at", datetime.now(timezone.utc).isoformat()),
        )
        self._current = home
        logger.info(
            f"HomeStore restored: '{home.home_name}' — "
            f"{len(rooms)} rooms, {len(devices)} devices"
        )
        return home

    # --- Conversion for bridge/session ---

    def to_session_dict(self) -> dict[str, Any]:
        """Convert current HomeConfig to the dict format expected by
        bridge.setup_fresh_session().

        Returns a dict with keys: home_id, home_name, devices, rooms,
        total_devices, total_rooms.
        """
        if self._current is None:
            raise RuntimeError("No home configuration set")

        home = self._current
        devices_list = []
        for d in home.devices:
            devices_list.append({
                "id": d.id,
                "name": d.name,
                "device_type": d.device_type,
                "room_id": d.room_id,
                "status": "online",
                "properties": dict(d.properties),
            })

        rooms_list = []
        for r in home.rooms:
            rooms_list.append({
                "id": r.id,
                "name": r.name,
                "room_type": r.room_type,
                "area": r.area,
                "floor": r.floor,
                "x": r.x,
                "y": r.y,
                "width": r.width,
                "height": r.height,
            })

        return {
            "home_id": home.home_id,
            "home_name": home.home_name,
            "template": home.template,
            "devices": devices_list,
            "rooms": rooms_list,
            "residents": [asdict(r) for r in home.residents],
            "total_devices": len(devices_list),
            "total_rooms": len(rooms_list),
            "created_at": home.created_at,
        }


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_home_store: Optional[HomeStore] = None


def get_home_store() -> HomeStore:
    """Get or create the global HomeStore singleton."""
    global _home_store
    if _home_store is None:
        _home_store = HomeStore()
    return _home_store
