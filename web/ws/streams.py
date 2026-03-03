"""
WebSocket stream handlers.

Provides 5 WebSocket endpoint handlers:
- /ws/telemetry   -- device telemetry every 2 seconds
- /ws/blockchain  -- block mined / transaction events
- /ws/agents      -- agent decisions, conflicts, arbitrations
- /ws/governance  -- preference changes, preset applications
- /ws/scenarios   -- scenario runner progress (1/39, 2/39, ...)

Each handler:
1. Accepts the connection via ConnectionManager
2. Runs a channel-specific loop (polling or event-driven)
3. Cleanly disconnects on close
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from loguru import logger

from web.core.state import get_app_state
from web.ws.manager import ws_manager


def _utcnow() -> str:
    """ISO timestamp."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# 1. Telemetry stream (polling-based, 2-second interval)
# ---------------------------------------------------------------------------

async def telemetry_stream(websocket: WebSocket) -> None:
    """Stream device telemetry readings every 2 seconds.

    Polls MCP for all device readings and sends them as JSON.
    If no active session, sends an error and closes.
    """
    await ws_manager.connect(websocket, "telemetry")
    try:
        state = get_app_state()
        if not state.is_active or not state.mcp:
            await websocket.send_json({
                "event_type": "error",
                "message": "No active session",
                "timestamp": _utcnow(),
            })
            return

        while True:
            try:
                # Poll telemetry from MCP (sync call -> run in thread)
                telemetry_list = await asyncio.to_thread(
                    state.mcp.get_all_telemetry
                )

                readings = []
                for t in telemetry_list:
                    readings.append({
                        "device_id": t.device_id,
                        "device_type": t.device_type,
                        "timestamp": t.timestamp,
                        "readings": t.readings,
                    })

                await websocket.send_json({
                    "event_type": "telemetry_update",
                    "timestamp": _utcnow(),
                    "device_count": len(readings),
                    "devices": readings,
                })

            except WebSocketDisconnect:
                break
            except Exception as e:
                logger.debug(f"Telemetry stream error: {e}")
                try:
                    await websocket.send_json({
                        "event_type": "error",
                        "message": str(e),
                        "timestamp": _utcnow(),
                    })
                except Exception:
                    break

            await asyncio.sleep(2.0)

    except WebSocketDisconnect:
        pass
    finally:
        ws_manager.disconnect(websocket, "telemetry")


# ---------------------------------------------------------------------------
# 2. Blockchain stream (event-driven via queue)
# ---------------------------------------------------------------------------

async def blockchain_stream(websocket: WebSocket) -> None:
    """Stream blockchain events (blocks mined, transactions validated).

    Sits idle and waits for events pushed via ws_manager.push_event().
    Also sends initial state snapshot on connect.
    """
    await ws_manager.connect(websocket, "blockchain")
    try:
        state = get_app_state()

        # Send initial snapshot
        if state.chain:
            await websocket.send_json({
                "event_type": "blockchain_snapshot",
                "timestamp": _utcnow(),
                "total_blocks": len(state.chain.chain),
                "total_transactions": sum(
                    len(b.transactions) for b in state.chain.chain
                ),
                "chain_valid": state.chain.validate_chain(),
            })

        # Keep connection alive, listen for client messages or disconnect
        while True:
            try:
                # Wait for client ping/pong or close
                data = await asyncio.wait_for(
                    websocket.receive_text(), timeout=30.0
                )
                # Client can send "ping" to keep alive
                if data == "ping":
                    await websocket.send_text("pong")
            except asyncio.TimeoutError:
                # Send heartbeat
                await websocket.send_json({
                    "event_type": "heartbeat",
                    "timestamp": _utcnow(),
                    "channel": "blockchain",
                })
            except WebSocketDisconnect:
                break

    except WebSocketDisconnect:
        pass
    finally:
        ws_manager.disconnect(websocket, "blockchain")


# ---------------------------------------------------------------------------
# 3. Agents stream (event-driven via queue)
# ---------------------------------------------------------------------------

