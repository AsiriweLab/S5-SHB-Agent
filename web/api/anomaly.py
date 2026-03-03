"""
Anomaly detection endpoints.

Provides REST access to the ML/DL anomaly detection agent:
- Accumulate telemetry data for training
- Train ML models (statistical, IForest, LOF, autoencoder)
- Run anomaly detection on current telemetry
- Training summary and statistics
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from loguru import logger

from web.core.state import get_app_state

router = APIRouter()


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


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/accumulate")
def accumulate_telemetry() -> dict[str, Any]:
    """Accumulate current device telemetry for ML training.

    Reads all telemetry via MCP and feeds it into the anomaly agent's
    training buffer. Call this multiple times before /train.
    """
    state = _require_active_session()

    if not state.anomaly_agent:
        raise HTTPException(
            status_code=400, detail="Anomaly detection agent not initialized"
        )
    if not state.mcp:
        raise HTTPException(
            status_code=400, detail="MCP not initialized"
        )

    try:
        telemetry = state.mcp.get_all_telemetry()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"MCP telemetry error: {e}")

    state.anomaly_agent.accumulate_telemetry(telemetry)

    return {
        "accumulated": len(telemetry),
        "message": f"Accumulated {len(telemetry)} telemetry readings for training.",
    }


@router.post("/train")
def train_models() -> dict[str, Any]:
    """Train all anomaly detection models on accumulated telemetry.

    Must call /accumulate at least once before training.
    Returns training results including per-model statistics.
    """
    state = _require_active_session()

    if not state.anomaly_agent:
        raise HTTPException(
            status_code=400, detail="Anomaly detection agent not initialized"
        )

    try:
        result = state.anomaly_agent.train()
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Training error: {e}"
        )

    return {
        "trained": state.anomaly_agent.trained,
        "result": result,
    }


@router.post("/detect")
def run_detection() -> dict[str, Any]:
    """Run anomaly detection on current device telemetry.

    Models must be trained first (call /train).
    Detected anomalies generate corrective agent decisions.
    """
    state = _require_active_session()

    if not state.anomaly_agent:
        raise HTTPException(
            status_code=400, detail="Anomaly detection agent not initialized"
        )
    if not state.mcp:
        raise HTTPException(
            status_code=400, detail="MCP not initialized"
        )

    if not state.anomaly_agent.trained:
        raise HTTPException(
            status_code=400,
            detail="Models not trained. Call POST /accumulate then POST /train first.",
        )

    try:
        telemetry = state.mcp.get_all_telemetry()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"MCP telemetry error: {e}")

    try:
        anomalies, decisions = state.anomaly_agent.detect_and_decide(telemetry)
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Detection error: {e}"
        )

    # Store anomalies in off-chain DB
    if state.store:
        for a in anomalies:
            try:
                state.store.store_anomaly(
                    device_id=a.device_id,
                    device_type=a.device_type,
                    anomaly_score=a.anomaly_score,
                    is_anomaly=a.is_anomaly,
                    detectors_triggered=a.detectors_triggered,
                    explanation="",
                    readings_json="{}",
                )
            except Exception:
                pass

    # Submit decisions to blockchain if any
    decision_results = []
    if decisions and state.chain:
        for dec in decisions:
            tx = dec.transaction
            validation = state.chain.validate_and_add(tx)
            decision_results.append({
                "agent_id": tx.agent_id,
                "action": tx.action,
                "target_device": tx.target_device,
                "confidence": tx.confidence,
                "accepted": validation["accepted"],
            })

    return {
        "devices_scanned": len(telemetry),
        "anomalies_detected": sum(1 for a in anomalies if a.is_anomaly),
        "anomalies": [
            {
                "device_id": a.device_id,
                "device_type": a.device_type,
                "is_anomaly": a.is_anomaly,
                "anomaly_score": a.anomaly_score,
                "detectors_triggered": a.detectors_triggered,
            }
            for a in anomalies
        ],
        "corrective_decisions": decision_results,
    }


@router.get("/models")
def get_model_summary() -> dict[str, Any]:
    """Get training summary for all anomaly detection models."""
    state = _require_active_session()

    if not state.anomaly_agent:
        raise HTTPException(
            status_code=400, detail="Anomaly detection agent not initialized"
        )

    summary = state.anomaly_agent.training_summary()

    return {
        "trained": state.anomaly_agent.trained,
        "summary": summary,
    }


@router.get("/stats")
def get_anomaly_stats() -> dict[str, Any]:
    """Get anomaly detection statistics from off-chain store."""
    state = _require_active_session()

    if not state.store:
        return {"stats": {}}

    try:
        stats = state.store.get_anomaly_stats()
    except Exception:
        stats = {}

    return {"stats": stats}
