"""
HES Telemetry Sync -- bridges S5-HES behavioral data into engine DeviceLayer.

Called before each telemetry read in the orchestrator loops to ensure
agents perceive S5-HES-Agent behavioral simulation data, not engine
random noise.

Flow:
  S5-HES simulation events --> sync() --> DeviceLayer device states
                                          --> MCP --> agents / bridge
"""

from __future__ import annotations

from typing import Any

from loguru import logger


class HESTelemetrySync:
    """Fetches HES telemetry events and updates engine device states.

    Each call to sync() fetches new DEVICE_DATA_GENERATED events from
    S5-HES-Agent, caches the latest readings per device, and writes them
    into the DeviceLayer so the MCP server returns HES-backed data.
    """

    def __init__(self) -> None:
        self._last_offset: int = 0
        self._latest_readings: dict[str, dict[str, Any]] = {}
        self._sync_count: int = 0
        self._devices_updated: int = 0

    async def sync(
        self,
        s5_client: Any,
        device_layer: Any,
    ) -> dict[str, Any]:
        """Fetch new HES events and update device states.

        Args:
            s5_client: S5HESClient instance (async HTTP client).
            device_layer: engine DeviceLayer to update.

        Returns:
            Dict with sync stats (events_fetched, devices_updated, etc.).
        """
        if s5_client is None:
            logger.warning("HES telemetry sync: no S5-HES client available")
            return {"skipped": True, "reason": "no_client"}

        try:
            events_data = await s5_client.get_events(
                limit=500, offset=self._last_offset
            )
            events: list = (
                events_data
                if isinstance(events_data, list)
                else events_data.get("events", [])
            )
        except Exception as exc:
            logger.warning(f"HES telemetry sync fetch failed: {exc}")
            return {"skipped": True, "reason": "fetch_failed"}

        if not events:
            return {"events_fetched": 0, "devices_updated": 0}

        # Filter for device telemetry events and cache latest per device
        _DEVICE_EVENT_TYPES = {
            "device_data_generated", "DEVICE_DATA_GENERATED",
            "device_state_change", "DEVICE_STATE_CHANGE",
        }
        data_events = 0
        for event in events:
            etype = event.get("event_type", "")
            if etype not in _DEVICE_EVENT_TYPES:
                continue

            source_id = event.get("source_id", "")
            data = event.get("data", {})
            if not source_id or not data:
                continue

            # Keep all data fields including device_type and device_name metadata
            readings = dict(data)
            self._latest_readings[source_id] = readings
            data_events += 1

        self._last_offset += len(events)

        # Apply cached readings to DeviceLayer
        updated = 0
        for device_id, readings in self._latest_readings.items():
            if device_layer.update_device_from_hes(device_id, readings):
                updated += 1

        self._sync_count += 1
        self._devices_updated = updated

        return {
            "events_fetched": len(events),
            "data_events": data_events,
            "devices_updated": updated,
            "total_cached": len(self._latest_readings),
        }

    def get_stats(self) -> dict[str, Any]:
        """Return sync statistics for diagnostics."""
        return {
            "sync_count": self._sync_count,
            "last_offset": self._last_offset,
            "cached_devices": len(self._latest_readings),
            "last_devices_updated": self._devices_updated,
        }
