"""
WebSocket connection manager.

Manages per-channel WebSocket connections with:
- Channel-based connection tracking (telemetry, blockchain, agents, governance, scenarios)
- Broadcast to all connections on a channel
- Safe disconnect handling
- Event queue for broadcasting from sync contexts (agent endpoints, etc.)
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from typing import Any

from fastapi import WebSocket
from loguru import logger


class ConnectionManager:
    """Manages WebSocket connections grouped by channel."""

    CHANNELS = ("telemetry", "blockchain", "agents", "governance", "scenarios")

    def __init__(self) -> None:
        # channel_name -> set of active WebSocket connections
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)
        # Async queue for events pushed from sync code (REST endpoints)
        self._event_queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self, websocket: WebSocket, channel: str) -> None:
        """Accept and register a WebSocket connection on a channel."""
        await websocket.accept()
        self._connections[channel].add(websocket)
        logger.debug(
            f"WS connected: channel={channel}, "
            f"total={len(self._connections[channel])}"
        )

    def disconnect(self, websocket: WebSocket, channel: str) -> None:
        """Remove a WebSocket connection from a channel."""
        self._connections[channel].discard(websocket)
        logger.debug(
            f"WS disconnected: channel={channel}, "
            f"total={len(self._connections[channel])}"
        )

    def connection_count(self, channel: str) -> int:
        """Number of active connections on a channel."""
        return len(self._connections[channel])

    def total_connections(self) -> int:
        """Total active connections across all channels."""
        return sum(len(conns) for conns in self._connections.values())

    # ------------------------------------------------------------------
    # Broadcasting
    # ------------------------------------------------------------------

    async def broadcast(self, channel: str, data: dict[str, Any]) -> None:
        """Send JSON data to all connections on a channel.

        Disconnected clients are automatically removed.
        """
        dead: list[WebSocket] = []
        for ws in self._connections[channel]:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._connections[channel].discard(ws)

    async def broadcast_text(self, channel: str, text: str) -> None:
        """Send raw text to all connections on a channel."""
        dead: list[WebSocket] = []
        for ws in self._connections[channel]:
            try:
                await ws.send_text(text)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._connections[channel].discard(ws)

    # ------------------------------------------------------------------
    # Event queue (for pushing events from sync REST endpoints)
    # ------------------------------------------------------------------

    def push_event(self, channel: str, data: dict[str, Any]) -> None:
        """Push an event onto the async queue (safe to call from sync code).

        The event will be picked up by the event dispatcher task and
        broadcast to the appropriate channel.
        """
        try:
            self._event_queue.put_nowait((channel, data))
        except Exception:
            pass  # Queue full or other issue -- drop silently

    async def dispatch_events(self) -> None:
        """Continuously dispatch queued events to WebSocket channels.

        Run this as a background task in the FastAPI lifespan.
        """
        while True:
            channel, data = await self._event_queue.get()
            await self.broadcast(channel, data)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """Return connection counts per channel."""
        return {
            ch: len(self._connections[ch])
            for ch in self.CHANNELS
        }


# Module-level singleton
ws_manager = ConnectionManager()
