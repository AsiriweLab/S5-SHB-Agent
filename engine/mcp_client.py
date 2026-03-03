"""
MCP Client: Synchronous wrapper for async FastMCP Client.

POC7: Supports both in-process and stdio transport.
      Uses PERSISTENT connection (critical for stdio -- avoids spawning
      a new subprocess per call).
      Adds ping() for health monitoring.
"""

import asyncio
import json
from typing import Dict, Any, List, Optional
from dataclasses import dataclass

from fastmcp import Client


# ---------------------------------------------------------------------------
# Telemetry mirror (identical fields to devices.Telemetry so that
# agent.perceive_and_decide() works without change)
# ---------------------------------------------------------------------------

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
# Transport Factory (NEW POC7)
# ---------------------------------------------------------------------------

def create_mcp_client(transport_mode: str, server=None,
                      server_script: str = None):
    """Factory: create MCPDeviceClient with chosen transport.

    Args:
        transport_mode: "inprocess" or "stdio"
        server: FastMCP server instance (for inprocess)
        server_script: Path to MCP server script (for stdio, unused)
    """
    if transport_mode == "stdio":
        from fastmcp.client.transports import PythonStdioTransport
        transport = PythonStdioTransport(script_path=server_script)
        return MCPDeviceClient(transport=transport)
    else:  # "inprocess"
        return MCPDeviceClient(transport=server)


# ---------------------------------------------------------------------------
# Synchronous MCP Device Client (persistent connection)
# ---------------------------------------------------------------------------

class MCPDeviceClient:
    """Synchronous client that mirrors DeviceLayer's interface but
    routes all calls through MCP protocol.

    Uses a PERSISTENT connection so that stdio transport keeps the
    subprocess alive across all calls (instead of spawning a new
    subprocess per call).
    """

    def __init__(self, transport):
        """
        Args:
            transport: A FastMCP server instance (in-process) or
                       a transport object (stdio).
        """
        self._transport = transport
        self._loop = asyncio.new_event_loop()
        self._client = None
        self._client_cm = None

    # ---- async bridge (persistent connection) ----------------------------

    def _run(self, coro):
        """Run an async coroutine on the dedicated event loop.

        If called from within an already-running event loop (e.g. FastAPI),
        delegates to a background thread to avoid 'Cannot run the event loop
        while another loop is running' errors.
        """
        try:
            asyncio.get_running_loop()
            # Inside an async context — run on a separate thread
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(self._loop.run_until_complete, coro).result()
        except RuntimeError:
            # No running loop — safe to run directly
            return self._loop.run_until_complete(coro)

    async def _ensure_connected(self):
        """Open a persistent client session (reused across all calls).

        For inprocess: wraps the in-memory server.
        For stdio: spawns and connects to the subprocess ONCE.
        """
        if self._client is None:
            self._client_cm = Client(self._transport)
            self._client = await self._client_cm.__aenter__()
        return self._client

    async def _call(self, tool_name: str, params: dict = None) -> str:
        """Call an MCP tool using the persistent connection."""
        client = await self._ensure_connected()
        result = await client.call_tool(tool_name, params or {})
        # fastmcp 3.x: result is CallToolResult with .content list
        if result and result.content:
            first = result.content[0]
            return first.text if hasattr(first, "text") else str(first)
        return "{}"

    def _call_sync(self, tool_name: str, params: dict = None) -> str:
        """Synchronously call an MCP tool, return raw text."""
        return self._run(self._call(tool_name, params))

    # ---- health check (NEW POC7) ----------------------------------------

    def ping(self) -> bool:
        """MCP-level ping check."""
        try:
            return self._run(self._ping_async())
        except Exception:
            return False

    async def _ping_async(self) -> bool:
        client = await self._ensure_connected()
        return await client.ping()

    # ---- public API (mirrors DeviceLayer) --------------------------------

    def list_devices(self) -> List[dict]:
        """Discover all devices via MCP tool."""
        raw = self._call_sync("list_devices")
        return json.loads(raw)

    def get_device_status(self, device_id: str) -> dict:
        """Get single device status via MCP tool."""
        raw = self._call_sync("get_device_status",
                              {"device_id": device_id})
        return json.loads(raw)

    def get_all_telemetry(self) -> List[Telemetry]:
        """Collect all telemetry via MCP, return Telemetry objects."""
        raw = self._call_sync("get_all_telemetry")
        items = json.loads(raw)
        return [
            Telemetry(
                device_id=item["device_id"],
                device_type=item["device_type"],
                timestamp=item["timestamp"],
                readings=item["readings"],
            )
            for item in items
        ]

    def execute(self, device_id: str, command: str,
                params: Dict[str, Any] = None) -> dict:
        """Execute a command on a device via MCP tool."""
        raw = self._call_sync("execute_command", {
            "device_id": device_id,
            "command": command,
            "params": json.dumps(params or {}),
        })
        return json.loads(raw)

    def scan_emergencies(self) -> List[dict]:
        """Scan for emergencies via MCP tool."""
        raw = self._call_sync("scan_emergencies")
        return json.loads(raw)

    def apply_fallback_rules(self) -> List[dict]:
        """Apply fallback rules via MCP tool."""
        raw = self._call_sync("apply_fallback_rules")
        return json.loads(raw)

    def inject_fault(self, device_id: str, fault_type: str,
                     params: dict = None) -> dict:
        """Inject a simulated fault via MCP tool."""
        raw = self._call_sync("inject_fault", {
            "device_id": device_id,
            "fault_type": fault_type,
            "params": json.dumps(params or {}),
        })
        return json.loads(raw)

    def register_device(self, device_type: str, device_id: str,
                        room: str, params: dict = None) -> dict:
        """Dynamically register a new device via MCP tool."""
        raw = self._call_sync("register_device", {
            "device_type": device_type,
            "device_id": device_id,
            "room": room,
            "params": json.dumps(params or {}),
        })
        return json.loads(raw)

    def health_check(self) -> dict:
        """Call the MCP health_check tool."""
        raw = self._call_sync("health_check")
        return json.loads(raw)

    def device_count(self) -> int:
        """Get device count by listing devices."""
        return len(self.list_devices())

    def close(self):
        """Clean up the persistent connection and event loop.

        For stdio: terminates the subprocess.
        For inprocess: closes the client session.
        """
        if self._client_cm:
            try:
                self._run(self._client_cm.__aexit__(None, None, None))
            except Exception:
                pass
            self._client = None
            self._client_cm = None
        if self._loop and not self._loop.is_closed():
            self._loop.close()
