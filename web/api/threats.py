"""
Threat Builder endpoints.

CRUD operations for configuring threat scenarios to inject during simulation.
The threat builder operates independently -- no active session or S5-HES-Agent
required to configure threats.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from web.core.state import get_app_state
from web.core.threat_store import (
    get_threat_store,
    ThreatConfig,
    THREAT_TYPES,
    THREAT_TYPE_IDS,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class ThreatCreateRequest(BaseModel):
    name: str
    threat_type: str
    target_device: str = ""
    severity: str = "medium"
    parameters: dict[str, Any] = Field(default_factory=dict)


class ThreatUpdateRequest(BaseModel):
    name: Optional[str] = None
    threat_type: Optional[str] = None
    target_device: Optional[str] = None
    severity: Optional[str] = None
    parameters: Optional[dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/types")
async def list_threat_types() -> list[dict]:
    """List available threat types (built-in + S5-HES catalog if available)."""
    types = list(THREAT_TYPES)

    state = get_app_state()
    if state.s5_hes_available and state.s5_hes_client:
        try:
            s5_types = await state.s5_hes_client.get_threat_catalog()
            builtin_ids = {t["id"] for t in types}
            for st in s5_types:
                sid = st.get("id", st.get("type", ""))
                if sid and sid not in builtin_ids:
                    types.append({
                        "id": sid,
                        "name": st.get("name", sid),
                        "category": st.get("category", "other"),
                        "severity_default": st.get("severity_default",
                                                   st.get("severity", "medium")),
                        "description": st.get("description", ""),
                        "source": "s5_hes",
                    })
        except Exception as e:
            logger.debug(f"Failed to fetch S5-HES threat catalog: {e}")

    return types


@router.get("/")
async def list_threats() -> list[dict]:
    """Get configured threats for current home."""
    store = get_threat_store()
    return [
        {
            "id": t.id,
            "name": t.name,
            "threat_type": t.threat_type,
            "target_device": t.target_device,
            "severity": t.severity,
            "parameters": t.parameters,
        }
        for t in store.get_threats()
    ]


@router.post("/", status_code=201)
async def add_threat(body: ThreatCreateRequest) -> dict:
    """Add a threat configuration."""
    valid_severities = {"low", "medium", "high", "critical"}
    if body.severity not in valid_severities:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid severity '{body.severity}'. Valid: {sorted(valid_severities)}",
        )

    if body.threat_type not in THREAT_TYPE_IDS:
        logger.warning(
            f"Threat type '{body.threat_type}' not in built-in catalog "
            f"(may be an S5-HES extended type)"
        )

    config = ThreatConfig(
        id=uuid.uuid4().hex[:8],
        name=body.name,
        threat_type=body.threat_type,
        target_device=body.target_device,
        severity=body.severity,
        parameters=body.parameters,
    )
    store = get_threat_store()
    store.add_threat(config)
    return {"id": config.id, "name": config.name, "status": "added"}


@router.put("/{threat_id}")
async def update_threat(threat_id: str, body: ThreatUpdateRequest) -> dict:
    """Update a threat configuration."""
    if body.severity is not None:
        valid_severities = {"low", "medium", "high", "critical"}
        if body.severity not in valid_severities:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid severity '{body.severity}'.",
            )

    store = get_threat_store()
    updates = body.model_dump(exclude_none=True)
    updated = store.update_threat(threat_id, updates)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Threat '{threat_id}' not found")
    return {"id": threat_id, "status": "updated"}


@router.delete("/{threat_id}")
async def remove_threat(threat_id: str) -> dict:
    """Remove a threat configuration."""
    store = get_threat_store()
    removed = store.remove_threat(threat_id)
    if not removed:
        raise HTTPException(status_code=404, detail=f"Threat '{threat_id}' not found")
    return {"id": threat_id, "status": "removed"}
