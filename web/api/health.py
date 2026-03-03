"""
Health check endpoints.

Provides health, readiness, and system info for the s5-shb-agent backend.
"""

from fastapi import APIRouter

from web.core.state import get_app_state
from engine import __version__, __app_name__

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    """Basic health check."""
    state = get_app_state()
    return {
        "status": "ok",
        "app_name": __app_name__,
        "version": __version__,
        "session_active": state.is_active,
        "session_name": state.session_name if state.is_active else None,
    }


@router.get("/health/detail")
async def health_detail() -> dict:
    """Detailed health check with subsystem status."""
    state = get_app_state()

    subsystems = {
        "blockchain": state.chain is not None,
        "agents": len(state.agents) > 0,
        "mcp": state.mcp is not None,
        "offchain_store": state.store is not None,
        "nlu": state.nlu_agent is not None,
        "anomaly_detection": state.anomaly_agent is not None,
        "arbitration": state.arb_agent is not None,
        "governance": state.gov_contract is not None,
        "health_monitor": state.health_monitor is not None,
        "session_manager": state.session_mgr is not None,
        "s5_hes": state.s5_hes_available,
    }

    return {
        "status": "ok",
        "app_name": __app_name__,
        "version": __version__,
        "session": {
            "active": state.is_active,
            "name": state.session_name if state.is_active else None,
            "is_fresh": state.is_fresh,
        },
        "subsystems": subsystems,
        "subsystems_ready": sum(subsystems.values()),
        "subsystems_total": len(subsystems),
    }


@router.get("/health/s5-hes")
async def health_s5_hes() -> dict:
    """Check S5-HES-Agent connectivity."""
    state = get_app_state()
    if state.s5_hes_client is None:
        return {
            "status": "not_configured",
            "available": False,
            "message": "S5-HES client not initialized",
        }
    available = await state.s5_hes_client.health_check()
    state.s5_hes_available = available
    return {
        "status": "ok" if available else "unavailable",
        "available": available,
        "url": state.s5_hes_client.base_url,
    }


@router.get("/health/mcp")
async def health_mcp() -> dict:
    """Check MCP subsystem health."""
    state = get_app_state()
    if not state.is_active or state.mcp is None:
        return {
            "status": "inactive",
            "message": "No active session or MCP not initialized",
        }
    try:
        devices = state.mcp.list_devices()
        return {
            "status": "ok",
            "devices_registered": len(devices),
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}
