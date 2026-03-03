"""
Standalone FastAPI application for s5-shb-agent.

Runs on port 8001 independently of S5-HES-Agent.
Start with: python -m web.main
"""

from __future__ import annotations

import asyncio
import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

# ---------------------------------------------------------------------------
# Path setup: add engine/ to sys.path so engine files can use bare imports
# (e.g. `from blockchain import ...`) to import each other, while the web
# layer uses qualified imports (e.g. `from engine.blockchain import ...`).
# ---------------------------------------------------------------------------
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_engine_dir = os.path.join(_project_root, "engine")

if _project_root not in sys.path:
    sys.path.insert(0, _project_root)
if _engine_dir not in sys.path:
    sys.path.insert(0, _engine_dir)

# Now safe to import web/engine modules
from web.router import app_router  # noqa: E402
from web.ws.manager import ws_manager  # noqa: E402
from web.ws.streams import (  # noqa: E402
    telemetry_stream,
    blockchain_stream,
    agents_stream,
    governance_stream,
    scenarios_stream,
)


# ---------------------------------------------------------------------------
# Lifespan (startup / shutdown)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler.

    Startup:
    - Launch WebSocket event dispatcher background task.

    Shutdown:
    - Auto-save active session if one exists.
    - Cancel background tasks.
    """
    logger.info("s5-shb-agent starting up on port 8001")

    # Start WS event dispatcher
    dispatcher_task = asyncio.create_task(ws_manager.dispatch_events())

    # Initialize S5-HES-Agent client
    from web.core.s5_hes_client import S5HESClient
    from web.core.state import get_app_state as _get_state

    state = _get_state()
    state.s5_hes_client = S5HESClient()
    try:
        s5_available = await state.s5_hes_client.health_check()
        state.s5_hes_available = s5_available
        if s5_available:
            logger.info(f"S5-HES-Agent connected at {state.s5_hes_client.base_url}")
        else:
            logger.warning("S5-HES-Agent not available (simulation features disabled)")
    except Exception as e:
        logger.warning(f"S5-HES-Agent probe failed: {e}")
        state.s5_hes_available = False

    # Start periodic S5-HES health check (every 30s)
    async def _s5_hes_health_loop():
        """Periodically check S5-HES availability so the flag stays current.

        Always reads the CURRENT AppState (not a captured reference) because
        reset_app_state() replaces the singleton during session setup/resume.
        """
        while True:
            await asyncio.sleep(30)
            try:
                current = _get_state()
                if current.s5_hes_client:
                    available = await current.s5_hes_client.health_check()
                    if available != current.s5_hes_available:
                        current.s5_hes_available = available
                        if available:
                            logger.info("S5-HES-Agent reconnected")
                        else:
                            logger.warning("S5-HES-Agent became unavailable")
                    else:
                        current.s5_hes_available = available
            except Exception:
                current = _get_state()
                current.s5_hes_available = False

    health_task = asyncio.create_task(_s5_hes_health_loop())

    yield

    # Cancel health check
    health_task.cancel()
    try:
        await health_task
    except asyncio.CancelledError:
        pass

    # Shutdown: auto-save active session
    try:
        from web.core.state import get_app_state
        from web.core.bridge import save_current_session

        state = get_app_state()
        if state.is_active:
            logger.info(f"Auto-saving session '{state.session_name}' on shutdown")
            save_current_session()
    except Exception as e:
        logger.warning(f"Auto-save on shutdown failed: {e}")

    # Close S5-HES client
    try:
        state = _get_state()
        if state.s5_hes_client:
            await state.s5_hes_client.close()
    except Exception:
        pass

    # Cancel dispatcher
    dispatcher_task.cancel()
    try:
        await dispatcher_task
    except asyncio.CancelledError:
        pass

    logger.info("s5-shb-agent shut down")


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="S5-ABC-HS Agent",
    description="Society 5.0 Agentic Blockchain Smart Home Agent",
    version="0.1.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    lifespan=lifespan,
)

# CORS -- allow frontend dev server + production
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# REST API router (mounted at /api)
# ---------------------------------------------------------------------------

app.include_router(app_router, prefix="/api")


# ---------------------------------------------------------------------------
# WebSocket endpoints (mounted directly on app)
# ---------------------------------------------------------------------------

@app.websocket("/ws/telemetry")
async def ws_telemetry(websocket: WebSocket):
    await telemetry_stream(websocket)


@app.websocket("/ws/blockchain")
async def ws_blockchain(websocket: WebSocket):
    await blockchain_stream(websocket)


@app.websocket("/ws/agents")
async def ws_agents(websocket: WebSocket):
    await agents_stream(websocket)


@app.websocket("/ws/governance")
async def ws_governance(websocket: WebSocket):
    await governance_stream(websocket)


@app.websocket("/ws/scenarios")
async def ws_scenarios(websocket: WebSocket):
    await scenarios_stream(websocket)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "web.main:app",
        host="0.0.0.0",
        port=8001,
        reload=False,
        log_level="info",
    )
