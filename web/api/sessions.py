"""
Session management endpoints.

Provides CRUD operations for sessions:
- List all sessions
- Create a new session (from home configuration)
- Get session info
- Resume a session
- Save active session
- Delete a session
- Get active session info
"""

from __future__ import annotations

import re
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from web.core.state import get_app_state
from web.core.bridge import (
    setup_fresh_session,
    setup_resume_session,
    save_current_session,
    teardown_session,
    _get_session_manager,
)


router = APIRouter()


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class RealDeviceRequest(BaseModel):
    """Connection config for a single real device."""
    device_id: str
    device_type: str = ""
    room: str = "unknown"
    protocol: str = Field(default="mock", description="Protocol: mqtt, http, mock")
    host: str = "localhost"
    port: int = 1883
    topic: str = ""
    endpoint: str = ""
    auth: dict[str, str] = Field(default_factory=dict)
    telemetry_map: dict[str, str] = Field(default_factory=dict)
    command_map: dict[str, str] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)


class SessionCreateRequest(BaseModel):
    """Request body for creating a new session."""
    name: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Session name (alphanumeric, hyphens, underscores)",
    )
    preset: str = Field(
        default="balanced",
        description="Governance preset: balanced, safety_first, efficiency, privacy",
    )
    device_mode: str = Field(
        default="simulation",
        description="Device mode: simulation, real, or hybrid",
    )
    real_devices: list[RealDeviceRequest] = Field(
        default_factory=list,
        description="Real device connection configs (for real/hybrid mode)",
    )


class SessionCreateResponse(BaseModel):
    """Response after creating a session."""
    session_name: str
    status: str
    device_mode: str = "simulation"
    devices_mapped: int
    devices_skipped: int
    device_type_counts: dict[str, int]
    agents_initialized: int
    blockchain_blocks: int
    real_devices: int = 0
    simulated_devices: int = 0
    message: str


class SessionInfoResponse(BaseModel):
    """Response for session metadata."""
    name: str
    created: Optional[float] = None
    created_iso: Optional[str] = None
    last_run: Optional[float] = None
    last_run_iso: Optional[str] = None
    poc_version: Optional[str] = None
    blocks: int = 0
    scenarios_run: int = 0
    device_mode: Optional[str] = None


class ActiveSessionResponse(BaseModel):
    """Response for the currently active session."""
    active: bool
    session_name: Optional[str] = None
    is_fresh: bool = True
    devices: int = 0
    agents: int = 0
    blockchain_blocks: int = 0
    subsystems_ready: int = 0
    subsystems_total: int = 0


# ---------------------------------------------------------------------------
# Home configuration access
# ---------------------------------------------------------------------------

def _get_home_config() -> dict[str, Any]:
    """Retrieve home configuration from the HomeStore.

    Raises HTTPException if no home configuration has been set via Home Builder.
    """
    from web.core.home_store import get_home_store

    store = get_home_store()
    home = store.get_current_home()
    if home is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "No home configuration provided. "
                "Use the Home Builder (POST /api/home) to configure a home first."
            ),
        )
    return store.to_session_dict()


def _validate_session_name(name: str) -> str:
    """Validate and sanitize session name."""
    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Session name cannot be empty")
    if len(name) > 64:
        raise HTTPException(status_code=400, detail="Session name too long (max 64)")
    if not re.match(r"^[a-zA-Z0-9_-]+$", name):
        raise HTTPException(
            status_code=400,
            detail="Session name must contain only alphanumeric characters, hyphens, and underscores",
        )
    return name


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/")
async def list_sessions() -> list[SessionInfoResponse]:
    """List all saved sessions with metadata."""
    session_mgr = _get_session_manager()
    sessions = session_mgr.list_sessions()
    return [
        SessionInfoResponse(
            name=s.get("name", ""),
            created=s.get("created"),
            created_iso=s.get("created_iso"),
            last_run=s.get("last_run"),
            last_run_iso=s.get("last_run_iso"),
            poc_version=s.get("poc_version"),
            blocks=s.get("blocks", 0),
            scenarios_run=s.get("scenarios_run", 0),
            device_mode=s.get("device_mode"),
        )
        for s in sessions
    ]


