"""
Layer 1: Device Layer -- Smart Home IoT Device Simulation

POC5: Added SmartPlug, HVAC, SmartAppliance for 7-agent scenarios.
POC4: Added GasSensor, MotionSensor, Camera for multi-agent scenarios.

Responsibilities:
- Direct command execution with sub-millisecond latency
- Telemetry generation (sensor readings)
- SAFETY-CRITICAL: Emergency fallback rules that execute WITHOUT AI or blockchain
"""

import time
import random
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List


@dataclass
class Telemetry:
    device_id: str
    device_type: str
    timestamp: float
    readings: Dict[str, Any]

    def summary(self) -> str:
        parts = [f"{k}={v}" for k, v in self.readings.items()]
        return f"[{self.device_type}:{self.device_id}] {', '.join(parts)}"


# ---------------------------------------------------------------------------
# Individual Device Types
# ---------------------------------------------------------------------------

class SmartDevice:
    def __init__(self, device_id: str, device_type: str, room: str):
        self.device_id = device_id
        self.device_type = device_type
        self.room = room
        self.mode: str = "simulation"      # "simulation" or "real"
        self.state: Dict[str, Any] = {}
        self._hes_backed: bool = False     # True when receiving HES telemetry

    def update_from_hes(self, readings: Dict[str, Any]) -> None:
        """Update device state from S5-HES-Agent telemetry data."""
        self._hes_backed = True
        self.state.update(readings)

    def shutdown(self) -> None:
        """Clean up resources. Override in subclasses that hold connections."""
        pass

    def execute(self, command: str, params: Dict[str, Any] = None) -> dict:
        raise NotImplementedError

    def telemetry(self) -> Telemetry:
        raise NotImplementedError

    def check_emergency(self) -> Optional[dict]:
        return None


class Thermostat(SmartDevice):
    def __init__(self, device_id: str, room: str, initial_temp: float = 28.0):
        super().__init__(device_id, "thermostat", room)
        self.state = {"current_temp": initial_temp, "target_temp": 24.0,
                      "mode": "cool", "is_on": True}

    def execute(self, command: str, params: Dict[str, Any] = None) -> dict:
        p = params or {}
        if command == "set_temperature":
            old = self.state["target_temp"]
            self.state["target_temp"] = p["temperature"]
            return {"ok": True, "msg": f"Target {old}->{p['temperature']}C"}
        if command == "turn_off":
            self.state["is_on"] = False
            return {"ok": True, "msg": "Thermostat off"}
        return {"ok": False, "msg": f"Unknown: {command}"}

    def telemetry(self) -> Telemetry:
        if not self._hes_backed:
            self.state["current_temp"] += random.uniform(-0.3, 0.3)
            self.state["current_temp"] = round(self.state["current_temp"], 1)
        return Telemetry(self.device_id, self.device_type, time.time(),
                         {k: v for k, v in self.state.items()})


class DoorLock(SmartDevice):
    def __init__(self, device_id: str, room: str):
        super().__init__(device_id, "door_lock", room)
        self.state = {"is_locked": True}

    def execute(self, command: str, params: Dict[str, Any] = None) -> dict:
        if command == "lock":
            self.state["is_locked"] = True
            return {"ok": True, "msg": "Door locked"}
        if command == "unlock":
            self.state["is_locked"] = False
            return {"ok": True, "msg": "Door unlocked"}
        return {"ok": False, "msg": f"Unknown: {command}"}

    def telemetry(self) -> Telemetry:
        if self._hes_backed:
            return Telemetry(self.device_id, self.device_type, time.time(),
                             {k: v for k, v in self.state.items()})
        return Telemetry(self.device_id, self.device_type, time.time(),
                         {"is_locked": self.state["is_locked"],
                          "battery_pct": random.randint(75, 100)})


