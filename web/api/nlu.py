"""
NLU (Natural Language Understanding) endpoints.

Provides text-based command processing through the NLU pipeline:
- Process text commands (parse intent, execute decisions)
- Conversation history
- NLU interaction statistics
"""

from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from web.core.state import get_app_state

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class NLUCommandRequest(BaseModel):
    """Request to process a natural language command."""
    text: str = Field(..., min_length=1, max_length=500,
                      description="Natural language command text")


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


def _submit_nlu_decisions(decisions, state) -> list[dict[str, Any]]:
    """Submit NLU-generated decisions to blockchain and execute via MCP."""
    from engine.config import CONFIDENCE_THRESHOLD

    results = []
    for dec in decisions:
        tx = dec.transaction

        # Store reasoning off-chain
        state.store.store_reasoning(
            dec.reasoning_hash, dec.reasoning_text,
            tx.agent_id, tx.action, tx.target_device, tx.confidence,
        )

        # Submit to blockchain
        validation = state.chain.validate_and_add(tx)
        accepted = validation["accepted"]
        conflict = validation.get("conflict")

        if conflict:
            state.store.store_conflict(conflict)

        # Execute accepted commands via MCP
        executed = False
        exec_result = None
        if accepted and tx.confidence >= CONFIDENCE_THRESHOLD:
            try:
                exec_result = state.mcp.execute(
                    tx.target_device, tx.action, tx.params
                )
                executed = True
            except Exception as e:
                logger.warning(f"NLU MCP execution failed: {e}")

        # Record decision outcome
        state.store.store_decision_outcome(
            agent_id=tx.agent_id,
            action=tx.action,
            target_device=tx.target_device,
            confidence=tx.confidence,
            accepted=accepted,
            conflict=bool(conflict),
            conflict_winner=conflict.get("winner_id", "") if conflict else "",
            reasoning_summary=dec.reasoning_text[:100],
        )

        results.append({
            "agent_id": tx.agent_id,
            "action": tx.action,
            "target_device": tx.target_device,
            "confidence": tx.confidence,
            "accepted": accepted,
            "executed": executed,
            "exec_result": exec_result,
        })

    return results


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/command")
def process_command(body: NLUCommandRequest) -> dict[str, Any]:
    """Process a natural language command through the NLU pipeline.

    Flow:
    1. Get current telemetry from MCP
    2. Get conversation context
    3. Parse intent via NLU agent (LLM)
    4. Submit decisions to blockchain
    5. Execute accepted commands via MCP
    6. Store conversation turn
    """
    state = _require_active_session()

    if not state.nlu_agent:
        raise HTTPException(
            status_code=400, detail="NLU agent not initialized"
        )
    if not state.mcp or not state.chain:
        raise HTTPException(
            status_code=400, detail="MCP or blockchain not initialized"
        )

    # Get telemetry for context
    try:
        telemetry = state.mcp.get_all_telemetry()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"MCP telemetry error: {e}")

    # Get conversation context
    conv_context = ""
    if state.convo:
        conv_context = state.convo.get_context_string()

    # Process through NLU agent
    try:
        parsed_intent, decisions = state.nlu_agent.process_command(
            body.text, telemetry_list=telemetry,
            conversation_context=conv_context,
        )
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"NLU processing error: {e}"
        )

    # Submit decisions to blockchain + execute
    submission_results = []
    if decisions:
        try:
            submission_results = _submit_nlu_decisions(decisions, state)
        except Exception as e:
            logger.warning(f"NLU decision submission error: {e}")

    # Build response text
    response_text = parsed_intent.query_response or ""
    if not response_text and submission_results:
        actions_str = ", ".join(
            f"{r['action']} on {r['target_device']}" for r in submission_results
        )
        response_text = f"Executed: {actions_str}"
    if not response_text:
        if parsed_intent.intent_type == "unknown":
            response_text = (
                "Sorry, I couldn't understand that command. "
                "Try something like: \"Turn on the living room light\" "
                "or \"Set temperature to 24\"."
            )
        elif parsed_intent.intent_type == "command" and not submission_results:
            response_text = "Command recognized but no device actions were generated."
        elif not response_text:
            response_text = f"Processed as '{parsed_intent.intent_type}' intent."

    # Extract mentioned devices
    devices_mentioned = list({
        r["target_device"] for r in submission_results
    })
    if parsed_intent.actions:
        for a in parsed_intent.actions:
            if isinstance(a, dict) and a.get("target_device"):
                if a["target_device"] not in devices_mentioned:
                    devices_mentioned.append(a["target_device"])

    # Store conversation turn
    if state.convo:
        state.convo.add_turn(
            user_text=body.text,
            intent_type=parsed_intent.intent_type,
            actions=parsed_intent.actions,
            response=response_text,
            devices_mentioned=devices_mentioned,
        )

    # Store in off-chain DB
    if state.store:
        try:
            state.store.store_conversation_turn(
                user_text=body.text,
                intent_type=parsed_intent.intent_type,
                actions_json=json.dumps(parsed_intent.actions),
                response=response_text,
                confidence=parsed_intent.confidence,
                devices_mentioned=",".join(devices_mentioned),
            )
        except Exception:
            pass

    return {
        "text": body.text,
        "intent": parsed_intent.intent_type,
        "intent_type": parsed_intent.intent_type,
        "confidence": parsed_intent.confidence,
        "actions": parsed_intent.actions,
        "query_response": parsed_intent.query_response,
        "decisions": submission_results,
        "response": response_text,
    }


@router.get("/history")
def get_conversation_history() -> dict[str, Any]:
    """Get conversation history from off-chain store + in-memory context."""
    state = _require_active_session()

    # Try off-chain database first (persists across restarts)
    history: list[dict] = []
    if state.store:
        try:
            history = state.store.get_conversation_history(limit=50)
        except Exception:
            pass

    # Fallback: use in-memory conversation manager if DB is empty
    if not history and state.convo:
        for turn in state.convo._turns:
            history.append({
                "user_text": turn.user_text,
                "intent_type": turn.intent_type,
                "response": turn.response,
                "confidence": 0.0,
                "devices_mentioned": ",".join(turn.devices_mentioned),
                "timestamp": turn.timestamp,
            })

    return {"history": history}


@router.get("/stats")
def get_nlu_stats() -> dict[str, Any]:
    """Get NLU interaction statistics from off-chain store.

    Returns a flat structure matching what the frontend expects:
    total_commands, avg_confidence, intent_distribution.
    """
    state = _require_active_session()

    # From off-chain DB
    db_stats: dict = {}
    if state.store:
        try:
            db_stats = state.store.get_conversation_stats()
        except Exception:
            pass

    # From in-memory session
    session_summary: dict = {}
    if state.convo:
        session_summary = state.convo.get_session_summary()

    total = db_stats.get("total_interactions", 0) or session_summary.get("total_turns", 0)
    avg_conf = db_stats.get("avg_confidence")
    intent_dist = db_stats.get("by_intent") or session_summary.get("intent_distribution", {})

    return {
        "total_commands": total,
        "avg_confidence": round(avg_conf, 3) if avg_conf else None,
        "intent_distribution": intent_dist,
    }
