"""
MCP Server: Smart Home Device Layer Protocol Interface

POC7: 9 MCP tools (8 from POC6 + health_check).
Wraps DeviceLayer operations for standardized, protocol-based access.
"""

import json
import time
from typing import Any, List, Optional

from fastmcp import FastMCP

from devices import DeviceLayer, Telemetry, HESDevice

# ---------------------------------------------------------------------------
# MCP Server Instance
# ---------------------------------------------------------------------------

mcp = FastMCP("SmartHomeDeviceLayer")

# Underlying DeviceLayer (initialised by init_server)
_dl: Optional[DeviceLayer] = None

# MCP call log for auditing
_mcp_call_log: List[dict] = []


def init_server(device_layer: DeviceLayer) -> FastMCP:
    """Initialise the MCP server with a DeviceLayer instance."""
    global _dl
    _dl = device_layer
    return mcp


def get_device_layer() -> DeviceLayer:
    """Access the underlying DeviceLayer (for setup/permissions only)."""
    return _dl


def get_call_log() -> List[dict]:
    """Return the MCP call audit log."""
    return list(_mcp_call_log)


def _log_call(tool_name: str, params: dict, result: Any):
    """Record an MCP tool invocation for audit trail."""
    _mcp_call_log.append({
        "tool": tool_name,
        "params": params,
        "result_summary": str(result)[:200],
        "timestamp": time.time(),
    })


# ---------------------------------------------------------------------------
# MCP Tools: Device Discovery
# ---------------------------------------------------------------------------

@mcp.tool()
def list_devices() -> str:
    """List all registered smart home devices with their types and rooms."""
    devices = []
    for dev_id, dev in _dl.devices.items():
        devices.append({
            "device_id": dev.device_id,
            "device_type": dev.device_type,
            "room": dev.room,
            "mode": getattr(dev, "mode", "simulation"),
            "state_keys": list(dev.state.keys()),
        })
    result = json.dumps(devices)
    _log_call("list_devices", {}, f"{len(devices)} devices")
    return result


@mcp.tool()
def get_device_status(device_id: str) -> str:
    """Get current status/telemetry for a single device."""
    dev = _dl.devices.get(device_id)
    if not dev:
        result = json.dumps({"error": f"Device '{device_id}' not found"})
        _log_call("get_device_status", {"device_id": device_id}, "NOT FOUND")
        return result
    t = dev.telemetry()
    result = json.dumps({
        "device_id": t.device_id,
        "device_type": t.device_type,
        "timestamp": t.timestamp,
        "readings": t.readings,
    })
    _log_call("get_device_status", {"device_id": device_id}, "OK")
    return result


# ---------------------------------------------------------------------------
# MCP Tools: Telemetry Collection
# ---------------------------------------------------------------------------

@mcp.tool()
def get_all_telemetry() -> str:
    """Collect telemetry from all devices. Returns JSON array."""
    telemetry_list = _dl.get_all_telemetry()
    result = json.dumps([
        {
            "device_id": t.device_id,
            "device_type": t.device_type,
            "timestamp": t.timestamp,
            "readings": t.readings,
        }
        for t in telemetry_list
    ])
    _log_call("get_all_telemetry", {}, f"{len(telemetry_list)} readings")
    return result


# ---------------------------------------------------------------------------
# MCP Tools: Command Execution
# ---------------------------------------------------------------------------

@mcp.tool()
def execute_command(device_id: str, command: str,
                    params: str = "{}") -> str:
    """Execute a command on a device.  params is a JSON string."""
    parsed = json.loads(params) if isinstance(params, str) else params
    result = _dl.execute(device_id, command, parsed)
    _log_call("execute_command",
              {"device_id": device_id, "command": command,
               "params": parsed}, result)
    return json.dumps(result)


# ---------------------------------------------------------------------------
# MCP Tools: Safety-Critical Operations
# ---------------------------------------------------------------------------

@mcp.tool()
def scan_emergencies() -> str:
    """Scan all devices for emergency conditions.  Executes firmware
    override actions immediately (bypasses AI and blockchain)."""
    emergencies = _dl.scan_emergencies()
    _log_call("scan_emergencies", {}, f"{len(emergencies)} emergencies")
    return json.dumps(emergencies, default=str)