class SmartLight(SmartDevice):
    def __init__(self, device_id: str, room: str):
        super().__init__(device_id, "smart_light", room)
        self.state = {"is_on": True, "brightness": 80}

    def execute(self, command: str, params: Dict[str, Any] = None) -> dict:
        p = params or {}
        if command == "turn_on":
            self.state["is_on"] = True
            return {"ok": True, "msg": "Light on"}
        if command == "turn_off":
            self.state["is_on"] = False
            return {"ok": True, "msg": "Light off"}
        if command == "set_brightness":
            self.state["brightness"] = p.get("brightness", 80)
            return {"ok": True, "msg": f"Brightness -> {self.state['brightness']}%"}
        return {"ok": False, "msg": f"Unknown: {command}"}

    def telemetry(self) -> Telemetry:
        watts = self.state["brightness"] * 0.12 if self.state["is_on"] else 0
        return Telemetry(self.device_id, self.device_type, time.time(),
                         {"is_on": self.state["is_on"],
                          "brightness": self.state["brightness"],
                          "power_watts": round(watts, 1)})


class SmokeSensor(SmartDevice):
    SMOKE_THRESHOLD = 0.3

    def __init__(self, device_id: str, room: str):
        super().__init__(device_id, "smoke_sensor", room)
        self.state = {"smoke_level": 0.0, "alarm_active": False}

    def inject_smoke(self, level: float = 0.8):
        self.state["smoke_level"] = level

    def clear_smoke(self):
        self.state["smoke_level"] = 0.0
        self.state["alarm_active"] = False

    def execute(self, command: str, params: Dict[str, Any] = None) -> dict:
        if command == "silence_alarm":
            self.state["alarm_active"] = False
            return {"ok": True, "msg": "Alarm silenced"}
        return {"ok": False, "msg": f"Unknown: {command}"}

    def telemetry(self) -> Telemetry:
        if self._hes_backed:
            return Telemetry(self.device_id, self.device_type, time.time(),
                             {k: v for k, v in self.state.items()})
        noise = random.uniform(0, 0.02) if self.state["smoke_level"] < 0.1 else 0
        return Telemetry(self.device_id, self.device_type, time.time(),
                         {"smoke_level": round(self.state["smoke_level"] + noise, 3),
                          "alarm_active": self.state["alarm_active"]})

    def check_emergency(self) -> Optional[dict]:
        """SAFETY CRITICAL -- runs at device firmware level, no AI needed."""
        if self.state["smoke_level"] >= self.SMOKE_THRESHOLD:
            self.state["alarm_active"] = True
            return {
                "type": "SMOKE_EMERGENCY",
                "source_device": self.device_id,
                "room": self.room,
                "smoke_level": self.state["smoke_level"],
                "immediate_actions": [
                    {"target_type": "door_lock", "command": "unlock",
                     "reason": "emergency_evacuation"},
                    {"target_type": "smart_light", "command": "turn_on",
                     "reason": "emergency_visibility"},
                    {"target_type": "smart_light", "command": "set_brightness",
                     "params": {"brightness": 100}, "reason": "max_visibility"},
                ],
                "timestamp": time.time(),
            }
        return None


# ---------------------------------------------------------------------------
# NEW POC4 Device Types
# ---------------------------------------------------------------------------

