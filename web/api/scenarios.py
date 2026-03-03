"""
Scenario runner endpoints.

Provides REST access to the 39-scenario demonstration suite:
- List scenarios with names and categories
- Run single scenario by ID
- Run all 39 scenarios (with WebSocket progress broadcasting)
- Get scenario detail and last run result
- List scenario categories

The 39 scenarios are imported from engine/scenarios.py and executed
against the active session's blockchain, agents, MCP, etc.
"""

from __future__ import annotations

import io
import sys
import time
from contextlib import redirect_stdout
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from loguru import logger

from web.core.state import get_app_state

router = APIRouter()


# ---------------------------------------------------------------------------
# Scenario catalog (metadata only, no imports needed)
# ---------------------------------------------------------------------------

SCENARIO_CATALOG: list[dict[str, Any]] = [
    # POC5: Core Blockchain (1-15)
    {"id": 1,  "name": "Normal Multi-Agent Operation",       "category": "POC5", "group": "Core Blockchain"},
    {"id": 2,  "name": "Gas Emergency (Safety Override)",     "category": "POC5", "group": "Core Blockchain"},
    {"id": 3,  "name": "Conflict: Security vs Privacy",      "category": "POC5", "group": "Core Blockchain"},
    {"id": 4,  "name": "Conflict: Security vs Safety",       "category": "POC5", "group": "Core Blockchain"},
    {"id": 5,  "name": "Unauthorized Agent",                 "category": "POC5", "group": "Core Blockchain"},
    {"id": 6,  "name": "Graceful Degradation",               "category": "POC5", "group": "Core Blockchain"},
    {"id": 7,  "name": "Cascading Agent Response",           "category": "POC5", "group": "Core Blockchain"},
    {"id": 8,  "name": "Merkle Anchoring",                   "category": "POC5", "group": "Core Blockchain"},
    {"id": 9,  "name": "ML/DL Readiness",                    "category": "POC5", "group": "Core Blockchain"},
    {"id": 10, "name": "Full Audit Summary",                 "category": "POC5", "group": "Core Blockchain"},
    {"id": 11, "name": "Energy Optimization",                "category": "POC5", "group": "Core Blockchain"},
    {"id": 12, "name": "Climate Comfort vs Energy",          "category": "POC5", "group": "Core Blockchain"},
    {"id": 13, "name": "Maintenance Alert",                  "category": "POC5", "group": "Core Blockchain"},
    {"id": 14, "name": "Cross-Tier Conflict Cascade",        "category": "POC5", "group": "Core Blockchain"},
    {"id": 15, "name": "Full 7-Agent Audit",                 "category": "POC5", "group": "Core Blockchain"},
    # POC6: MCP Protocol (16-18)
    {"id": 16, "name": "MCP Device Discovery",               "category": "POC6", "group": "MCP Protocol"},
    {"id": 17, "name": "Dynamic Device Registration",        "category": "POC6", "group": "MCP Protocol"},
    {"id": 18, "name": "MCP Protocol Audit Trail",           "category": "POC6", "group": "MCP Protocol"},
    # POC7: Async + Health (19-23)
    {"id": 19, "name": "Async Parallel Agent Reasoning",     "category": "POC7", "group": "Async + Health"},
    {"id": 20, "name": "Agent Feedback Loop",                "category": "POC7", "group": "Async + Health"},
    {"id": 21, "name": "MCP Health Monitoring",              "category": "POC7", "group": "Async + Health"},
    {"id": 22, "name": "Stdio Transport Verification",       "category": "POC7", "group": "Async + Health"},
    {"id": 23, "name": "Multi-Model Audit",                  "category": "POC7", "group": "Async + Health"},
    # POC8: NLU + Anomaly + Arbitration (24-33)
    {"id": 24, "name": "NLU Text Command Processing",        "category": "POC8", "group": "NLU + Anomaly + Arbitration"},
    {"id": 25, "name": "NLU Multi-Turn Context",             "category": "POC8", "group": "NLU + Anomaly + Arbitration"},
    {"id": 26, "name": "NLU Safety Override",                "category": "POC8", "group": "NLU + Anomaly + Arbitration"},
    {"id": 27, "name": "Anomaly Detection: Model Training",  "category": "POC8", "group": "NLU + Anomaly + Arbitration"},
    {"id": 28, "name": "Anomaly Detection: Fault Response",  "category": "POC8", "group": "NLU + Anomaly + Arbitration"},
    {"id": 29, "name": "Arbitration: Conflict Resolution",   "category": "POC8", "group": "NLU + Anomaly + Arbitration"},
    {"id": 30, "name": "Arbitration: Safety Override",       "category": "POC8", "group": "NLU + Anomaly + Arbitration"},
    {"id": 31, "name": "Full 10-Agent Integration",          "category": "POC8", "group": "NLU + Anomaly + Arbitration"},
    {"id": 32, "name": "POC9 Audit Summary",                 "category": "POC8", "group": "NLU + Anomaly + Arbitration"},
    {"id": 33, "name": "Adaptive PoW Difficulty Demo",       "category": "POC8", "group": "NLU + Anomaly + Arbitration"},
    # POC10: Society 5.0 Governance (34-39)
    {"id": 34, "name": "Resident Preference Change",         "category": "POC10", "group": "Society 5.0 Governance"},
    {"id": 35, "name": "Voice-Controlled Governance",        "category": "POC10", "group": "Society 5.0 Governance"},
    {"id": 36, "name": "Model Router + Governance Preset",   "category": "POC10", "group": "Society 5.0 Governance"},
    {"id": 37, "name": "LOCKED Parameter Immutability",      "category": "POC10", "group": "Society 5.0 Governance"},
    {"id": 38, "name": "Confirmation Mode Demo",             "category": "POC10", "group": "Society 5.0 Governance"},
    {"id": 39, "name": "Session Persistence + POC10 Audit",  "category": "POC10", "group": "Society 5.0 Governance"},
]

