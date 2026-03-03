"""
Home Builder endpoints.

CRUD operations for building a home configuration (rooms, devices, residents)
before creating a blockchain session.  The home builder operates independently
-- it does NOT require an active session or S5-HES-Agent.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from web.core.state import get_app_state
from web.core.home_store import (
    get_home_store,
    HomeConfig,
    Room,
    Device,
    Resident,
    BUILTIN_TEMPLATES,
    ROOM_TYPES,
)

router = APIRouter()


def _gen_id() -> str:
    return uuid.uuid4().hex[:8]


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class HomeCreateRequest(BaseModel):
    template: Optional[str] = None
    home_name: str = "My Smart Home"


class HomeUpdateRequest(BaseModel):
    home_name: Optional[str] = None


class RoomCreateRequest(BaseModel):
    name: str
    room_type: str
    area: float = 20.0
    floor: int = 0
    x: float = 0
    y: float = 0
    width: float = 140
    height: float = 100


class RoomUpdateRequest(BaseModel):
    name: Optional[str] = None
    room_type: Optional[str] = None
    area: Optional[float] = None
    floor: Optional[int] = None


class DeviceCreateRequest(BaseModel):
    name: str
    device_type: str
    room_id: str
    properties: dict[str, Any] = Field(default_factory=dict)


class ResidentCreateRequest(BaseModel):
    name: str
    resident_type: str = "adult"
    age: int = 30
    schedule: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_home() -> HomeConfig:
    """Get the current home config, raising 404 if none exists."""
    store = get_home_store()
    home = store.get_current_home()
    if home is None:
        raise HTTPException(
            status_code=404,
            detail="No home configured. Use POST /api/home to create one.",
        )
    return home


# ---------------------------------------------------------------------------
# Home CRUD
# ---------------------------------------------------------------------------

@router.get("/")
async def get_home() -> dict:
    """Get the current home configuration."""
    home = _require_home()
    store = get_home_store()
    return store.to_session_dict()


@router.post("/", status_code=201)
async def create_home(body: HomeCreateRequest) -> dict:
    """Create a home from a template or as an empty custom config."""
    store = get_home_store()

    if body.template:
        try:
            config = store.create_from_template(body.template, body.home_name)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    else:
        config = HomeConfig(
            home_id=_gen_id(),
            home_name=body.home_name,
            template="custom",
        )
        store.set_current_home(config)
        logger.info(f"Home initialized: {config.home_name} (rooms/devices added next)")

    return {
        "home_id": config.home_id,
        "home_name": config.home_name,
        "template": config.template,
        "rooms": len(config.rooms),
        "devices": len(config.devices),
        "residents": len(config.residents),
    }


@router.put("/")
async def update_home(body: HomeUpdateRequest) -> dict:
    """Update top-level home properties."""
    home = _require_home()
    if body.home_name is not None:
        home.home_name = body.home_name
    return {"home_id": home.home_id, "home_name": home.home_name}


@router.post("/custom", status_code=201)
async def create_custom_home(body: dict[str, Any]) -> dict:
    """Bulk create/replace home from a full configuration payload.

    Accepts the same payload format as S5-HES-Agent's
    /api/simulation/home/custom endpoint for compatibility.
    """
    store = get_home_store()
    home_id = _gen_id()
    home_name = body.get("name", "My Smart Home")

    rooms_data = body.get("rooms", [])
    inhabitants_data = body.get("inhabitants", [])

    rooms: list[Room] = []
    room_id_map: dict[str, str] = {}  # frontend id -> backend id

    for r in rooms_data:
        rid = _gen_id()
        old_id = r.get("id", rid)
        room_id_map[old_id] = rid
        room = Room(
            id=rid,
            name=r.get("name", "Room"),
            room_type=r.get("type", r.get("room_type", "living_room")),
            area=r.get("area", 20.0),
            floor=r.get("floor", 0),
            x=r.get("x", 0),
            y=r.get("y", 0),
            width=r.get("width", 140),
            height=r.get("height", 100),
        )
        # Add devices from room payload
        for d in r.get("devices", []):
            dev_id = _gen_id()
            room.devices.append(dev_id)
        rooms.append(room)

    # Build device list (second pass to get room_id mapping)
    devices: list[Device] = []
    for r in rooms_data:
        old_id = r.get("id", "")
        backend_room_id = room_id_map.get(old_id, "")
        for d in r.get("devices", []):
            dev_id = _gen_id()
            device = Device(
                id=dev_id,
                name=d.get("name", d.get("type", "device")),
                device_type=d.get("type", d.get("device_type", "unknown")),
                room_id=backend_room_id,
                agent_managed=True,
                properties={
                    k: v for k, v in d.items()
                    if k not in ("id", "name", "type", "device_type")
                },
            )
            devices.append(device)

    # Fix room.devices lists to match actual device IDs
    dev_idx = 0
    for room in rooms:
        count = len(room.devices)
        room.devices = [devices[dev_idx + i].id for i in range(count)]
        dev_idx += count

    residents: list[Resident] = []
    for inh in inhabitants_data:
        residents.append(Resident(
            id=_gen_id(),
            name=inh.get("name", "Resident"),
            resident_type=inh.get("role", inh.get("resident_type", "adult")),
            age=inh.get("age", 30),
            schedule=inh.get("schedule", {}),
        ))

    config = HomeConfig(
        home_id=home_id,
        home_name=home_name,
        template="custom",
        rooms=rooms,
        devices=devices,
        residents=residents,
    )
    store.set_current_home(config)
    logger.info(
        f"Custom home created: {home_name} — "
        f"{len(rooms)} rooms, {len(devices)} devices, {len(residents)} residents"
    )
    return {
        "home_id": home_id,
        "home_name": home_name,
        "rooms": len(rooms),
        "devices": len(devices),
        "residents": len(residents),
    }


# ---------------------------------------------------------------------------
# Templates & Device Type Catalog
# ---------------------------------------------------------------------------

@router.get("/templates")
async def list_templates() -> list[dict]:
    """List available home templates (built-in + S5-HES if available)."""
    templates = []
    for tid, tpl in BUILTIN_TEMPLATES.items():
        templates.append({
            "id": tid,
            "name": tpl["name"],
            "description": tpl.get("description", ""),
            "rooms": len(tpl["rooms"]),
            "devices": len(tpl.get("default_devices", [])),
            "source": "builtin",
        })

    # Extend with S5-HES templates if available
    state = get_app_state()
    if state.s5_hes_available and state.s5_hes_client:
        try:
            s5_templates = await state.s5_hes_client.get_templates()
            for st in s5_templates:
                templates.append({
                    "id": st.get("id", st.get("name", "")),
                    "name": st.get("name", ""),
                    "description": st.get("description", "S5-HES template"),
                    "rooms": st.get("rooms_count", 0),
                    "devices": st.get("devices_count", 0),
                    "source": "s5_hes",
                })
        except Exception as e:
            logger.debug(f"Failed to fetch S5-HES templates: {e}")

    return templates


@router.get("/device-types")
async def list_device_types() -> list[dict]:
    """Device catalog from S5-HES-Agent. All device types are agent-managed."""
    types: list[dict] = []
    existing_ids: set[str] = set()

    state = get_app_state()
    if state.s5_hes_available and state.s5_hes_client:
        try:
            s5_types = await state.s5_hes_client.get_device_types()

            # S5-HES may return:
            #   {categories: [...], devices_by_category: {cat: [devs]}, total_device_types: N}
            #   OR a flat {category: [devices]} dict
            #   OR a flat list of devices
            device_map: dict | None = None
            if isinstance(s5_types, dict):
                if "devices_by_category" in s5_types:
                    device_map = s5_types["devices_by_category"]
                else:
                    device_map = {
                        k: v for k, v in s5_types.items()
                        if isinstance(v, list)
                    }

            if device_map:
                for category, devs in device_map.items():
                    if not isinstance(devs, list):
                        continue
                    for d in devs:
                        if not isinstance(d, dict):
                            continue
                        did = d.get("id", d.get("type", ""))
                        if did and did not in existing_ids:
                            types.append({
                                "id": did,
                                "name": d.get("name", did),
                                "category": category,
                                "agent_managed": True,
                                "description": d.get("description", ""),
                                "source": "s5_hes",
                            })
                            existing_ids.add(did)
            elif isinstance(s5_types, list):
                for d in s5_types:
                    if not isinstance(d, dict):
                        continue
                    did = d.get("id", d.get("type", ""))
                    if did and did not in existing_ids:
                        types.append({
                            "id": did,
                            "name": d.get("name", did),
                            "category": d.get("category", "other"),
                            "agent_managed": True,
                            "source": "s5_hes",
                        })
                        existing_ids.add(did)
        except Exception as e:
            logger.debug(f"Failed to fetch S5-HES device types: {e}")

    return types


@router.get("/device-types/categorized")
async def list_device_types_categorized() -> dict[str, list[dict]]:
    """Device types grouped by category."""
    all_types = await list_device_types()
    categorized: dict[str, list[dict]] = {}
    for t in all_types:
        cat = t.get("category", "other")
        categorized.setdefault(cat, []).append(t)
    return categorized


# ---------------------------------------------------------------------------
# Room CRUD
# ---------------------------------------------------------------------------

@router.get("/rooms")
async def list_rooms() -> list[dict]:
    """List rooms in the current home."""
    home = _require_home()
    return [
        {
            "id": r.id,
            "name": r.name,
            "room_type": r.room_type,
            "area": r.area,
            "floor": r.floor,
            "x": r.x,
            "y": r.y,
            "width": r.width,
            "height": r.height,
            "device_count": len(r.devices),
        }
        for r in home.rooms
    ]


@router.post("/rooms", status_code=201)
async def add_room(body: RoomCreateRequest) -> dict:
    """Add a room to the current home."""
    home = _require_home()
    if body.room_type not in ROOM_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid room type '{body.room_type}'. Valid: {ROOM_TYPES}",
        )
    room = Room(
        id=_gen_id(),
        name=body.name,
        room_type=body.room_type,
        area=body.area,
        floor=body.floor,
        x=body.x,
        y=body.y,
        width=body.width,
        height=body.height,
    )
    home.rooms.append(room)
    return {"id": room.id, "name": room.name, "room_type": room.room_type}


@router.put("/rooms/{room_id}")
async def update_room(room_id: str, body: RoomUpdateRequest) -> dict:
    """Update a room's properties."""
    home = _require_home()
    for room in home.rooms:
        if room.id == room_id:
            if body.name is not None:
                room.name = body.name
            if body.room_type is not None:
                if body.room_type not in ROOM_TYPES:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid room type '{body.room_type}'.",
                    )
                room.room_type = body.room_type
            if body.area is not None:
                room.area = body.area
            if body.floor is not None:
                room.floor = body.floor
            return {"id": room.id, "name": room.name, "status": "updated"}
    raise HTTPException(status_code=404, detail=f"Room '{room_id}' not found")


