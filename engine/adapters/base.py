"""
Real Device Adapter -- Abstract base class for protocol-specific adapters.

RealDeviceAdapter extends SmartDevice so it plugs directly into DeviceLayer.
Everything above DeviceLayer (MCP, agents, blockchain) is completely unaware
of whether a device is simulated or real.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, Optional

from engine.devices import SmartDevice, Telemetry
from engine.device_config import DeviceConnectionConfig


class RealDeviceAdapter(SmartDevice):
    """Base class for real device adapters.

    Extends SmartDevice with protocol-specific communication.
    Subclasses implement _connect(), _disconnect(), _send_command(),
    and _read_telemetry() for their protocol (MQTT, HTTP, etc.).
    """

    def __init__(self, config: DeviceConnectionConfig):
        super().__init__(config.device_id, config.device_type, config.room)
        self.mode = "real"
        self.config = config
        self._connected = False
        self._last_telemetry: Dict[str, Any] = {}
        self._command_log: list[dict] = []

    # ----- Payload parser (shared by all adapters) -----

    @staticmethod
    def parse_payload(raw: str) -> Dict[str, Any] | None:
        """Parse a protocol payload into a dict.

        IoT devices send data in a handful of well-known formats.
        We support those — not random garbage:

        1. JSON (the standard):  {"temperature": 23.5}
        2. Shell-mangled JSON:   {temperature: 23.5}  or  {'temperature': 23.5}
           (common when sending via CLI tools like mosquitto_pub)
        3. key=value (legacy sensors):  temperature=23.5,humidity=45

        Anything else is rejected — the device should be configured
        to send one of these formats.
        """
        if not raw or not raw.strip():
            return None
        raw = raw.strip()

        # 1. Standard JSON — the expected path
        try:
            obj = json.loads(raw)
            return obj if isinstance(obj, dict) else {"value": obj}
        except (json.JSONDecodeError, ValueError):
            pass

        # 2. Shell-mangled JSON — quotes stripped or single-quoted
        #    This is the #1 real-world issue (PowerShell, bash escaping)
        if raw.startswith("{"):
            # Try single-quote replacement first (simplest fix)
            try:
                obj = json.loads(raw.replace("'", '"'))
                if isinstance(obj, dict):
                    return obj
            except (json.JSONDecodeError, ValueError):
                pass

            # Unquoted keys: {temperature: 23.5, mode: cooling}
            try:
                fixed = re.sub(
                    r'(?<=[{,])\s*([A-Za-z_][A-Za-z0-9_]*)\s*:',
                    r' "\1":', raw,
                )
                fixed = re.sub(
                    r':\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?=[,}])',
                    lambda m: ': "' + m.group(1) + '"'
                    if m.group(1) not in ("true", "false", "null")
                    else ": " + m.group(1),
                    fixed,
                )
                obj = json.loads(fixed)
                if isinstance(obj, dict):
                    return obj
            except (json.JSONDecodeError, ValueError):
                pass

        # 3. key=value pairs — some legacy sensors use this
        if "=" in raw and "{" not in raw:
            try:
                result: Dict[str, Any] = {}
                for pair in re.split(r'[,;\s]+', raw):
                    if "=" not in pair:
                        continue
                    k, v = pair.split("=", 1)
                    k, v = k.strip(), v.strip()
                    try:
                        result[k] = int(v)
                    except ValueError:
                        try:
                            result[k] = float(v)
                        except ValueError:
                            result[k] = v
                if result:
                    return result
            except Exception:
                pass

        # Not a recognized format — reject it
        return None

    # ----- Abstract methods (implement per protocol) -----

    def _connect(self) -> bool:
        """Establish connection to the real device. Returns True on success."""
        raise NotImplementedError

    def _disconnect(self) -> None:
        """Close connection to the real device."""
        raise NotImplementedError

    def _send_command(self, command: str, params: Dict[str, Any]) -> dict:
        """Send a command to the real device via protocol.

        Returns: dict with 'ok' (bool) and 'msg' (str) keys.
        """
        raise NotImplementedError

    def _read_telemetry(self) -> Dict[str, Any]:
        """Read current telemetry from the real device.

        Returns: dict of field_name -> value readings.
        """
        raise NotImplementedError

    # ----- SmartDevice interface -----

    def execute(self, command: str, params: Dict[str, Any] = None) -> dict:
        """Execute command on real device via protocol adapter."""
        p = params or {}
        # Map command names if configured
        mapped_cmd = self.config.command_map.get(command, command)

        try:
            if not self._connected:
                self._connected = self._connect()
            result = self._send_command(mapped_cmd, p)
        except Exception as e:
            result = {"ok": False, "msg": f"Adapter error: {e}"}

        self._command_log.append({
            "command": command,
            "params": p,
            "result": result,
            "timestamp": time.time(),
        })
        return result

    def telemetry(self) -> Telemetry:
        """Read telemetry from real device via protocol adapter."""
        try:
            if not self._connected:
                self._connected = self._connect()
            raw = self._read_telemetry()
        except Exception as e:
            raw = {"error": str(e), **self._last_telemetry}

        # Apply reverse telemetry mapping (protocol_field -> device_field)
        reverse_map = {v: k for k, v in self.config.telemetry_map.items()}
        readings = {}
        for key, value in raw.items():
            mapped_key = reverse_map.get(key, key)
            readings[mapped_key] = value

        # Cache and update state
        self._last_telemetry = readings
        self.state.update(readings)

        return Telemetry(
            device_id=self.device_id,
            device_type=self.device_type,
            timestamp=time.time(),
            readings=readings,
        )

    def shutdown(self) -> None:
        """Disconnect from the real device."""
        try:
            self._disconnect()
        except Exception:
            pass
        self._connected = False

    def check_emergency(self) -> Optional[dict]:
        """Check for emergency conditions using real telemetry data.

        Uses reading-based detection (not device_type matching) so any
        device reporting smoke_level or gas_level_ppm can trigger emergencies.
        """
        readings = self._last_telemetry
        if not readings:
            return None

        # Smoke detection -- reading-based, not type-based
        smoke = readings.get("smoke_level", 0)
        if isinstance(smoke, (int, float)) and smoke >= 0.3:
            return {
                "type": "SMOKE_EMERGENCY",
                "source_device": self.device_id,
                "room": self.room,
                "smoke_level": smoke,
                "immediate_actions": [
                    {"target_type": "smart_lock", "command": "unlock",
                     "reason": "emergency_evacuation"},
                    {"target_type": "smart_light", "command": "turn_on",
                     "reason": "emergency_visibility"},
                    {"target_type": "smart_light", "command": "set_brightness",
                     "params": {"brightness": 100}, "reason": "max_visibility"},
                ],
                "timestamp": time.time(),
            }

        # Gas detection -- reading-based
        gas_ppm = readings.get(
            "gas_level_ppm", readings.get("co_level", 0))
        threshold = {"CO": 50, "CO2": 5000, "NG": 1000}.get(
            readings.get("gas_type", "CO"), 100
        )
        if isinstance(gas_ppm, (int, float)) and gas_ppm >= threshold:
            return {
                "type": "GAS_EMERGENCY",
                "source_device": self.device_id,
                "room": self.room,
                "gas_type": readings.get("gas_type", "CO"),
                "gas_level_ppm": gas_ppm,
                "immediate_actions": [
                    {"target_type": "smart_lock", "command": "unlock",
                     "reason": "emergency_evacuation"},
                    {"target_type": "smart_light", "command": "turn_on",
                     "reason": "emergency_visibility"},
                    {"target_type": "thermostat", "command": "turn_off",
                     "reason": "prevent_gas_ignition"},
                ],
                "timestamp": time.time(),
            }

        return None

    def test_connection(self) -> dict:
        """Test the connection to the real device.

        Returns: dict with 'ok' (bool), 'msg' (str), 'latency_ms' (float).
        """
        start = time.time()
        try:
            self._connected = self._connect()
            latency = (time.time() - start) * 1000
            if self._connected:
                return {"ok": True, "msg": "Connected", "latency_ms": round(latency, 1)}
            return {"ok": False, "msg": "Connection failed", "latency_ms": round(latency, 1)}
        except Exception as e:
            latency = (time.time() - start) * 1000
            return {"ok": False, "msg": str(e), "latency_ms": round(latency, 1)}


class AdapterRegistry:
    """Registry mapping protocol names to adapter classes.

    Usage:
        AdapterRegistry.register("mqtt", MQTTDeviceAdapter)
        adapter = AdapterRegistry.create(config)
    """
    _registry: Dict[str, type] = {}

    @classmethod
    def register(cls, protocol: str, adapter_class: type) -> None:
        """Register an adapter class for a protocol."""
        cls._registry[protocol.lower()] = adapter_class

    @classmethod
    def create(cls, config: DeviceConnectionConfig) -> RealDeviceAdapter:
        """Create an adapter instance from a device connection config."""
        protocol = config.protocol.lower()
        adapter_class = cls._registry.get(protocol)
        if adapter_class is None:
            available = list(cls._registry.keys())
            raise ValueError(
                f"No adapter registered for protocol '{protocol}'. "
                f"Available: {available}"
            )
        adapter = adapter_class(config)
        # Eagerly connect so streaming protocols (MQTT) start receiving
        # data immediately rather than waiting for the first telemetry() call.
        try:
            adapter._connected = adapter._connect()
        except Exception:
            pass
        return adapter

    @classmethod
    def available_protocols(cls) -> list[str]:
        """List all registered protocol names."""
        return sorted(cls._registry.keys())
