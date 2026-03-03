"""
Governance endpoints.

Provides REST access to Society 5.0 governance layer:
- Resident preferences (4-tier system)
- Locked parameters (tier 4, immutable)
- Governance presets (balanced, max_privacy, budget, best_quality)
- Per-agent model assignments
- Model registry
- Cost tracking
- Governance change log
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from loguru import logger
from pydantic import BaseModel, Field

from web.core.state import get_app_state

router = APIRouter()


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class PreferenceUpdateRequest(BaseModel):
    """Request to update a governance preference."""
    value: Any = Field(..., description="New value for the preference")


class ModelAssignmentRequest(BaseModel):
    """Request to change an agent's model assignment."""
    model: str = Field(..., description="Model name (e.g., gemini-2.0-flash)")


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
# Preference endpoints
# ---------------------------------------------------------------------------

@router.get("/preferences")
async def get_all_preferences() -> dict[str, Any]:
    """Get all resident preferences with tier information."""
    state = _require_active_session()

    if not state.preferences:
        raise HTTPException(
            status_code=400, detail="Preferences not initialized"
        )

    from engine.resident_preferences import (
        TIER_MAP, LOCKED_PARAMETERS, VALIDATION_RULES,
    )

    prefs = state.preferences.to_dict()

    # Annotate with tier info + validation metadata for smart UI widgets
    annotated = {}
    for key, value in prefs.items():
        tier = TIER_MAP.get(key, 0)
        rule = VALIDATION_RULES.get(key, {})
        annotated[key] = {
            "value": value,
            "tier": tier,
            "editable": tier < 4,
            "validation": {
                "type": (rule.get("type", type(value)).__name__
                         if rule.get("type") else type(value).__name__),
                "choices": rule.get("choices"),
                "min": rule.get("min"),
                "max": rule.get("max"),
            } if rule else None,
        }

    return {
        "preferences": annotated,
        "locked_count": len(LOCKED_PARAMETERS),
        "total_keys": len(annotated),
    }


@router.put("/preferences/{key}")
async def update_preference(
    key: str, body: PreferenceUpdateRequest,
) -> dict[str, Any]:
    """Change a governance preference (validated by tier rules).

    Tier 1 (SAFE): Always allowed.
    Tier 2 (IMPACTFUL): Allowed with validation.
    Tier 3 (ADVANCED): Allowed with validation.
    Tier 4 (LOCKED): Rejected (immutable).
    """
    state = _require_active_session()

    if not state.gov_contract:
        raise HTTPException(
            status_code=400, detail="Governance contract not initialized"
        )

    result = state.gov_contract.apply_preference_change(key, body.value)

    if not result.get("success", False):
        raise HTTPException(
            status_code=400,
            detail=result.get("reason", "Preference change rejected"),
        )

    # Record on blockchain
    if state.chain:
        try:
            tx = state.gov_contract.create_governance_transaction(result)
            state.chain.add_transaction(tx)
        except Exception as e:
            logger.warning(f"Failed to record governance tx: {e}")

    # Store in off-chain DB
    if state.store:
        try:
            state.store.store_governance_change(
                change_type="preference",
                key_or_agent=key,
                old_value=str(result.get("old_value", "")),
                new_value=str(result.get("new_value", "")),
                tier=result.get("tier", 0),
                details_json="{}",
            )
        except Exception:
            pass

    # Re-apply priority adjustments if applicable
    if key in ("comfort_vs_energy", "security_vs_privacy"):
        from engine.config import AGENT_DEFINITIONS
        state.preferences.apply_to_agent_priorities(AGENT_DEFINITIONS)

    # Side-effect: anomaly_sensitivity → update live anomaly agent thresholds
    if key == "anomaly_sensitivity" and state.anomaly_agent:
        from engine.resident_preferences import ANOMALY_SENSITIVITY_MAP
        thresholds = ANOMALY_SENSITIVITY_MAP.get(body.value, {})
        if thresholds:
            state.anomaly_agent.update_thresholds(
                zscore_threshold=thresholds["zscore_threshold"],
                iforest_threshold=thresholds["iforest_threshold"],
            )
            logger.info(
                f"Anomaly thresholds updated: sensitivity={body.value}, "
                f"zscore={thresholds['zscore_threshold']}, "
                f"iforest={thresholds['iforest_threshold']}"
            )

    # Side-effect: anomaly_train_cycles → update orchestrator threshold
    if key == "anomaly_train_cycles":
        try:
            import web.core.orchestrator as orch
            orch._ANOMALY_TRAIN_AFTER = int(body.value)
            logger.info(f"Anomaly train-after threshold updated to {body.value}")
        except Exception as e:
            logger.warning(f"Failed to update orchestrator train threshold: {e}")

    # Broadcast governance event via WebSocket
    try:
        from web.ws.streams import emit_governance_event
        emit_governance_event(
            "preference_changed",
            parameter=key,
            old_value=result.get("old_value"),
            new_value=result.get("new_value"),
            tier=result.get("tier", 0),
        )
    except Exception:
        pass

    return {
        "key": key,
        "applied": True,
        **result,
    }


@router.get("/locked")
async def get_locked_parameters() -> dict[str, Any]:
    """Get all 9 locked (tier 4) parameters. These are immutable."""
    _require_active_session()

    from engine.resident_preferences import LOCKED_PARAMETERS

    return {
        "locked_parameters": LOCKED_PARAMETERS,
        "count": len(LOCKED_PARAMETERS),
        "description": "Tier 4 parameters are immutable safety invariants.",
    }


# ---------------------------------------------------------------------------
# Preset endpoints
# ---------------------------------------------------------------------------