@router.post("/", status_code=201)
async def create_session(body: SessionCreateRequest) -> SessionCreateResponse:
    """Create a new session from the current home configuration.

    Prerequisites:
    - A home configuration must be set (via POST /api/sessions/home).
    - No active session (teardown first if one exists).
    """
    name = _validate_session_name(body.name)

    # Check if session name already exists
    session_mgr = _get_session_manager()
    if session_mgr.session_exists(name):
        raise HTTPException(
            status_code=409,
            detail=f"Session '{name}' already exists. Delete it first or choose a different name.",
        )

    # Check if there's an active session
    state = get_app_state()
    if state.is_active:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Session '{state.session_name}' is currently active. "
                "Save and teardown the current session first."
            ),
        )

    # Read home configuration
    home_config = _get_home_config()
    home_devices = home_config.get("devices", [])
    home_rooms = home_config.get("rooms", [])

    # Build device config for dual-mode support
    from engine.device_config import DeviceMode, DeviceConnectionConfig, SessionDeviceConfig

    device_mode = body.device_mode.lower()
    if device_mode not in ("simulation", "real", "hybrid"):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid device_mode '{body.device_mode}'. Must be: simulation, real, hybrid",
        )

    device_config = None
    if device_mode in ("real", "hybrid"):
        real_conns = [
            DeviceConnectionConfig(
                device_id=rd.device_id,
                device_type=rd.device_type,
                room=rd.room,
                protocol=rd.protocol,
                host=rd.host,
                port=rd.port,
                topic=rd.topic,
                endpoint=rd.endpoint,
                auth=rd.auth,
                telemetry_map=rd.telemetry_map,
                command_map=rd.command_map,
                options=rd.options,
            )
            for rd in body.real_devices
        ]
        device_config = SessionDeviceConfig(
            mode=DeviceMode(device_mode),
            real_devices=real_conns,
        )

    logger.info(
        f"Creating session '{name}' ({device_mode}): "
        f"{len(home_devices)} devices, {len(home_rooms)} rooms"
    )

    # Create fresh session via bridge (mode-aware)
    new_state, mapping_report = setup_fresh_session(
        session_name=name,
        home_devices=home_devices,
        home_rooms=home_rooms,
        home_config=home_config,
        device_config=device_config,
    )

    return SessionCreateResponse(
        session_name=name,
        status="active",
        device_mode=device_mode,
        devices_mapped=mapping_report["mapped_devices"],
        devices_skipped=mapping_report["skipped_devices"],
        device_type_counts=mapping_report["device_type_counts"],
        agents_initialized=len(new_state.agents) + 3,  # +3 specialized
        blockchain_blocks=len(new_state.chain.chain),
        real_devices=mapping_report.get("real_devices", 0),
        simulated_devices=mapping_report.get(
            "simulated_devices", mapping_report["mapped_devices"]
        ),
        message=(
            f"Session '{name}' created ({device_mode}) with "
            f"{mapping_report['mapped_devices']} devices from "
            f"{len(home_devices)} home devices."
        ),
    )


@router.get("/active")
async def get_active_session() -> ActiveSessionResponse:
    """Get information about the currently active session."""
    state = get_app_state()

    if not state.is_active:
        return ActiveSessionResponse(active=False)

    subsystems = {
        "blockchain": state.chain is not None,
        "agents": len(state.agents) > 0,
        "mcp": state.mcp is not None,
        "offchain_store": state.store is not None,
        "nlu": state.nlu_agent is not None,
        "anomaly_detection": state.anomaly_agent is not None,
        "arbitration": state.arb_agent is not None,
        "governance": state.gov_contract is not None,
        "health_monitor": state.health_monitor is not None,
        "session_manager": state.session_mgr is not None,
    }

    device_count = 0
    if state.mcp:
        try:
            device_count = len(state.mcp.list_devices())
        except Exception:
            pass

    return ActiveSessionResponse(
        active=True,
        session_name=state.session_name,
        is_fresh=state.is_fresh,
        devices=device_count,
        agents=len(state.agents) + 3,  # +3 specialized
        blockchain_blocks=len(state.chain.chain) if state.chain else 0,
        subsystems_ready=sum(subsystems.values()),
        subsystems_total=len(subsystems),
    )