@router.delete("/rooms/{room_id}")
async def delete_room(room_id: str) -> dict:
    """Delete a room and all its devices."""
    home = _require_home()
    room_found = False
    for i, room in enumerate(home.rooms):
        if room.id == room_id:
            home.rooms.pop(i)
            room_found = True
            break
    if not room_found:
        raise HTTPException(status_code=404, detail=f"Room '{room_id}' not found")

    # Cascade: remove devices in this room
    removed_devices = [d for d in home.devices if d.room_id == room_id]
    home.devices = [d for d in home.devices if d.room_id != room_id]

    return {
        "id": room_id,
        "status": "deleted",
        "devices_removed": len(removed_devices),
    }


# ---------------------------------------------------------------------------
# Device CRUD
# ---------------------------------------------------------------------------

@router.get("/devices")
async def list_devices() -> list[dict]:
    """List devices in the current home."""
    home = _require_home()
    return [
        {
            "id": d.id,
            "name": d.name,
            "device_type": d.device_type,
            "room_id": d.room_id,
            "agent_managed": d.agent_managed,
            "properties": d.properties,
        }
        for d in home.devices
    ]


@router.post("/devices", status_code=201)
async def add_device(body: DeviceCreateRequest) -> dict:
    """Add a device to a room."""
    home = _require_home()

    # Validate room exists
    room = None
    for r in home.rooms:
        if r.id == body.room_id:
            room = r
            break
    if room is None:
        raise HTTPException(
            status_code=400,
            detail=f"Room '{body.room_id}' not found in current home.",
        )

    dev_id = _gen_id()
    device = Device(
        id=dev_id,
        name=body.name,
        device_type=body.device_type,
        room_id=body.room_id,
        agent_managed=True,
        properties=body.properties,
    )
    home.devices.append(device)
    room.devices.append(dev_id)

    return {
        "id": device.id,
        "name": device.name,
        "device_type": device.device_type,
        "room_id": device.room_id,
        "agent_managed": device.agent_managed,
    }