class GasSensor(SmartDevice):
    """SAFETY CRITICAL -- gas leak detection (CO, CO2, Natural Gas)."""
    GAS_THRESHOLD_PPM = {"CO": 50, "CO2": 5000, "NG": 1000}

    def __init__(self, device_id: str, room: str, gas_type: str = "CO"):
        super().__init__(device_id, "gas_sensor", room)
        self.state = {"gas_level_ppm": 0, "gas_type": gas_type,
                      "alarm_active": False}

    def inject_gas(self, level_ppm: int = 200):
        """Simulate gas leak for demo."""
        self.state["gas_level_ppm"] = level_ppm

    def clear_gas(self):
        self.state["gas_level_ppm"] = 0
        self.state["alarm_active"] = False

    def execute(self, command: str, params: Dict[str, Any] = None) -> dict:
        if command == "silence_alarm":
            self.state["alarm_active"] = False
            return {"ok": True, "msg": "Gas alarm silenced"}
        return {"ok": False, "msg": f"Unknown: {command}"}

    def telemetry(self) -> Telemetry:
        if self._hes_backed:
            return Telemetry(self.device_id, self.device_type, time.time(),
                             {k: v for k, v in self.state.items()})
        noise = random.randint(0, 3) if self.state["gas_level_ppm"] < 10 else 0
        return Telemetry(self.device_id, self.device_type, time.time(),
                         {"gas_level_ppm": self.state["gas_level_ppm"] + noise,
                          "gas_type": self.state["gas_type"],
                          "alarm_active": self.state["alarm_active"]})

    def check_emergency(self) -> Optional[dict]:
        """SAFETY CRITICAL -- firmware-level gas detection."""
        threshold = self.GAS_THRESHOLD_PPM.get(self.state["gas_type"], 100)
        if self.state["gas_level_ppm"] >= threshold:
            self.state["alarm_active"] = True
            return {
                "type": "GAS_EMERGENCY",
                "source_device": self.device_id,
                "room": self.room,
                "gas_type": self.state["gas_type"],
                "gas_level_ppm": self.state["gas_level_ppm"],
                "immediate_actions": [
                    {"target_type": "door_lock", "command": "unlock",
                     "reason": "emergency_evacuation"},
                    {"target_type": "smart_light", "command": "turn_on",
                     "reason": "emergency_visibility"},
                    {"target_type": "smart_light", "command": "set_brightness",
                     "params": {"brightness": 100}, "reason": "max_visibility"},
                    {"target_type": "thermostat", "command": "turn_off",
                     "reason": "prevent_gas_ignition"},
                ],
                "timestamp": time.time(),
            }
        return None


class MotionSensor(SmartDevice):
    """PIR motion detector for occupancy/intrusion detection."""

    def __init__(self, device_id: str, room: str):
        super().__init__(device_id, "motion_sensor", room)
        self.state = {"motion_detected": False, "confidence": 0.0,
                      "last_motion_time": 0.0}

    def trigger_motion(self, confidence: float = 0.85):
        """Simulate motion detection."""
        self.state["motion_detected"] = True
        self.state["confidence"] = confidence
        self.state["last_motion_time"] = time.time()

    def clear_motion(self):
        self.state["motion_detected"] = False
        self.state["confidence"] = 0.0

    def execute(self, command: str, params: Dict[str, Any] = None) -> dict:
        if command == "reset":
            self.clear_motion()
            return {"ok": True, "msg": "Motion sensor reset"}
        return {"ok": False, "msg": f"Unknown: {command}"}

    def telemetry(self) -> Telemetry:
        return Telemetry(self.device_id, self.device_type, time.time(),
                         {"motion_detected": self.state["motion_detected"],
                          "confidence": self.state["confidence"],
                          "last_motion_time": self.state["last_motion_time"]})


class Camera(SmartDevice):
    """Smart camera with recording and detection capabilities."""

    def __init__(self, device_id: str, room: str):
        super().__init__(device_id, "camera", room)
        self.state = {"is_recording": False, "motion_detected": False,
                      "person_detected": False}

    def execute(self, command: str, params: Dict[str, Any] = None) -> dict:
        if command == "start_recording":
            self.state["is_recording"] = True
            return {"ok": True, "msg": "Camera recording started"}
        if command == "stop_recording":
            self.state["is_recording"] = False
            return {"ok": True, "msg": "Camera recording stopped"}
        return {"ok": False, "msg": f"Unknown: {command}"}

    def simulate_detection(self, motion: bool = True, person: bool = False):
        """Simulate camera detecting motion/person."""
        self.state["motion_detected"] = motion
        self.state["person_detected"] = person

    def telemetry(self) -> Telemetry:
        return Telemetry(self.device_id, self.device_type, time.time(),
                         {"is_recording": self.state["is_recording"],
                          "motion_detected": self.state["motion_detected"],
                          "person_detected": self.state["person_detected"]})


# ---------------------------------------------------------------------------
# NEW POC5 Device Types
# ---------------------------------------------------------------------------