@router.get("/presets")
async def get_available_presets() -> dict[str, Any]:
    """List available governance presets."""
    _require_active_session()

    from engine.model_router import GOVERNANCE_PRESETS

    presets = {}
    for name, preset in GOVERNANCE_PRESETS.items():
        presets[name] = {
            "default_model": preset.get("default_model", ""),
            "safety_model": preset.get("safety_model", ""),
            "description": preset.get("description", ""),
        }

    return {"presets": presets}


@router.post("/presets/{name}/apply")
async def apply_preset(name: str) -> dict[str, Any]:
    """Apply a governance preset to all agents."""
    state = _require_active_session()

    if not state.gov_contract:
        raise HTTPException(
            status_code=400, detail="Governance contract not initialized"
        )

    from engine.config import AGENT_DEFINITIONS

    result = state.gov_contract.apply_preset(name, AGENT_DEFINITIONS)

    if not result.get("success", False):
        raise HTTPException(
            status_code=400,
            detail=result.get("reason", f"Preset '{name}' not found"),
        )

    # Record on blockchain
    if state.chain:
        try:
            tx = state.gov_contract.create_governance_transaction({
                "change_type": "preset",
                "preset_name": name,
                **result,
            })
            state.chain.add_transaction(tx)
        except Exception as e:
            logger.warning(f"Failed to record preset governance tx: {e}")

    # Store in off-chain DB
    if state.store:
        try:
            state.store.store_governance_change(
                change_type="preset",
                key_or_agent=name,
                old_value="",
                new_value=name,
                tier=0,
                details_json="{}",
            )
        except Exception:
            pass

    # Broadcast governance event via WebSocket
    try:
        from web.ws.streams import emit_governance_event
        emit_governance_event(
            "preset_applied",
            preset_name=name,
        )
    except Exception:
        pass

    return {
        "preset": name,
        "applied": True,
        **result,
    }


# ---------------------------------------------------------------------------
# Model assignment endpoints
# ---------------------------------------------------------------------------

@router.get("/models")
async def get_model_assignments() -> dict[str, Any]:
    """Get per-agent model assignments."""
    state = _require_active_session()

    if not state.model_router:
        raise HTTPException(
            status_code=400, detail="Model router not initialized"
        )

    assignments = state.model_router.get_all_assignments()

    return {
        "assignments": assignments,
        "total_agents": len(assignments),
    }


@router.put("/models/{agent_id}")
async def update_model_assignment(
    agent_id: str, body: ModelAssignmentRequest,
) -> dict[str, Any]:
    """Change the model assigned to a specific agent.

    Validated by governance contract tier constraints.
    """
    state = _require_active_session()

    if not state.gov_contract:
        raise HTTPException(
            status_code=400, detail="Governance contract not initialized"
        )

    result = state.gov_contract.apply_model_change(agent_id, body.model)

    if not result.get("success", False):
        raise HTTPException(
            status_code=400,
            detail=result.get("reason", "Model change rejected"),
        )

    # Record on blockchain
    if state.chain:
        try:
            tx = state.gov_contract.create_governance_transaction(result)
            state.chain.add_transaction(tx)
        except Exception as e:
            logger.warning(f"Failed to record model change tx: {e}")

    # Store in off-chain DB
    if state.store:
        try:
            state.store.store_governance_change(
                change_type="model_assignment",
                key_or_agent=agent_id,
                old_value=str(result.get("old_model", "")),
                new_value=body.model,
                tier=0,
                details_json="{}",
            )
        except Exception:
            pass

    return {
        "agent_id": agent_id,
        "model": body.model,
        "applied": True,
        **result,
    }


@router.get("/registry")
async def get_model_registry() -> dict[str, Any]:
    """Get the full model registry (all available models and their metadata)."""
    _require_active_session()

    from engine.model_router import MODEL_REGISTRY

    registry = {}
    for name, info in MODEL_REGISTRY.items():
        registry[name] = {
            "tier": info.get("tier", ""),
            "cost_per_1k": info.get("cost_per_1k", 0),
            "provider": info.get("provider", ""),
            "privacy": info.get("privacy", ""),
        }

    return {
        "models": registry,
        "total": len(registry),
    }


# ---------------------------------------------------------------------------
# Cost & log endpoints
# ---------------------------------------------------------------------------

@router.get("/cost")
async def get_cost_tracking() -> dict[str, Any]:
    """Get model usage cost tracking summary."""
    state = _require_active_session()

    # From off-chain store
    offchain_stats = {}
    if state.store:
        try:
            offchain_stats = state.store.get_model_usage_stats()
        except Exception:
            pass

    # From model router cost tracker
    router_summary = {}
    if state.model_router and hasattr(state.model_router, "cost_tracker"):
        try:
            router_summary = state.model_router.cost_tracker.summary()
        except Exception:
            pass

    return {
        "offchain_stats": offchain_stats,
        "router_summary": router_summary,
    }


@router.get("/log")
async def get_governance_log(
    limit: int = Query(50, ge=1, le=500),
) -> dict[str, Any]:
    """Get governance change log."""
    state = _require_active_session()

    # In-memory log from governance contract
    contract_log = []
    if state.gov_contract:
        contract_log = state.gov_contract.change_log[:limit]

    # Off-chain stats
    offchain_stats = {}
    if state.store:
        try:
            offchain_stats = state.store.get_governance_stats()
        except Exception:
            pass

    return {
        "changes": contract_log,
        "total_changes": len(contract_log),
        "preference_changes": (
            state.gov_contract.preference_changes
            if state.gov_contract else 0
        ),
        "model_changes": (
            state.gov_contract.model_changes
            if state.gov_contract else 0
        ),
        "offchain_stats": offchain_stats,
    }
