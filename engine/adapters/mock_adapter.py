"""
Mock Device Adapter -- For testing the real-device code path without hardware.

Records all commands and returns configurable telemetry.
Useful for integration tests and CI/CD pipelines.
"""

from __future__ import annotations

import time
import random
from typing import Any, Dict

from engine.device_config import DeviceConnectionConfig
from engine.adapters.base import RealDeviceAdapter, AdapterRegistry


class MockDeviceAdapter(RealDeviceAdapter):
    """Mock adapter that simulates a real device through the adapter pipeline.

    Unlike simulation-mode devices (which bypass the adapter layer entirely),
    MockDeviceAdapter exercises the full RealDeviceAdapter code path —
    connect, send_command, read_telemetry — using in-memory state.
    """

    def __init__(self, config: DeviceConnectionConfig):
        super().__init__(config)
        self._mock_state: Dict[str, Any] = {}
        self._received_commands: list[dict] = []
        self._init_mock_state()

    def _init_mock_state(self) -> None:
        """Initialize mock state based on device type."""
        defaults = {
            "thermostat": {"current_temp": 24.0, "target_temp": 22.0,
                          "mode": "cool", "is_on": True},
            "door_lock": {"is_locked": True, "battery_pct": 95},
            "smart_light": {"is_on": True, "brightness": 80},
            "smoke_sensor": {"smoke_level": 0.0, "alarm_active": False},
            "gas_sensor": {"gas_level_ppm": 0, "gas_type": "CO",
                          "alarm_active": False},
            "motion_sensor": {"motion_detected": False, "confidence": 0.0},
            "camera": {"is_recording": False, "motion_detected": False},
            "smart_plug": {"is_on": True, "power_watts": 60.0,
                          "voltage": 220.0},
            "hvac": {"current_temp": 24.0, "target_temp": 22.0,
                    "humidity": 45.0, "mode": "auto", "is_on": True},
            "smart_appliance": {"status": "ok", "runtime_hours": 500,
                               "is_on": True},
        }
        self._mock_state = defaults.get(self.device_type, {"status": "ok"})
        self.state.update(self._mock_state)

    def _connect(self) -> bool:
        """Mock connection always succeeds."""
        return True

    def _disconnect(self) -> None:
        """Mock disconnect is a no-op."""
        pass

    def _send_command(self, command: str, params: Dict[str, Any]) -> dict:
        """Process command against mock state."""
        self._received_commands.append({
            "command": command,
            "params": params,
            "timestamp": time.time(),
        })

        # Handle common commands by device type
        if command == "set_temperature" and "temperature" in params:
            self._mock_state["target_temp"] = params["temperature"]
            return {"ok": True, "msg": f"Target -> {params['temperature']}C"}

        if command in ("turn_on", "turn_off"):
            self._mock_state["is_on"] = command == "turn_on"
            return {"ok": True, "msg": f"Device {'on' if command == 'turn_on' else 'off'}"}

        if command == "lock":
            self._mock_state["is_locked"] = True
            return {"ok": True, "msg": "Locked"}

        if command == "unlock":
            self._mock_state["is_locked"] = False
            return {"ok": True, "msg": "Unlocked"}

        if command == "set_brightness" and "brightness" in params:
            self._mock_state["brightness"] = params["brightness"]
            return {"ok": True, "msg": f"Brightness -> {params['brightness']}%"}

        if command == "start_recording":
            self._mock_state["is_recording"] = True
            return {"ok": True, "msg": "Recording started"}

        if command == "stop_recording":
            self._mock_state["is_recording"] = False
            return {"ok": True, "msg": "Recording stopped"}

        if command == "silence_alarm":
            self._mock_state["alarm_active"] = False
            return {"ok": True, "msg": "Alarm silenced"}

        return {"ok": True, "msg": f"Mock executed: {command}"}

    def _read_telemetry(self) -> Dict[str, Any]:
        """Return mock state with slight randomization for realism."""
        readings = dict(self._mock_state)

        # Add realistic noise to numeric values
        if "current_temp" in readings:
            readings["current_temp"] = round(
                readings["current_temp"] + random.uniform(-0.2, 0.2), 1
            )
        if "power_watts" in readings and readings.get("is_on"):
            readings["power_watts"] = round(
                readings["power_watts"] + random.uniform(-1.5, 1.5), 1
            )
        if "humidity" in readings:
            readings["humidity"] = round(
                readings["humidity"] + random.uniform(-0.3, 0.3), 1
            )

        return readings

    # ----- Test helpers -----

    def get_received_commands(self) -> list[dict]:
        """Return all commands received by this mock device."""
        return list(self._received_commands)

    def inject_state(self, **kwargs: Any) -> None:
        """Inject state values for testing (e.g., inject_state(smoke_level=0.8))."""
        self._mock_state.update(kwargs)
        self.state.update(kwargs)


# Auto-register with the AdapterRegistry
AdapterRegistry.register("mock", MockDeviceAdapter)