class SmartPlug(SmartDevice):
    """Smart plug with power monitoring for energy management."""

    def __init__(self, device_id: str, room: str,
                 initial_watts: float = 60.0):
        super().__init__(device_id, "smart_plug", room)
        self.state = {
            "is_on": True,
            "power_watts": initial_watts,
            "voltage": 220.0,
            "current_amps": round(initial_watts / 220.0, 2),
            "mode": "standard",
        }

    def execute(self, command: str, params: Dict[str, Any] = None) -> dict:
        p = params or {}
        if command == "turn_on":
            self.state["is_on"] = True
            return {"ok": True, "msg": "Plug on"}
        if command == "turn_off":
            self.state["is_on"] = False
            self.state["power_watts"] = 0
            self.state["current_amps"] = 0
            return {"ok": True, "msg": "Plug off"}
        if command == "set_mode":
            mode = p.get("mode", "standard")
            self.state["mode"] = mode
            base = 60.0
            if mode == "eco":
                base *= 0.6
            elif mode == "performance":
                base *= 1.3
            if self.state["is_on"]:
                self.state["power_watts"] = round(base, 1)
                self.state["current_amps"] = round(base / 220.0, 2)
            return {"ok": True, "msg": f"Mode -> {mode}"}
        return {"ok": False, "msg": f"Unknown: {command}"}

    def telemetry(self) -> Telemetry:
        if not self._hes_backed and self.state["is_on"]:
            self.state["power_watts"] += random.uniform(-2, 2)
            self.state["power_watts"] = max(0, round(
                self.state["power_watts"], 1))
            self.state["voltage"] = round(220 + random.uniform(-1, 1), 1)
            self.state["current_amps"] = round(
                self.state["power_watts"] / self.state["voltage"], 2)
        return Telemetry(self.device_id, self.device_type, time.time(),
                         {k: v for k, v in self.state.items()})


class HVACSystem(SmartDevice):
    """HVAC system for climate control (heating, cooling, humidity)."""

    def __init__(self, device_id: str, room: str,
                 initial_temp: float = 24.0):
        super().__init__(device_id, "hvac", room)
        self.state = {
            "current_temp": initial_temp,
            "target_temp": 22.0,
            "humidity": 45.0,
            "mode": "auto",
            "fan_speed": "medium",
            "is_on": True,
        }

    def execute(self, command: str, params: Dict[str, Any] = None) -> dict:
        p = params or {}
        if command == "set_temperature":
            old = self.state["target_temp"]
            self.state["target_temp"] = p["temperature"]
            return {"ok": True,
                    "msg": f"HVAC target {old}->{p['temperature']}C"}
        if command == "set_mode":
            self.state["mode"] = p.get("mode", "auto")
            return {"ok": True,
                    "msg": f"HVAC mode -> {self.state['mode']}"}
        if command == "set_fan_speed":
            self.state["fan_speed"] = p.get("fan_speed", "medium")
            return {"ok": True,
                    "msg": f"Fan speed -> {self.state['fan_speed']}"}
        if command == "turn_off":
            self.state["is_on"] = False
            return {"ok": True, "msg": "HVAC off"}
        if command == "turn_on":
            self.state["is_on"] = True
            return {"ok": True, "msg": "HVAC on"}
        return {"ok": False, "msg": f"Unknown: {command}"}

    def telemetry(self) -> Telemetry:
        if not self._hes_backed and self.state["is_on"]:
            self.state["current_temp"] += random.uniform(-0.3, 0.3)
            self.state["humidity"] += random.uniform(-0.5, 0.5)
            self.state["current_temp"] = round(
                self.state["current_temp"], 1)
            self.state["humidity"] = round(
                max(20, min(80, self.state["humidity"])), 1)
        return Telemetry(self.device_id, self.device_type, time.time(),
                         {k: v for k, v in self.state.items()})