async def agents_stream(websocket: WebSocket) -> None:
    """Stream agent events (decisions, conflicts, arbitrations).

    Sits idle and waits for events pushed via ws_manager.push_event().
    Also sends initial agent roster on connect.
    """
    await ws_manager.connect(websocket, "agents")
    try:
        state = get_app_state()

        # Send agent roster on connect
        if state.agents:
            from engine.config import AGENT_DEFINITIONS
            roster = []
            for agent_id, defn in AGENT_DEFINITIONS.items():
                roster.append({
                    "agent_id": agent_id,
                    "role": defn["role"],
                    "model": defn.get("model", "n/a"),
                })
            await websocket.send_json({
                "event_type": "agent_roster",
                "timestamp": _utcnow(),
                "agents": roster,
            })

        # Keep connection alive
        while True:
            try:
                data = await asyncio.wait_for(
                    websocket.receive_text(), timeout=30.0
                )
                if data == "ping":
                    await websocket.send_text("pong")
            except asyncio.TimeoutError:
                await websocket.send_json({
                    "event_type": "heartbeat",
                    "timestamp": _utcnow(),
                    "channel": "agents",
                })
            except WebSocketDisconnect:
                break

    except WebSocketDisconnect:
        pass
    finally:
        ws_manager.disconnect(websocket, "agents")


# ---------------------------------------------------------------------------
# 4. Governance stream (event-driven via queue)
# ---------------------------------------------------------------------------

async def governance_stream(websocket: WebSocket) -> None:
    """Stream governance events (preference changes, preset applications).

    Sends current preferences on connect, then waits for pushed events.
    """
    await ws_manager.connect(websocket, "governance")
    try:
        state = get_app_state()

        # Send current preference state
        if state.preferences:
            await websocket.send_json({
                "event_type": "governance_snapshot",
                "timestamp": _utcnow(),
                "preferences": state.preferences.to_dict(),
            })

        # Keep connection alive
        while True:
            try:
                data = await asyncio.wait_for(
                    websocket.receive_text(), timeout=30.0
                )
                if data == "ping":
                    await websocket.send_text("pong")
            except asyncio.TimeoutError:
                await websocket.send_json({
                    "event_type": "heartbeat",
                    "timestamp": _utcnow(),
                    "channel": "governance",
                })
            except WebSocketDisconnect:
                break

    except WebSocketDisconnect:
        pass
    finally:
        ws_manager.disconnect(websocket, "governance")


# ---------------------------------------------------------------------------
# 5. Scenarios stream (event-driven via queue)
# ---------------------------------------------------------------------------

async def scenarios_stream(websocket: WebSocket) -> None:
    """Stream scenario execution progress (1/39, 2/39, ...).

    Events are pushed by the scenario runner via ws_manager.push_event().
    """
    await ws_manager.connect(websocket, "scenarios")
    try:
        # Send ready message
        await websocket.send_json({
            "event_type": "scenarios_ready",
            "timestamp": _utcnow(),
            "message": "Connected to scenario progress stream",
        })

        # Keep connection alive
        while True:
            try:
                data = await asyncio.wait_for(
                    websocket.receive_text(), timeout=30.0
                )
                if data == "ping":
                    await websocket.send_text("pong")
            except asyncio.TimeoutError:
                await websocket.send_json({
                    "event_type": "heartbeat",
                    "timestamp": _utcnow(),
                    "channel": "scenarios",
                })
            except WebSocketDisconnect:
                break

    except WebSocketDisconnect:
        pass
    finally:
        ws_manager.disconnect(websocket, "scenarios")


# ---------------------------------------------------------------------------
# Helper: Broadcast event from REST endpoints
# ---------------------------------------------------------------------------

def emit_blockchain_event(event_type: str, **kwargs: Any) -> None:
    """Push a blockchain event for broadcast (call from sync code)."""
    ws_manager.push_event("blockchain", {
        "event_type": event_type,
        "timestamp": _utcnow(),
        **kwargs,
    })


def emit_agent_event(event_type: str, **kwargs: Any) -> None:
    """Push an agent event for broadcast (call from sync code)."""
    ws_manager.push_event("agents", {
        "event_type": event_type,
        "timestamp": _utcnow(),
        **kwargs,
    })


def emit_governance_event(event_type: str, **kwargs: Any) -> None:
    """Push a governance event for broadcast (call from sync code)."""
    ws_manager.push_event("governance", {
        "event_type": event_type,
        "timestamp": _utcnow(),
        **kwargs,
    })


def emit_scenario_event(
    current: int, total: int, name: str, status: str, **kwargs: Any,
) -> None:
    """Push a scenario progress event for broadcast (call from sync code)."""
    ws_manager.push_event("scenarios", {
        "event_type": "scenario_progress",
        "timestamp": _utcnow(),
        "current": current,
        "total": total,
        "scenario_name": name,
        "status": status,
        **kwargs,
    })