CATEGORIES = {
    "POC5":  {"name": "Core Blockchain",               "ids": list(range(1, 16))},
    "POC6":  {"name": "MCP Protocol",                  "ids": list(range(16, 19))},
    "POC7":  {"name": "Async + Health",                 "ids": list(range(19, 24))},
    "POC8":  {"name": "NLU + Anomaly + Arbitration",    "ids": list(range(24, 34))},
    "POC10": {"name": "Society 5.0 Governance",         "ids": list(range(34, 40))},
}

# Module-level storage for last run results
_last_results: dict[int, dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_active_session():
    """Raise 400 if no active session."""
    state = get_app_state()
    if not state.is_active:
        raise HTTPException(
            status_code=400,
            detail="No active session. Create or resume a session first.",
        )
    return state


def _get_scenario_meta(scenario_id: int) -> dict[str, Any]:
    """Get scenario metadata by ID."""
    for s in SCENARIO_CATALOG:
        if s["id"] == scenario_id:
            return s
    return {}


def _build_scenario_args(scenario_id: int, state) -> dict[str, Any]:
    """Build the kwargs dict for a scenario function based on its ID.

    Different scenarios require different subsets of session objects.
    We provide a superset and let Python dispatch to the right ones.
    """
    # Common args available to all scenarios
    args: dict[str, Any] = {}

    if state.mcp:
        args["mcp"] = state.mcp
    if state.chain:
        args["chain"] = state.chain
    if state.agents:
        args["agents"] = state.agents
    if state.agent_keys:
        args["agent_keys"] = state.agent_keys
    if state.store:
        args["store"] = state.store
    if state.health_monitor:
        args["health_monitor"] = state.health_monitor
    if state.nlu_agent:
        args["nlu_agent"] = state.nlu_agent
    if state.anomaly_agent:
        args["anomaly_agent"] = state.anomaly_agent
    if state.arb_agent:
        args["arb_agent"] = state.arb_agent
    if state.convo:
        args["convo"] = state.convo
    if state.preferences:
        args["preferences"] = state.preferences
    if state.model_router:
        args["router"] = state.model_router
    if state.gov_contract:
        args["gov_contract"] = state.gov_contract
    if state.session_mgr:
        args["session_mgr"] = state.session_mgr
        args["session_name"] = state.session_name

    return args


def _run_scenario(scenario_id: int, state) -> dict[str, Any]:
    """Execute a single scenario by ID, capturing stdout as output log.

    Returns structured result dict.
    """
    import importlib
    import inspect

    meta = _get_scenario_meta(scenario_id)
    if not meta:
        raise ValueError(f"Unknown scenario ID: {scenario_id}")

    # Import the scenario function from engine/scenarios.py
    try:
        import engine.scenarios as engine_scenarios
    except ImportError as e:
        raise RuntimeError(f"Cannot import engine scenarios module: {e}")

    func_name = f"scenario_{scenario_id}"
    func = getattr(engine_scenarios, func_name, None)
    if func is None:
        raise RuntimeError(f"Function '{func_name}' not found in scenarios.py")

    # Build args -- inspect the function signature and provide matching kwargs
    all_args = _build_scenario_args(scenario_id, state)
    sig = inspect.signature(func)
    kwargs = {}
    for param_name in sig.parameters:
        if param_name in all_args:
            kwargs[param_name] = all_args[param_name]

    # Execute with stdout capture
    stdout_capture = io.StringIO()
    t0 = time.perf_counter()
    error = None
    try:
        with redirect_stdout(stdout_capture):
            func(**kwargs)
    except Exception as e:
        error = str(e)
        logger.warning(f"Scenario {scenario_id} error: {e}")
    elapsed = time.perf_counter() - t0

    output = stdout_capture.getvalue()

    result = {
        "scenario_id": scenario_id,
        "name": meta["name"],
        "category": meta["category"],
        "status": "error" if error else "completed",
        "elapsed_seconds": round(elapsed, 3),
        "output_lines": output.strip().split("\n") if output.strip() else [],
        "error": error,
    }

    _last_results[scenario_id] = result
    return result


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/")
async def list_scenarios() -> dict[str, Any]:
    """List all 39 scenarios with names, categories, and last run status."""
    scenarios = []
    for s in SCENARIO_CATALOG:
        entry = {**s}
        last = _last_results.get(s["id"])
        entry["last_run"] = last["status"] if last else None
        entry["last_elapsed"] = last["elapsed_seconds"] if last else None
        scenarios.append(entry)

    return {
        "total": len(scenarios),
        "scenarios": scenarios,
    }


@router.post("/{scenario_id}/run")
def run_single_scenario(scenario_id: int) -> dict[str, Any]:
    """Execute a single scenario by ID (1-39).

    The scenario runs against the active session and produces blockchain
    transactions, agent decisions, etc.
    """
    state = _require_active_session()

    if scenario_id < 1 or scenario_id > 39:
        raise HTTPException(
            status_code=400,
            detail=f"Scenario ID must be 1-39, got {scenario_id}",
        )

    if not state.chain or not state.store:
        raise HTTPException(
            status_code=400,
            detail="Blockchain or off-chain store not initialized.",
        )

    # Broadcast start event via WebSocket
    try:
        from web.ws.streams import emit_scenario_event
        meta = _get_scenario_meta(scenario_id)
        emit_scenario_event(
            current=scenario_id, total=39,
            name=meta.get("name", ""), status="running",
        )
    except Exception:
        pass

    try:
        result = _run_scenario(scenario_id, state)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Scenario {scenario_id} execution error: {e}",
        )

    # Broadcast completion event
    try:
        from web.ws.streams import emit_scenario_event
        emit_scenario_event(
            current=scenario_id, total=39,
            name=meta.get("name", ""),
            status=result["status"],
            elapsed_seconds=result["elapsed_seconds"],
        )
    except Exception:
        pass

    return result