@router.get("/{name}")
async def get_session(name: str) -> SessionInfoResponse:
    """Get metadata for a specific session."""
    name = _validate_session_name(name)
    session_mgr = _get_session_manager()

    if not session_mgr.session_exists(name):
        raise HTTPException(status_code=404, detail=f"Session '{name}' not found")

    sessions = session_mgr.list_sessions()
    for s in sessions:
        if s.get("name") == name:
            return SessionInfoResponse(
                name=name,
                created=s.get("created"),
                created_iso=s.get("created_iso"),
                last_run=s.get("last_run"),
                last_run_iso=s.get("last_run_iso"),
                poc_version=s.get("poc_version"),
                blocks=s.get("blocks", 0),
                scenarios_run=s.get("scenarios_run", 0),
                device_mode=s.get("device_mode"),
            )

    raise HTTPException(status_code=404, detail=f"Session '{name}' metadata not found")


@router.post("/{name}/resume")
async def resume_session(name: str) -> dict[str, Any]:
    """Resume (activate) an existing session from disk."""
    name = _validate_session_name(name)

    # Check if there's an active session
    state = get_app_state()
    if state.is_active:
        if state.session_name == name:
            return {
                "status": "already_active",
                "session_name": name,
                "message": f"Session '{name}' is already active.",
            }
        raise HTTPException(
            status_code=409,
            detail=(
                f"Session '{state.session_name}' is currently active. "
                "Save and teardown the current session first."
            ),
        )

    session_mgr = _get_session_manager()
    if not session_mgr.session_exists(name):
        raise HTTPException(status_code=404, detail=f"Session '{name}' not found")

    logger.info(f"Resuming session: {name}")
    new_state = setup_resume_session(name)

    device_count = 0
    if new_state.mcp:
        try:
            device_count = len(new_state.mcp.list_devices())
        except Exception:
            pass

    return {
        "status": "resumed",
        "session_name": name,
        "devices": device_count,
        "agents": len(new_state.agents) + 3,
        "blockchain_blocks": len(new_state.chain.chain) if new_state.chain else 0,
        "message": f"Session '{name}' resumed successfully.",
    }


@router.post("/{name}/save")
async def save_session(name: str) -> dict[str, Any]:
    """Save the currently active session to disk."""
    name = _validate_session_name(name)

    state = get_app_state()
    if not state.is_active:
        raise HTTPException(status_code=400, detail="No active session to save")

    if state.session_name != name:
        raise HTTPException(
            status_code=400,
            detail=f"Active session is '{state.session_name}', not '{name}'",
        )

    result = save_current_session()
    return {
        "status": "saved",
        **result,
        "message": f"Session '{name}' saved successfully.",
    }


@router.delete("/{name}")
async def delete_session(name: str) -> dict[str, Any]:
    """Delete a session from disk.

    Cannot delete the currently active session.
    """
    name = _validate_session_name(name)

    # Prevent deleting active session
    state = get_app_state()
    if state.is_active and state.session_name == name:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot delete active session '{name}'. "
                "Teardown the session first."
            ),
        )

    session_mgr = _get_session_manager()
    if not session_mgr.session_exists(name):
        raise HTTPException(status_code=404, detail=f"Session '{name}' not found")

    deleted = session_mgr.delete_session(name)
    if deleted:
        logger.info(f"Session '{name}' deleted")
        return {
            "status": "deleted",
            "session_name": name,
            "message": f"Session '{name}' deleted.",
        }

    raise HTTPException(status_code=500, detail=f"Failed to delete session '{name}'")


@router.post("/teardown")
async def teardown_active_session() -> dict[str, Any]:
    """Deactivate the current session without deleting it from disk.

    The session can be resumed later with POST /{name}/resume.
    """
    state = get_app_state()
    if not state.is_active:
        return {
            "status": "no_session",
            "message": "No active session to teardown.",
        }

    session_name = state.session_name

    # Auto-save before teardown so the session can be resumed later
    try:
        save_current_session()
        logger.info(f"Auto-saved session '{session_name}' before teardown")
    except Exception as e:
        logger.warning(f"Auto-save before teardown failed: {e}")

    teardown_session()

    return {
        "status": "torn_down",
        "session_name": session_name,
        "message": f"Session '{session_name}' deactivated. Resume with POST /{session_name}/resume.",
    }
