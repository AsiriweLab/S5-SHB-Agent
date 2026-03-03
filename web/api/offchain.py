"""
Off-chain data query endpoints.

Provides paginated, filterable access to all 13 off-chain SQLite tables:
- Telemetry (continuous, events, alerts)
- Reasoning log
- Conflict records
- Merkle anchors
- MCP health log
- Decision outcomes
- NLU conversation log
- Anomaly log
- Arbitration log
- Governance change log
- Model usage log
- Combined statistics
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query

from web.core.state import get_app_state

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_store():
    """Raise 400 if no active session or store not initialized."""
    state = get_app_state()
    if not state.is_active:
        raise HTTPException(
            status_code=400,
            detail="No active session.",
        )
    if not state.store:
        raise HTTPException(
            status_code=400,
            detail="Off-chain store not initialized.",
        )
    return state


# ---------------------------------------------------------------------------
# Telemetry endpoints
# ---------------------------------------------------------------------------

@router.get("/telemetry/continuous")
async def query_continuous_telemetry(
    device_id: Optional[str] = Query(None, description="Filter by device ID"),
    limit: int = Query(100, ge=1, le=5000),
) -> dict[str, Any]:
    """Query continuous numeric telemetry streams."""
    state = _require_store()
    records = state.store.query_continuous(device_id=device_id, limit=limit)
    return {"records": records, "count": len(records)}


@router.get("/telemetry/events")
async def query_telemetry_events(
    event_type: Optional[str] = Query(None, description="Filter by event type"),
    limit: int = Query(100, ge=1, le=5000),
) -> dict[str, Any]:
    """Query discrete state-change events."""
    state = _require_store()
    records = state.store.query_events(event_type=event_type, limit=limit)
    return {"records": records, "count": len(records)}


@router.get("/telemetry/alerts")
async def query_telemetry_alerts(
    severity: Optional[str] = Query(None, description="Filter by severity"),
    limit: int = Query(100, ge=1, le=5000),
) -> dict[str, Any]:
    """Query threshold crossings and anomaly alerts."""
    state = _require_store()
    records = state.store.query_alerts(severity=severity, limit=limit)
    return {"records": records, "count": len(records)}


# ---------------------------------------------------------------------------
# Reasoning & conflicts
# ---------------------------------------------------------------------------

@router.get("/reasoning")
async def query_reasoning(
    reasoning_hash: Optional[str] = Query(
        None, description="Look up a specific reasoning hash"
    ),
    limit: int = Query(50, ge=1, le=500),
) -> dict[str, Any]:
    """Query reasoning log entries.

    If reasoning_hash is provided, returns a single entry with verification.
    Otherwise returns recent reasoning entries.
    """
    state = _require_store()

    if reasoning_hash:
        result = state.store.verify_reasoning(reasoning_hash)
        return {"entry": result, "verified": result.get("verified", False)}

    # Query recent reasoning (direct SQL query on reasoning_log table)
    try:
        rows = state.store.conn.execute(
            "SELECT * FROM reasoning_log ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
        records = [dict(r) for r in rows]
    except Exception:
        records = []

    return {"records": records, "count": len(records)}


@router.get("/conflicts")
async def query_conflicts(
    device_id: Optional[str] = Query(None, description="Filter by device ID"),
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    """Query agent conflict resolution records."""
    state = _require_store()
    records = state.store.query_conflicts(device_id=device_id, limit=limit)

    conflict_stats = {}
    try:
        conflict_stats = state.store.conflict_stats()
    except Exception:
        pass

    return {
        "records": records,
        "count": len(records),
        "stats": conflict_stats,
    }


# ---------------------------------------------------------------------------
# Merkle anchors
# ---------------------------------------------------------------------------

@router.get("/anchors")
async def query_anchors(
    limit: int = Query(50, ge=1, le=500),
) -> dict[str, Any]:
    """Query telemetry Merkle anchors linking off-chain to on-chain."""
    state = _require_store()

    try:
        rows = state.store.conn.execute(
            "SELECT * FROM telemetry_anchors ORDER BY batch_id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        records = [dict(r) for r in rows]
    except Exception:
        records = []

    unanchored = state.store.get_unanchored_count()

    return {
        "records": records,
        "count": len(records),
        "unanchored_telemetry": unanchored,
    }


# ---------------------------------------------------------------------------
# MCP health
# ---------------------------------------------------------------------------

@router.get("/health")
async def query_mcp_health(
    limit: int = Query(50, ge=1, le=500),
) -> dict[str, Any]:
    """Query MCP health check snapshots."""
    state = _require_store()

    try:
        rows = state.store.conn.execute(
            "SELECT * FROM mcp_health_log ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
        records = [dict(r) for r in rows]
    except Exception:
        records = []

    summary = {}
    try:
        summary = state.store.get_health_summary()
    except Exception:
        pass

    return {
        "records": records,
        "count": len(records),
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# Decision outcomes
# ---------------------------------------------------------------------------

@router.get("/outcomes")
async def query_outcomes(
    agent_id: Optional[str] = Query(None, description="Filter by agent ID"),
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    """Query agent decision outcomes (feedback loop data)."""
    state = _require_store()

    if agent_id:
        records = state.store.get_recent_outcomes(agent_id, limit=limit)
    else:
        try:
            rows = state.store.conn.execute(
                "SELECT * FROM agent_decision_outcomes "
                "ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
            records = [dict(r) for r in rows]
        except Exception:
            records = []

    return {"records": records, "count": len(records)}


# ---------------------------------------------------------------------------
# NLU conversations
# ---------------------------------------------------------------------------

@router.get("/conversations")
async def query_conversations(
    limit: int = Query(50, ge=1, le=500),
) -> dict[str, Any]:
    """Query NLU conversation log entries."""
    state = _require_store()

    try:
        rows = state.store.conn.execute(
            "SELECT * FROM conversation_log ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
        records = [dict(r) for r in rows]
    except Exception:
        records = []

    stats = {}
    try:
        stats = state.store.get_conversation_stats()
    except Exception:
        pass

    return {
        "records": records,
        "count": len(records),
        "stats": stats,
    }


# ---------------------------------------------------------------------------
# Anomaly log
# ---------------------------------------------------------------------------

@router.get("/anomalies")
async def query_anomalies(
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    """Query anomaly detection log entries."""
    state = _require_store()

    try:
        rows = state.store.conn.execute(
            "SELECT * FROM anomaly_log ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
        records = [dict(r) for r in rows]
    except Exception:
        records = []

    stats = {}
    try:
        stats = state.store.get_anomaly_stats()
    except Exception:
        pass

    return {
        "records": records,
        "count": len(records),
        "stats": stats,
    }


# ---------------------------------------------------------------------------
# Arbitration log
# ---------------------------------------------------------------------------

@router.get("/arbitrations")
async def query_arbitrations(
    limit: int = Query(50, ge=1, le=500),
) -> dict[str, Any]:
    """Query arbitration decision log entries."""
    state = _require_store()

    try:
        rows = state.store.conn.execute(
            "SELECT * FROM arbitration_log ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
        records = [dict(r) for r in rows]
    except Exception:
        records = []

    stats = {}
    try:
        stats = state.store.get_arbitration_stats()
    except Exception:
        pass

    return {
        "records": records,
        "count": len(records),
        "stats": stats,
    }


# ---------------------------------------------------------------------------
# Governance log
# ---------------------------------------------------------------------------

@router.get("/governance")
async def query_governance_log(
    limit: int = Query(50, ge=1, le=500),
) -> dict[str, Any]:
    """Query governance change log entries."""
    state = _require_store()

    try:
        rows = state.store.conn.execute(
            "SELECT * FROM governance_log ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
        records = [dict(r) for r in rows]
    except Exception:
        records = []

    stats = {}
    try:
        stats = state.store.get_governance_stats()
    except Exception:
        pass

    return {
        "records": records,
        "count": len(records),
        "stats": stats,
    }


# ---------------------------------------------------------------------------
# Model usage log
# ---------------------------------------------------------------------------

@router.get("/model-usage")
async def query_model_usage(
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    """Query model usage / cost tracking log entries."""
    state = _require_store()

    try:
        rows = state.store.conn.execute(
            "SELECT * FROM model_usage_log ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
        records = [dict(r) for r in rows]
    except Exception:
        records = []

    stats = {}
    try:
        stats = state.store.get_model_usage_stats()
    except Exception:
        pass

    return {
        "records": records,
        "count": len(records),
        "stats": stats,
    }


# ---------------------------------------------------------------------------
# Combined statistics
# ---------------------------------------------------------------------------

@router.get("/stats")
async def get_all_stats() -> dict[str, Any]:
    """Get comprehensive statistics for all 13 off-chain tables."""
    state = _require_store()

    try:
        stats = state.store.stats()
    except Exception:
        stats = {}

    return {"stats": stats}
