"""
Device Configuration -- Dual-mode device support (Simulation / Real / Hybrid).

Defines configuration models for connecting to real IoT devices via
protocol adapters (MQTT, HTTP) alongside simulated devices.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class DeviceMode(str, Enum):
    """Operating mode for devices in a session."""
    SIMULATION = "simulation"
    REAL = "real"
    HYBRID = "hybrid"


@dataclass
class DeviceConnectionConfig:
    """Connection configuration for a single real device.

    Maps a device_id to a protocol adapter with connection details
    and field mappings for telemetry/command translation.
    """
    device_id: str
    device_type: str
    room: str
    protocol: str                          # "mqtt", "http", "mock"
    host: str = "localhost"
    port: int = 1883
    # Protocol-specific fields
    topic: str = ""                        # MQTT topic
    endpoint: str = ""                     # HTTP endpoint path
    # Authentication
    auth: Dict[str, str] = field(default_factory=dict)
    # Field mapping: device_field -> protocol_field
    telemetry_map: Dict[str, str] = field(default_factory=dict)
    command_map: Dict[str, str] = field(default_factory=dict)
    # Extra protocol-specific options
    options: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "device_id": self.device_id,
            "device_type": self.device_type,
            "room": self.room,
            "protocol": self.protocol,
            "host": self.host,
            "port": self.port,
            "topic": self.topic,
            "endpoint": self.endpoint,
            "auth": self.auth,
            "telemetry_map": self.telemetry_map,
            "command_map": self.command_map,
            "options": self.options,
        }

    @classmethod
    def from_dict(cls, d: dict) -> DeviceConnectionConfig:
        return cls(
            device_id=d["device_id"],
            device_type=d.get("device_type", ""),
            room=d.get("room", "unknown"),
            protocol=d.get("protocol", "mock"),
            host=d.get("host", "localhost"),
            port=d.get("port", 1883),
            topic=d.get("topic", ""),
            endpoint=d.get("endpoint", ""),
            auth=d.get("auth", {}),
            telemetry_map=d.get("telemetry_map", {}),
            command_map=d.get("command_map", {}),
            options=d.get("options", {}),
        )


@dataclass
class SessionDeviceConfig:
    """Session-level device mode configuration.

    Stored alongside home_config.json in the session directory
    and restored on session resume.
    """
    mode: DeviceMode = DeviceMode.SIMULATION
    real_devices: List[DeviceConnectionConfig] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "mode": self.mode.value,
            "real_devices": [d.to_dict() for d in self.real_devices],
        }

    @classmethod
    def from_dict(cls, d: dict) -> SessionDeviceConfig:
        mode = DeviceMode(d.get("mode", "simulation"))
        real_devices = [
            DeviceConnectionConfig.from_dict(rd)
            for rd in d.get("real_devices", [])
        ]
        return cls(mode=mode, real_devices=real_devices)

    def get_real_device_ids(self) -> set[str]:
        """Return set of device_ids configured for real connection."""
        return {d.device_id for d in self.real_devices}

    def get_config_for_device(self, device_id: str) -> Optional[DeviceConnectionConfig]:
        """Get connection config for a specific device, or None if simulated."""
        for d in self.real_devices:
            if d.device_id == device_id:
                return d
        return None