@router.post("/run-all")
def run_all_scenarios(
    start: int = Query(1, ge=1, le=39, description="Start from scenario ID"),
    end: int = Query(39, ge=1, le=39, description="End at scenario ID"),
) -> dict[str, Any]:
    """Execute scenarios in range [start, end] sequentially.

    Progress is broadcast on the /ws/scenarios WebSocket channel.
    """
    state = _require_active_session()

    if not state.chain or not state.store:
        raise HTTPException(
            status_code=400,
            detail="Blockchain or off-chain store not initialized.",
        )

    if start > end:
        raise HTTPException(
            status_code=400,
            detail=f"start ({start}) must be <= end ({end})",
        )

    total = end - start + 1
    results = []
    completed = 0
    errors = 0
    t0 = time.perf_counter()

    for sid in range(start, end + 1):
        # Broadcast progress
        try:
            from web.ws.streams import emit_scenario_event
            meta = _get_scenario_meta(sid)
            emit_scenario_event(
                current=sid, total=total,
                name=meta.get("name", ""), status="running",
            )
        except Exception:
            pass

        try:
            result = _run_scenario(sid, state)
            results.append(result)
            if result["status"] == "completed":
                completed += 1
            else:
                errors += 1
        except Exception as e:
            results.append({
                "scenario_id": sid,
                "status": "error",
                "error": str(e),
            })
            errors += 1

    elapsed_total = time.perf_counter() - t0

    # Broadcast completion
    try:
        from web.ws.streams import emit_scenario_event
        emit_scenario_event(
            current=end, total=total,
            name="All scenarios", status="finished",
            completed=completed, errors=errors,
        )
    except Exception:
        pass

    return {
        "total_run": len(results),
        "completed": completed,
        "errors": errors,
        "elapsed_seconds": round(elapsed_total, 3),
        "results": results,
    }


@router.get("/categories")
async def get_categories() -> dict[str, Any]:
    """List scenario categories (POC5, POC6, POC7, POC8, POC10)."""
    return {
        "categories": {
            cat_id: {
                "name": cat["name"],
                "count": len(cat["ids"]),
                "ids": cat["ids"],
            }
            for cat_id, cat in CATEGORIES.items()
        }
    }


@router.get("/{scenario_id}")
async def get_scenario_detail(scenario_id: int) -> dict[str, Any]:
    """Get scenario description, category, and last run result."""
    if scenario_id < 1 or scenario_id > 39:
        raise HTTPException(
            status_code=400,
            detail=f"Scenario ID must be 1-39, got {scenario_id}",
        )

    meta = _get_scenario_meta(scenario_id)
    if not meta:
        raise HTTPException(
            status_code=404,
            detail=f"Scenario {scenario_id} not found",
        )

    last = _last_results.get(scenario_id)

    return {
        **meta,
        "last_run": last if last else None,
    }