class SmartAppliance(SmartDevice):
    """Smart appliance with maintenance tracking and health monitoring."""

    def __init__(self, device_id: str, room: str,
                 appliance_type: str = "washer"):
        super().__init__(device_id, "smart_appliance", room)
        self.state = {
            "status": "ok",
            "runtime_hours": random.randint(100, 5000),
            "last_service_ts": time.time() - random.randint(86400, 2592000),
            "appliance_type": appliance_type,
            "is_on": True,
        }

    def inject_degradation(self, status: str = "warning"):
        """Simulate appliance degradation for demo."""
        self.state["status"] = status

    def execute(self, command: str, params: Dict[str, Any] = None) -> dict:
        if command == "report_status":
            return {"ok": True,
                    "msg": f"Status: {self.state['status']}, "
                    f"runtime: {self.state['runtime_hours']}h"}
        if command == "trigger_maintenance_mode":
            self.state["is_on"] = False
            self.state["status"] = "maintenance"
            return {"ok": True, "msg": "Appliance in maintenance mode"}
        if command == "reset_runtime":
            self.state["runtime_hours"] = 0
            self.state["last_service_ts"] = time.time()
            self.state["status"] = "ok"
            return {"ok": True, "msg": "Runtime reset, status OK"}
        return {"ok": False, "msg": f"Unknown: {command}"}

    def telemetry(self) -> Telemetry:
        if not self._hes_backed and self.state["is_on"]:
            self.state["runtime_hours"] += random.uniform(0, 0.1)
            self.state["runtime_hours"] = round(
                self.state["runtime_hours"], 1)
        return Telemetry(self.device_id, self.device_type, time.time(),
                         {k: v for k, v in self.state.items()})


# ---------------------------------------------------------------------------
# HES-backed universal device
# ---------------------------------------------------------------------------

class HESDevice(SmartDevice):
    """Universal device backed by S5-HES-Agent telemetry.

    All devices in the production pipeline are HESDevice instances.
    Stores HES telemetry readings directly; no local simulation noise.
    Supports generic command execution via state mutation.
    """

    def __init__(self, device_id: str, device_type: str, room: str,
                 hes_device_type: str = ""):
        super().__init__(device_id, device_type, room)
        self.hes_device_type = hes_device_type
        self._hes_backed = True
        self.state = {"status": "waiting_for_data"}

    def execute(self, command: str, params: Dict[str, Any] = None) -> dict:
        p = params or {}
        if command == "turn_on":
            self.state["is_on"] = True
            return {"ok": True, "msg": f"{self.device_type} turned on"}
        if command == "turn_off":
            self.state["is_on"] = False
            return {"ok": True, "msg": f"{self.device_type} turned off"}
        if command == "set_state":
            self.state.update(p)
            return {"ok": True, "msg": f"State updated: {list(p.keys())}"}
        if command == "set_temperature":
            self.state["target_temp"] = p.get("temperature", 22)
            return {"ok": True,
                    "msg": f"Target temp -> {self.state['target_temp']}C"}
        if command == "lock":
            self.state["is_locked"] = True
            return {"ok": True, "msg": "Locked"}
        if command == "unlock":
            self.state["is_locked"] = False
            return {"ok": True, "msg": "Unlocked"}
        if command == "set_brightness":
            self.state["brightness"] = p.get("brightness", 80)
            return {"ok": True,
                    "msg": f"Brightness -> {self.state['brightness']}%"}
        if command == "set_mode":
            self.state["mode"] = p.get("mode", "auto")
            return {"ok": True, "msg": f"Mode -> {self.state['mode']}"}
        if command == "start_recording":
            self.state["is_recording"] = True
            return {"ok": True, "msg": "Recording started"}
        if command == "stop_recording":
            self.state["is_recording"] = False
            return {"ok": True, "msg": "Recording stopped"}
        if command == "silence_alarm":
            self.state["alarm_active"] = False
            return {"ok": True, "msg": "Alarm silenced"}
        if command == "reset":
            return {"ok": True, "msg": "Device reset"}
        if command == "report_status":
            return {"ok": True, "msg": f"Status: {self.state}"}
        if command == "trigger_maintenance_mode":
            self.state["is_on"] = False
            self.state["status"] = "maintenance"
            return {"ok": True, "msg": "Maintenance mode"}
        if command == "set_fan_speed":
            self.state["fan_speed"] = p.get("fan_speed", "medium")
            return {"ok": True,
                    "msg": f"Fan speed -> {self.state['fan_speed']}"}
        # Fallback: apply params as state updates
        if p:
            self.state.update(p)
            return {"ok": True,
                    "msg": f"Command '{command}' applied with params"}
        return {"ok": True, "msg": f"Command '{command}' acknowledged"}

    def check_emergency(self) -> Optional[dict]:
        """Check HES telemetry readings for emergency conditions."""
        smoke = self.state.get("smoke_level", 0)
        if isinstance(smoke, (int, float)) and smoke >= 0.3:
            self.state["alarm_active"] = True
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
                ],
                "timestamp": time.time(),
            }
        gas_ppm = self.state.get(
            "gas_level_ppm", self.state.get("co_level", 0))
        if isinstance(gas_ppm, (int, float)) and gas_ppm >= 50:
            self.state["alarm_active"] = True
            return {
                "type": "GAS_EMERGENCY",
                "source_device": self.device_id,
                "room": self.room,
                "gas_level_ppm": gas_ppm,
                "immediate_actions": [
                    {"target_type": "smart_lock", "command": "unlock",
                     "reason": "emergency_evacuation"},
                    {"target_type": "smart_light", "command": "turn_on",
                     "reason": "emergency_visibility"},
                ],
                "timestamp": time.time(),
            }
        return None

    def telemetry(self) -> Telemetry:
        return Telemetry(self.device_id, self.device_type, time.time(),
                         {k: v for k, v in self.state.items()})


