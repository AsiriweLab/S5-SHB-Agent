"""
Main API router for s5-shb-agent.

Aggregates all sub-routers into a single router mounted at /api by web/main.py.
"""

from fastapi import APIRouter

from web.api import health
from web.api import home
from web.api import threats
from web.api import chat
from web.api import simulation
from web.api import sessions
from web.api import devices
from web.api import blockchain
from web.api import agents
from web.api import nlu
from web.api import anomaly
from web.api import governance
from web.api import offchain
from web.api import audit
from web.api import report
from web.api import scenarios

# Main API router -- mounted at /api by web/main.py
app_router = APIRouter()

# --- Sub-routers ---

# Health
app_router.include_router(health.router, tags=["Health"])

# Home Builder
app_router.include_router(home.router, prefix="/home", tags=["Home Builder"])

# Threats
app_router.include_router(threats.router, prefix="/threats", tags=["Threat Builder"])

# Chat (Gemini-backed AI generation for builders)
app_router.include_router(chat.router, prefix="/chat", tags=["Chat"])

# Simulation
app_router.include_router(simulation.router, prefix="/simulation", tags=["Simulation"])

# Sessions
app_router.include_router(sessions.router, prefix="/sessions", tags=["Sessions"])

# Devices + Blockchain + Agents
app_router.include_router(devices.router, prefix="/devices", tags=["Devices"])
app_router.include_router(blockchain.router, prefix="/blockchain", tags=["Blockchain"])
app_router.include_router(agents.router, prefix="/agents", tags=["Agents"])

# NLU + Anomaly + Governance + Offchain + Audit + Report
app_router.include_router(nlu.router, prefix="/nlu", tags=["NLU"])
app_router.include_router(anomaly.router, prefix="/anomaly", tags=["Anomaly"])
app_router.include_router(governance.router, prefix="/governance", tags=["Governance"])
app_router.include_router(offchain.router, prefix="/offchain", tags=["Off-Chain"])
app_router.include_router(audit.router, prefix="/audit", tags=["Audit"])
app_router.include_router(report.router, prefix="/report", tags=["Report"])

# Scenarios
app_router.include_router(scenarios.router, prefix="/scenarios", tags=["Scenarios"])

# NOTE: WebSocket endpoints (/ws/*) are mounted directly on the
# FastAPI app in web/main.py, not on this APIRouter, because
# FastAPI WebSocket routes require direct app-level registration.
