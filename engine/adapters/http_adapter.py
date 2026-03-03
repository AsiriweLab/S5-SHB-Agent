"""
HTTP REST Device Adapter -- Connects to real IoT devices via HTTP APIs.

Uses the requests library (already a project dependency).
Reads telemetry via GET and sends commands via POST.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict

import requests

from engine.device_config import DeviceConnectionConfig
from engine.adapters.base import RealDeviceAdapter, AdapterRegistry


class HTTPDeviceAdapter(RealDeviceAdapter):
    """HTTP REST protocol adapter for real IoT devices.

    Connects to devices that expose HTTP APIs (common in modern smart home
    devices like Philips Hue, Shelly, Tasmota, etc.).

    URL convention:
        Telemetry: GET  {base_url}/{config.endpoint}
        Commands:  POST {base_url}/{config.endpoint}/command
    """

    def __init__(self, config: DeviceConnectionConfig):
        super().__init__(config)
        scheme = config.options.get("scheme", "http")
        self._base_url = f"{scheme}://{config.host}:{config.port}"
        self._telemetry_url = f"{self._base_url}/{config.endpoint.lstrip('/')}"
        self._command_url = f"{self._telemetry_url}/command"
        self._session: requests.Session | None = None
        self._timeout = config.options.get("timeout", 10)

    def _get_session(self) -> requests.Session:
        """Get or create an HTTP session with auth configured."""
        if self._session is None:
            self._session = requests.Session()

            # Configure authentication
            auth_type = self.config.auth.get("type", "none")
            if auth_type == "bearer":
                token = self.config.auth.get("token", "")
                self._session.headers["Authorization"] = f"Bearer {token}"
            elif auth_type == "basic":
                self._session.auth = (
                    self.config.auth.get("username", ""),
                    self.config.auth.get("password", ""),
                )
            elif auth_type == "api_key":
                key_name = self.config.auth.get("header", "X-API-Key")
                key_value = self.config.auth.get("key", "")
                self._session.headers[key_name] = key_value

            self._session.headers["Content-Type"] = "application/json"

        return self._session

    def _connect(self) -> bool:
        """Test HTTP connectivity to the device."""
        try:
            session = self._get_session()
            resp = session.get(self._telemetry_url, timeout=self._timeout)
            return resp.status_code == 200
        except requests.ConnectionError:
            return False

    def _disconnect(self) -> None:
        """Close the HTTP session."""
        if self._session is not None:
            self._session.close()
            self._session = None

    def _send_command(self, command: str, params: Dict[str, Any]) -> dict:
        """Send a command via HTTP POST."""
        session = self._get_session()
        payload = {"command": command, "params": params}

        try:
            resp = session.post(
                self._command_url,
                json=payload,
                timeout=self._timeout,
            )
            if resp.status_code == 200:
                body = resp.json()
                return {"ok": body.get("ok", True), "msg": body.get("msg", "OK")}
            return {"ok": False, "msg": f"HTTP {resp.status_code}: {resp.text[:200]}"}
        except requests.RequestException as e:
            return {"ok": False, "msg": f"HTTP error: {e}"}

    def _read_telemetry(self) -> Dict[str, Any]:
        """Read telemetry via HTTP GET — uses shared flexible parser."""
        session = self._get_session()

        try:
            resp = session.get(self._telemetry_url, timeout=self._timeout)
            if resp.status_code == 200:
                parsed = self.parse_payload(resp.text)
                if parsed is not None:
                    return parsed
                return {"error": "unparseable response", "raw": resp.text[:200]}
            return {"error": f"HTTP {resp.status_code}"}
        except requests.RequestException as e:
            return {"error": str(e)}

    def __del__(self):
        """Cleanup HTTP session on garbage collection."""
        try:
            self._disconnect()
        except Exception:
            pass


# Auto-register with the AdapterRegistry
AdapterRegistry.register("http", HTTPDeviceAdapter)