# ---------------------------------------------------------------------------
# Device Layer Manager
# ---------------------------------------------------------------------------

class DeviceLayer:
    """Layer 1 manager -- owns all devices, handles emergencies."""

    def __init__(self):
        self.devices: Dict[str, SmartDevice] = {}
        self.emergency_log: List[dict] = []

    def add(self, device: SmartDevice):
        self.devices[device.device_id] = device

    def shutdown_all(self) -> None:
        """Disconnect all real-device adapters (MQTT, HTTP, etc.)."""
        for dev in self.devices.values():
            try:
                dev.shutdown()
            except Exception:
                pass

    def get_all_telemetry(self) -> List[Telemetry]:
        return [d.telemetry() for d in self.devices.values()]

    def update_device_from_hes(self, device_id: str,
                                readings: Dict[str, Any]) -> bool:
        """Update a device's state from HES telemetry. Returns True if found."""
        dev = self.devices.get(device_id)
        if dev:
            dev.update_from_hes(readings)
            return True
        return False

    def execute(self, device_id: str, command: str,
                params: Dict[str, Any] = None) -> dict:
        dev = self.devices.get(device_id)
        if not dev:
            return {"ok": False, "msg": f"Device '{device_id}' not found"}
        return dev.execute(command, params or {})

    def scan_emergencies(self) -> List[dict]:
        """Check ALL devices. If emergency, execute immediate actions
        BYPASSING AI + chain."""
        emergencies = []
        for dev in self.devices.values():
            em = dev.check_emergency()
            if em:
                emergencies.append(em)
                self.emergency_log.append(em)
                self._execute_emergency(em)
        return emergencies

    def _execute_emergency(self, emergency: dict):
        for action in emergency.get("immediate_actions", []):
            for dev in self.devices.values():
                if dev.device_type == action["target_type"]:
                    dev.execute(action["command"], action.get("params", {}))

    # Fallback rules for when agent is offline (reading-based, not type-based)
    def apply_fallback_rules(self) -> List[dict]:
        """Execute pre-programmed rules when AI agent is unavailable.

        Uses reading-based checks (current_temp) instead of device_type matching,
        so any device reporting temperature can trigger fallback rules.
        """
        actions_taken = []
        for dev in self.devices.values():
            temp = dev.state.get("current_temp")
            if temp is not None and isinstance(temp, (int, float)):
                if temp > 30:
                    dev.execute("set_temperature", {"temperature": 24})
                    actions_taken.append({"device": dev.device_id,
                                          "rule": "temp_above_30",
                                          "action": "set 24C"})
                elif temp < 16:
                    dev.execute("set_temperature", {"temperature": 22})
                    actions_taken.append({"device": dev.device_id,
                                          "rule": "temp_below_16",
                                          "action": "set 22C"})
        return actions_taken


