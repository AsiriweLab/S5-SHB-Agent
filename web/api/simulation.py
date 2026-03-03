"""
Simulation proxy endpoints.

Proxies simulation control to S5-HES-Agent (port 8000) and manages the
telemetry polling loop that feeds events to WebSocket streams.

All simulation endpoints require S5-HES-Agent to be available.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from web.core.state import get_app_state
from web.core.home_store import get_home_store
from web.core.threat_store import get_threat_store
from web.core.orchestrator import start_orchestration, stop_orchestration

router = APIRouter()


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class SimulationStartRequest(BaseModel):
    duration_hours: float = Field(default=24.0, ge=0.0166, le=168.0)
    time_compression: int = Field(default=1440, ge=1, le=86400)
    include_threats: bool = True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_s5_hes():
    """Raise 503 if S5-HES-Agent is not available."""
    state = get_app_state()
    if not state.s5_hes_available or state.s5_hes_client is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "S5-HES-Agent is not available. "
                "Simulation features require S5-HES-Agent running on port 8000."
            ),
        )
    return state


_SEVERITY_VALUES = {"low": 25, "medium": 50, "high": 75, "critical": 100}


def _convert_home_to_s5_format(home_dict: dict[str, Any]) -> dict[str, Any]:
    """Convert HomeStore's session dict to S5-HES /home/custom format.

    S5-HES expects CustomHomeCreateRequest with:
      rooms: [{id, name, type, x, y, width, height, devices: [...]}]
      inhabitants: [{id, name, role, age, schedule: {...}}]
    """
    # Build rooms with embedded devices
    rooms_by_id: dict[str, dict] = {}
    for r in home_dict.get("rooms", []):
        rid = r.get("id", "")
        rooms_by_id[rid] = {
            "id": rid,
            "name": r.get("name", "Room"),
            "type": r.get("room_type", "living_room"),
            "x": r.get("x", 0),
            "y": r.get("y", 0),
            "width": r.get("width", 140),
            "height": r.get("height", 100),
            "devices": [],
        }

    # Place devices into their rooms
    for d in home_dict.get("devices", []):
        room_id = d.get("room_id", "")
        device_entry = {
            "id": d.get("id", ""),
            "name": d.get("name", ""),
            "type": d.get("device_type", ""),
            "properties": d.get("properties", {}),
        }
        if room_id in rooms_by_id:
            rooms_by_id[room_id]["devices"].append(device_entry)

    # Build inhabitants from residents
    inhabitants = []
    for r in home_dict.get("residents", []):
        inhabitants.append({
            "id": r.get("id", ""),
            "name": r.get("name", "Resident"),
            "role": r.get("resident_type", "adult"),
            "age": r.get("age", 30),
            "schedule": r.get("schedule", {}),
        })

    return {
        "name": home_dict.get("home_name", "Smart Home"),
        "rooms": list(rooms_by_id.values()),
        "inhabitants": inhabitants,
    }


# ---------------------------------------------------------------------------
# Telemetry polling background task
# ---------------------------------------------------------------------------

_polling_task: Optional[asyncio.Task] = None


async def _telemetry_polling_loop() -> None:
    """Background task: polls S5-HES events and pushes to WebSocket."""
    from web.ws.manager import ws_manager

    # Capture S5-HES client (stable reference — not reset by auto_ensure_session)
    client = get_app_state().s5_hes_client
    last_event_count = 0
    saw_running = False  # Guard against premature "idle" detection

    logger.info("Telemetry polling loop started")

    while True:
        # Always read the CURRENT state (may have been replaced by reset_app_state)
        state = get_app_state()
        if not state.simulation_active:
            break

        try:
            # Check simulation status
            status = await client.get_simulation_status()
            sim_state = status.get("state", status.get("status", "idle"))

            if sim_state == "running":
                saw_running = True

            # Detect simulation end — only after we've seen it running
            is_ended = sim_state in ("completed", "error")
            if sim_state == "idle" and saw_running:
                is_ended = True
            # Progress-based completion (S5-HES reports progress_percent)
            progress = status.get("progress_percent", 0)
            if saw_running and progress >= 100:
                is_ended = True

            if is_ended:
                # Set flag on CURRENT state so orchestrator loops see it too
                current = get_app_state()
                current.simulation_active = False
                # Stop orchestration (runs final cycle, then cleans up)
                await stop_orchestration()
                ws_manager.push_event("telemetry", {
                    "event_type": "simulation_ended",
                    "state": sim_state,
                })
                logger.info(f"Simulation ended: {sim_state}")
                break

            # Poll new events
            try:
                events_data = await client.get_events(
                    limit=100, offset=last_event_count
                )
                new_events = (
                    events_data
                    if isinstance(events_data, list)
                    else events_data.get("events", [])
                )
            except Exception:
                new_events = []

            if new_events:
                last_event_count += len(new_events)
                ws_manager.push_event("telemetry", {
                    "event_type": "simulation_events",
                    "events": new_events[:20],
                    "new_count": len(new_events),
                    "total_events": last_event_count,
                })

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.debug(f"Telemetry polling error: {e}")

        await asyncio.sleep(2.0)

    logger.info("Telemetry polling loop stopped")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/status")
async def simulation_status() -> dict:
    """S5-HES-Agent connection status and simulation state."""
    state = get_app_state()
    result: dict[str, Any] = {
        "s5_hes_available": state.s5_hes_available,
        "s5_hes_url": (
            state.s5_hes_client.base_url if state.s5_hes_client else None
        ),
        "simulation_active": state.simulation_active,
    }

    if state.s5_hes_available and state.s5_hes_client:
        try:
            sim_status = await state.s5_hes_client.get_simulation_status()
            result["simulation"] = sim_status
        except Exception as e:
            result["simulation_error"] = str(e)

    return result


@router.post("/start")
async def start_simulation(body: SimulationStartRequest) -> dict:
    """Push home to S5-HES-Agent and start behavior simulation."""
    global _polling_task

    state = _require_s5_hes()

    if state.simulation_active:
        raise HTTPException(status_code=409, detail="Simulation already running")

    # 1. Get home config
    store = get_home_store()
    home = store.get_current_home()
    if home is None:
        raise HTTPException(
            status_code=400,
            detail="No home configured. Use the Home Builder first.",
        )

    # 2. Convert and push home to S5-HES
    home_dict = store.to_session_dict()
    s5_home = _convert_home_to_s5_format(home_dict)
    try:
        push_result = await state.s5_hes_client.push_home(s5_home)
        logger.info(f"Home pushed to S5-HES: {push_result}")
    except Exception as e:
        raise HTTPException(
            status_code=502, detail=f"Failed to push home to S5-HES: {e}"
        )

    # 3. Build threat config
    threat_config = None
    threats_count = 0
    if body.include_threats:
        threat_store = get_threat_store()
        threats = threat_store.get_threats()
        if threats:
            threat_config = {
                "threats": [
                    {
                        "id": t.id,
                        "type": t.threat_type,
                        "name": t.name,
                        "severity": t.severity,
                        "severityValue": _SEVERITY_VALUES.get(t.severity, 50),
                        "targetDevices": (
                            [t.target_device] if t.target_device else []
                        ),
                        "startTime": t.parameters.get("start_time", 0),
                        "duration": t.parameters.get("duration", 30),
                    }
                    for t in threats
                ],
            }
            threats_count = len(threats)

    # 4. Start simulation
    try:
        result = await state.s5_hes_client.start_simulation(
            duration=body.duration_hours,
            time_compression=body.time_compression,
            threat_config=threat_config,
        )
    except Exception as e:
        raise HTTPException(
            status_code=502, detail=f"Failed to start simulation: {e}"
        )

    # 5. Start telemetry polling (S5-HES events -> WebSocket)
    state.simulation_active = True
    _polling_task = asyncio.create_task(_telemetry_polling_loop())

    # 6. Start orchestration (auto-session + agent cycles + telemetry bridge)
    orchestration_result = await start_orchestration()

    return {
        "status": "started",
        "simulation": result,
        "threats_injected": threats_count,
        "orchestration": orchestration_result,
    }


@router.post("/pause")
async def pause_simulation() -> dict:
    """Pause the running simulation."""
    state = _require_s5_hes()
    try:
        result = await state.s5_hes_client.pause_simulation()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Pause failed: {e}")
    return {"status": "paused", "detail": result}


@router.post("/resume")
async def resume_simulation() -> dict:
    """Resume a paused simulation."""
    state = _require_s5_hes()
    try:
        result = await state.s5_hes_client.resume_simulation()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Resume failed: {e}")
    return {"status": "resumed", "detail": result}


@router.post("/stop")
async def stop_simulation() -> dict:
    """Stop the running simulation."""
    global _polling_task

    state = _require_s5_hes()
    try:
        result = await state.s5_hes_client.stop_simulation()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Stop failed: {e}")

    state.simulation_active = False

    # Stop orchestration (agent cycles, telemetry bridge, auto-save)
    await stop_orchestration()

    if _polling_task and not _polling_task.done():
        _polling_task.cancel()
        try:
            await _polling_task
        except asyncio.CancelledError:
            pass
        _polling_task = None

    return {"status": "stopped", "detail": result}


@router.get("/events")
async def get_events(
    event_type: Optional[str] = None,
    device_id: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """Get simulation events (proxy to S5-HES-Agent)."""
    state = _require_s5_hes()
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if event_type:
        params["event_type"] = event_type
    if device_id:
        params["device_id"] = device_id
    try:
        result = await state.s5_hes_client.get_events(**params)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch events: {e}")
    return {"events": result}


@router.get("/telemetry/{device_id}")
async def get_device_telemetry(device_id: str) -> dict:
    """Get telemetry for a specific device (proxy to S5-HES-Agent)."""
    state = _require_s5_hes()
    try:
        result = await state.s5_hes_client.get_device_telemetry(device_id)
    except Exception as e:
        raise HTTPException(
            status_code=502, detail=f"Failed to fetch telemetry: {e}"
        )
    return {"device_id": device_id, "telemetry": result}
