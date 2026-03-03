"""
MQTT Device Adapter -- Connects to real IoT devices via MQTT broker.

Uses paho-mqtt (optional dependency). Falls back gracefully if not installed.
Subscribes to telemetry topics and publishes commands to command topics.
"""

from __future__ import annotations

import json
import os
import time
import threading
from typing import Any, Dict

from loguru import logger
from engine.device_config import DeviceConnectionConfig
from engine.adapters.base import RealDeviceAdapter, AdapterRegistry

try:
    import paho.mqtt.client as mqtt
    PAHO_AVAILABLE = True
except ImportError:
    PAHO_AVAILABLE = False


class MQTTDeviceAdapter(RealDeviceAdapter):
    """MQTT protocol adapter for real IoT devices.

    Connects to an MQTT broker, subscribes to a telemetry topic,
    and publishes commands to a command topic.

    Topic convention:
        Telemetry: {config.topic}          (e.g., home/living_room/thermostat)
        Commands:  {config.topic}/command   (e.g., home/living_room/thermostat/command)
    """

    def __init__(self, config: DeviceConnectionConfig):
        if not PAHO_AVAILABLE:
            raise ImportError(
                "paho-mqtt is required for MQTT adapters. "
                "Install with: pip install paho-mqtt"
            )
        super().__init__(config)
        self._client: mqtt.Client | None = None
        self._latest_payload: Dict[str, Any] = {}
        self._payload_lock = threading.Lock()
        self._command_topic = f"{config.topic}/command"

    def _on_connect(self, client: Any, userdata: Any, flags: Any, rc: int) -> None:
        """Callback when MQTT client connects to broker."""
        if rc == 0:
            logger.info(f"MQTT [{self.device_id}] connected to {self.config.host}:{self.config.port}")
            client.subscribe(self.config.topic, qos=1)
            logger.info(f"MQTT [{self.device_id}] subscribed to '{self.config.topic}'")
        else:
            logger.warning(f"MQTT [{self.device_id}] connect failed (rc={rc})")

    def _on_message(self, client: Any, userdata: Any, msg: Any) -> None:
        """Callback for incoming MQTT messages — uses shared flexible parser."""
        try:
            raw = msg.payload.decode("utf-8").strip()
        except UnicodeDecodeError:
            logger.warning(f"MQTT [{self.device_id}] cannot decode payload bytes")
            return

        payload = self.parse_payload(raw)
        if payload is not None:
            logger.debug(f"MQTT [{self.device_id}] received: {payload}")
            with self._payload_lock:
                self._latest_payload = payload
        else:
            logger.warning(f"MQTT [{self.device_id}] unparseable payload: {raw[:200]}")

    def _connect(self) -> bool:
        """Connect to the MQTT broker and subscribe to telemetry topic."""
        if self._client is not None:
            return True

        # Unique client_id per connection to avoid broker conflicts on reconnect
        uid = f"{os.getpid()}-{int(time.time() * 1000) % 100000}"
        cid = f"s5-abc-{self.device_id}-{uid}"

        # paho-mqtt v2 requires callback_api_version as first argument
        if hasattr(mqtt, 'CallbackAPIVersion'):
            client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION1,
                client_id=cid,
                protocol=mqtt.MQTTv311,
            )
        else:
            client = mqtt.Client(
                client_id=cid,
                protocol=mqtt.MQTTv311,
            )

        # Apply authentication if configured
        username = self.config.auth.get("username")
        password = self.config.auth.get("password")
        if username:
            client.username_pw_set(username, password)

        # TLS if configured
        if self.config.options.get("tls"):
            client.tls_set()

        client.on_connect = self._on_connect
        client.on_message = self._on_message

        try:
            logger.info(f"MQTT [{self.device_id}] connecting to {self.config.host}:{self.config.port} topic='{self.config.topic}'")
            client.connect(self.config.host, self.config.port, keepalive=60)
            client.loop_start()
            self._client = client
            return True
        except Exception as e:
            logger.error(f"MQTT [{self.device_id}] connect error: {e}")
            return False

    def _disconnect(self) -> None:
        """Disconnect from the MQTT broker."""
        if self._client is not None:
            self._client.loop_stop()
            self._client.disconnect()
            self._client = None

    def _send_command(self, command: str, params: Dict[str, Any]) -> dict:
        """Publish a command to the device's command topic."""
        if self._client is None:
            return {"ok": False, "msg": "Not connected to MQTT broker"}

        payload = json.dumps({"command": command, "params": params})
        result = self._client.publish(self._command_topic, payload, qos=1)

        if result.rc == 0:
            return {"ok": True, "msg": f"Published {command} to {self._command_topic}"}
        return {"ok": False, "msg": f"MQTT publish failed (rc={result.rc})"}

    def _read_telemetry(self) -> Dict[str, Any]:
        """Read the latest MQTT payload received on the telemetry topic."""
        with self._payload_lock:
            return dict(self._latest_payload)

    def __del__(self):
        """Cleanup MQTT connection on garbage collection."""
        try:
            self._disconnect()
        except Exception:
            pass


# Auto-register with the AdapterRegistry
AdapterRegistry.register("mqtt", MQTTDeviceAdapter)
