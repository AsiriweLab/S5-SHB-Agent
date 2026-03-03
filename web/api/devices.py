"""
Device layer endpoints.

Wraps the MCP client to expose device operations via REST:
- List devices, get status, telemetry
- Execute commands (validated on blockchain)
- Emergency scanning
- Fault injection (testing)
- Agent-to-device permission mapping
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from web.core.state import get_app_state

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class DeviceCommandRequest(BaseModel):
    """Request to execute a command on a device."""
    command: str = Field(..., description="Command to execute (e.g., set_temperature, lock)")
    params: dict[str, Any] = Field(default_factory=dict, description="Command parameters")


class FaultInjectRequest(BaseModel):
    """Request to inject a simulated fault for testing."""
    fault_type: str = Field(..., description="Fault type (e.g., smoke, gas, motion)")
    params: dict[str, Any] = Field(default_factory=dict, description="Fault parameters")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_active_session():
    """Raise 400 if no active session."""
    state = get_app_state()
    if not state.is_active or state.mcp is None:
        raise HTTPException(
            status_code=400,
            detail="No active session. Create or resume a session first.",
        )
    return state


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/")
def list_devices() -> list[dict[str, Any]]:
    """List all managed devices (mapped from home configuration)."""
    state = _require_active_session()
    try:
        devices = state.mcp.list_devices()
        return devices
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"MCP error: {e}")


@router.get("/telemetry")
def get_all_telemetry() -> list[dict[str, Any]]:
    """Get telemetry readings from all devices."""
    state = _require_active_session()
    try:
        telemetry_list = state.mcp.get_all_telemetry()
        return [
            {
                "device_id": t.device_id,
                "device_type": t.device_type,
                "timestamp": t.timestamp,
                "readings": t.readings,
            }
            for t in telemetry_list
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"MCP error: {e}")


@router.get("/emergencies")
def scan_emergencies() -> dict[str, Any]:
    """Scan all devices for emergency conditions."""
    state = _require_active_session()
    try:
        emergencies = state.mcp.scan_emergencies()

        # Record emergencies on blockchain
        if emergencies and state.chain:
            for em in emergencies:
                state.chain.record_emergency(em)

        return {
            "emergencies_found": len(emergencies),
            "emergencies": emergencies,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"MCP error: {e}")


@router.get("/agent-mapping")
def get_agent_device_mapping() -> dict[str, Any]:
    """Show which agent manages which device type (from AGENT_DEFINITIONS)."""
    state = _require_active_session()

    from engine.config import AGENT_DEFINITIONS

    mapping = {}
    for agent_id, defn in AGENT_DEFINITIONS.items():
        mapping[agent_id] = {
            "role": defn["role"],
            "priority": defn["priority"],
            "allowed_device_types": defn["allowed_device_types"],
        }

    return {"agent_device_mapping": mapping}


@router.get("/{device_id}")
def get_device_status(device_id: str) -> dict[str, Any]:
    """Get status and telemetry for a specific device."""
    state = _require_active_session()
    try:
        status = state.mcp.get_device_status(device_id)
        if not status or (isinstance(status, dict) and status.get("error")):
            raise HTTPException(
                status_code=404,
                detail=f"Device '{device_id}' not found",
            )
        return status
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"MCP error: {e}")


@router.post("/{device_id}/command")
def execute_command(
    device_id: str,
    body: DeviceCommandRequest,
) -> dict[str, Any]:
    """Execute a command on a device.

    The command is validated on the blockchain (permission check) then
    executed via MCP.
    """
    state = _require_active_session()

    # Execute via MCP
    try:
        result = state.mcp.execute(device_id, body.command, body.params)
        return {
            "device_id": device_id,
            "command": body.command,
            "params": body.params,
            "result": result,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"MCP execution error: {e}")


@router.post("/{device_id}/fault")
def inject_fault(
    device_id: str,
    body: FaultInjectRequest,
) -> dict[str, Any]:
    """Inject a simulated fault for testing purposes."""
    state = _require_active_session()
    try:
        result = state.mcp.inject_fault(device_id, body.fault_type, body.params)
        return {
            "device_id": device_id,
            "fault_type": body.fault_type,
            "params": body.params,
            "result": result,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Fault injection error: {e}")


# ---------------------------------------------------------------------------
# Dual-mode: device config and connection testing
# ---------------------------------------------------------------------------

@router.get("/config/protocols")
def list_protocols() -> dict[str, Any]:
    """List available device adapter protocols."""
    from engine.adapters import AdapterRegistry
    return {
        "protocols": AdapterRegistry.available_protocols(),
    }


@router.get("/config/mode")
def get_device_mode() -> dict[str, Any]:
    """Get the current session's device mode configuration."""
    state = get_app_state()
    if not state.is_active:
        return {"mode": None, "message": "No active session"}

    if state.device_config is None:
        return {"mode": "simulation", "real_devices": 0, "simulated_devices": 0}

    from engine.mcp_server import get_device_layer
    dl = get_device_layer()
    real_count = sum(1 for d in dl.devices.values()
                     if getattr(d, "mode", "simulation") == "real")
    sim_count = len(dl.devices) - real_count

    return {
        "mode": state.device_config.mode.value,
        "real_devices": real_count,
        "simulated_devices": sim_count,
        "total_devices": len(dl.devices),
        "real_device_ids": [
            d.device_id for d in dl.devices.values()
            if getattr(d, "mode", "simulation") == "real"
        ],
    }


class TestConnectionRequest(BaseModel):
    """Request to test a real device connection."""
    device_id: str
    protocol: str = "mock"
    host: str = "localhost"
    port: int = 1883
    topic: str = ""
    endpoint: str = ""
    auth: dict[str, str] = Field(default_factory=dict)
    device_type: str = "thermostat"
    room: str = "test"


@router.post("/config/test-connection")
def test_device_connection(body: TestConnectionRequest) -> dict[str, Any]:
    """Test connectivity to a real device before adding it to a session."""
    from engine.device_config import DeviceConnectionConfig
    from engine.adapters import AdapterRegistry

    config = DeviceConnectionConfig(
        device_id=body.device_id,
        device_type=body.device_type,
        room=body.room,
        protocol=body.protocol,
        host=body.host,
        port=body.port,
        topic=body.topic,
        endpoint=body.endpoint,
        auth=body.auth,
    )

    try:
        adapter = AdapterRegistry.create(config)
        result = adapter.test_connection()
        return {
            "device_id": body.device_id,
            "protocol": body.protocol,
            **result,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        return {
            "device_id": body.device_id,
            "protocol": body.protocol,
            "ok": False,
            "msg": str(e),
            "latency_ms": 0,
        }
