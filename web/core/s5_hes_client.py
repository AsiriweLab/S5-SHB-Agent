"""
HTTP client for S5-HES-Agent (external simulation service on port 8000).

All methods are async. Uses httpx.AsyncClient with configurable timeout.
S5-HES-Agent is optional -- all methods handle connection errors gracefully.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from loguru import logger


class S5HESClient:
    """Async HTTP client for S5-HES-Agent."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.base_url = base_url or os.getenv(
            "S5_HES_AGENT_URL", "http://localhost:8000"
        )
        _timeout = timeout or float(os.getenv("S5_HES_AGENT_TIMEOUT", "30"))
        self._timeout = httpx.Timeout(_timeout)
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Lazily create or return the httpx.AsyncClient."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self._timeout,
                follow_redirects=True,
            )
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    async def health_check(self) -> bool:
        """Check if S5-HES-Agent is reachable. Returns True/False."""
        try:
            client = await self._get_client()
            resp = await client.get("/api/health", timeout=5.0)
            return resp.status_code == 200
        except Exception as e:
            logger.debug(f"S5-HES health check failed: {e}")
            # Close stale client so next call creates a fresh connection
            await self.close()
            return False

    # ------------------------------------------------------------------
    # Templates & Device Types
    # ------------------------------------------------------------------

    async def get_templates(self) -> list[dict]:
        """Fetch available home templates."""
        client = await self._get_client()
        resp = await client.get("/api/simulation/templates")
        resp.raise_for_status()
        return resp.json()

    async def get_device_types(self) -> dict:
        """Fetch categorized device type catalog."""
        client = await self._get_client()
        resp = await client.get("/api/simulation/device-types/categorized")
        resp.raise_for_status()
        return resp.json()

    async def get_threat_catalog(self) -> list[dict]:
        """Fetch available threat definitions from S5-HES-Agent."""
        try:
            client = await self._get_client()
            resp = await client.get("/api/mode/threats")
            resp.raise_for_status()
            data = resp.json()
            # HES returns {"threats": [...]} -- extract the list
            if isinstance(data, dict) and "threats" in data:
                return data["threats"]
            if isinstance(data, list):
                return data
            return []
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Home Management
    # ------------------------------------------------------------------

    async def push_home(self, home_config: dict) -> dict:
        """Push home configuration to S5-HES simulation engine."""
        client = await self._get_client()
        resp = await client.post("/api/simulation/home/custom", json=home_config)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Simulation Control
    # ------------------------------------------------------------------

    async def start_simulation(
        self,
        duration: float = 24.0,
        time_compression: int = 1440,
        threat_config: dict[str, Any] | None = None,
    ) -> dict:
        """Start behavior simulation."""
        client = await self._get_client()
        body: dict[str, Any] = {
            "duration_hours": duration,
            "time_compression": time_compression,
        }
        if threat_config:
            body["enable_threats"] = True
            body["threats"] = threat_config.get("threats", [])
        resp = await client.post("/api/simulation/start", json=body)
        resp.raise_for_status()
        return resp.json()

    async def get_simulation_status(self) -> dict:
        """Get simulation progress."""
        client = await self._get_client()
        resp = await client.get("/api/simulation/status")
        resp.raise_for_status()
        return resp.json()

    async def stop_simulation(self) -> dict:
        """Stop running simulation."""
        client = await self._get_client()
        resp = await client.post("/api/simulation/stop")
        resp.raise_for_status()
        return resp.json()

    async def pause_simulation(self) -> dict:
        """Pause running simulation."""
        client = await self._get_client()
        resp = await client.post("/api/simulation/pause")
        resp.raise_for_status()
        return resp.json()

    async def resume_simulation(self) -> dict:
        """Resume paused simulation."""
        client = await self._get_client()
        resp = await client.post("/api/simulation/resume")
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Events & Telemetry
    # ------------------------------------------------------------------

    async def get_events(self, **filters: Any) -> Any:
        """Fetch simulation events (telemetry, anomalies)."""
        client = await self._get_client()
        resp = await client.get("/api/simulation/events", params=filters)
        resp.raise_for_status()
        return resp.json()

    async def get_device_telemetry(self, device_id: str) -> dict:
        """Fetch telemetry for a specific device."""
        client = await self._get_client()
        resp = await client.get(f"/api/simulation/devices/{device_id}/data")
        resp.raise_for_status()
        return resp.json()