@mcp.tool()
def apply_fallback_rules() -> str:
    """Apply pre-programmed fallback rules (for when AI agents are
    offline).  Returns list of actions taken."""
    actions = _dl.apply_fallback_rules()
    _log_call("apply_fallback_rules", {}, f"{len(actions)} actions")
    return json.dumps(actions)


# ---------------------------------------------------------------------------
# MCP Tools: Fault Injection (Test / Demo)
# ---------------------------------------------------------------------------

_FAULT_STATE_MAP = {
    "smoke": {"smoke_level": 0.8, "alarm_active": True},
    "gas": {"gas_level_ppm": 200, "alarm_active": True},
    "motion": {"motion_detected": True, "confidence": 0.85},
    "detection": {"motion_detected": True, "person_detected": False},
    "degradation": {"status": "warning"},
    "power_spike": {"power_watts": 500},
    "temperature_anomaly": {"current_temp": 45},
    "leak": {"leak_detected": True, "alarm_active": True},
    "clear_smoke": {"smoke_level": 0.0, "alarm_active": False},
    "clear_gas": {"gas_level_ppm": 0, "alarm_active": False},
    "clear_motion": {"motion_detected": False, "confidence": 0.0},
    "clear_detection": {"motion_detected": False, "person_detected": False},
    "clear_power_spike": {"power_watts": 50.0},
    "clear_temperature_anomaly": {"current_temp": 22.0},
    "clear_leak": {"leak_detected": False, "alarm_active": False},
}


@mcp.tool()
def inject_fault(device_id: str, fault_type: str,
                 params: str = "{}") -> str:
    """Inject a simulated fault into a device for testing.

    fault_type values:
        smoke, gas, motion, detection, degradation, power_spike,
        temperature_anomaly, leak,
        clear_smoke, clear_gas, clear_motion, clear_detection,
        clear_power_spike, clear_temperature_anomaly, clear_leak
    """
    dev = _dl.devices.get(device_id)
    if not dev:
        return json.dumps({"ok": False,
                           "msg": f"Device '{device_id}' not found"})

    p = json.loads(params) if isinstance(params, str) else params

    state_changes = _FAULT_STATE_MAP.get(fault_type)
    if state_changes is None:
        return json.dumps({"ok": False,
                           "msg": f"Unknown fault_type: {fault_type}"})

    # Apply fault defaults merged with any param overrides
    dev.state.update({**state_changes, **p})

    result = {"ok": True, "msg": f"Injected {fault_type} on {device_id}"}
    _log_call("inject_fault",
              {"device_id": device_id, "fault_type": fault_type}, result)
    return json.dumps(result)


# ---------------------------------------------------------------------------
# MCP Tools: Dynamic Device Registration (NEW for POC6)
# ---------------------------------------------------------------------------

@mcp.tool()
def register_device(device_type: str, device_id: str,
                    room: str, params: str = "{}") -> str:
    """Dynamically register a new device at runtime via MCP.

    Accepts any HES device type (118+ types). Creates a universal
    HESDevice instance.
    """
    if device_id in _dl.devices:
        return json.dumps({"ok": False,
                           "msg": f"Device '{device_id}' already exists"})

    device = HESDevice(
        device_id=device_id,
        device_type=device_type,
        room=room,
        hes_device_type=device_type,
    )
    _dl.add(device)

    result = {"ok": True,
              "msg": f"Registered {device_type} '{device_id}' in {room}",
              "total_devices": len(_dl.devices)}
    _log_call("register_device",
              {"device_type": device_type, "device_id": device_id,
               "room": room}, result)
    return json.dumps(result)


# ---------------------------------------------------------------------------
# MCP Tools: Health Check (NEW for POC7)
# ---------------------------------------------------------------------------

@mcp.tool()
def health_check() -> str:
    """Health check endpoint. Returns server status and device count."""
    result = {
        "status": "ok",
        "device_count": len(_dl.devices),
        "timestamp": time.time(),
        "server_name": "SmartHomeDeviceLayer",
    }
    _log_call("health_check", {}, "ok")
    return json.dumps(result)