@router.delete("/devices/{device_id}")
async def delete_device(device_id: str) -> dict:
    """Remove a device from the home."""
    home = _require_home()
    dev_found = False
    dev_room_id = ""
    for i, d in enumerate(home.devices):
        if d.id == device_id:
            dev_room_id = d.room_id
            home.devices.pop(i)
            dev_found = True
            break
    if not dev_found:
        raise HTTPException(status_code=404, detail=f"Device '{device_id}' not found")

    # Remove from room's device list
    for room in home.rooms:
        if room.id == dev_room_id and device_id in room.devices:
            room.devices.remove(device_id)
            break

    return {"id": device_id, "status": "deleted"}


# ---------------------------------------------------------------------------
# Resident CRUD
# ---------------------------------------------------------------------------

@router.get("/residents")
async def list_residents() -> list[dict]:
    """List residents in the current home."""
    home = _require_home()
    return [
        {
            "id": r.id,
            "name": r.name,
            "resident_type": r.resident_type,
            "schedule": r.schedule,
        }
        for r in home.residents
    ]


@router.post("/residents", status_code=201)
async def add_resident(body: ResidentCreateRequest) -> dict:
    """Add a resident to the home."""
    home = _require_home()
    valid_types = ["adult", "child", "elderly", "teenager", "pet", "guest"]
    if body.resident_type not in valid_types:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid resident type '{body.resident_type}'. Valid: {valid_types}",
        )
    resident = Resident(
        id=_gen_id(),
        name=body.name,
        resident_type=body.resident_type,
        age=body.age,
        schedule=body.schedule,
    )
    home.residents.append(resident)
    return {
        "id": resident.id,
        "name": resident.name,
        "resident_type": resident.resident_type,
    }


@router.delete("/residents/{resident_id}")
async def delete_resident(resident_id: str) -> dict:
    """Remove a resident from the home."""
    home = _require_home()
    for i, r in enumerate(home.residents):
        if r.id == resident_id:
            home.residents.pop(i)
            return {"id": resident_id, "status": "deleted"}
    raise HTTPException(status_code=404, detail=f"Resident '{resident_id}' not found")
