"""
Audit endpoints.

Programmatic version of audit.py CLI tool. Runs 18-section audit
and returns structured JSON results instead of printing to stdout.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from web.core.state import get_app_state

router = APIRouter()

# Module-level cache for last audit results
_last_audit_results: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_active_session():
    """Raise 400 if no active session."""
    state = get_app_state()
    if not state.is_active:
        raise HTTPException(
            status_code=400,
            detail="No active session.",
        )
    return state


def _run_audit(state) -> dict[str, Any]:
    """Run the 18-section audit programmatically.

    Returns structured results dict instead of printing to stdout.
    """
    from engine.config import AGENT_DEFINITIONS

    chain = state.chain
    store = state.store
    results = {"sections": {}, "summary": {}}

    # Section 1: Blockchain integrity
    s1 = {"total_blocks": len(chain.chain), "valid": chain.validate_chain()}
    s1["total_transactions"] = sum(len(b.transactions) for b in chain.chain)
    s1["rejected"] = len(chain.rejected_tx)
    s1["blocks"] = []
    for b in chain.chain:
        status = "OK" if b.hash == b.compute_hash() else "CORRUPTED"
        s1["blocks"].append({
            "index": b.index,
            "tx_count": len(b.transactions),
            "status": status,
        })
    results["sections"]["1_blockchain_integrity"] = s1

    # Section 2: Reasoning hash verification
    s2 = {"checks": 0, "passed": 0, "missing": 0}
    for b in chain.chain:
        for tx in b.transactions:
            if tx.tx_type == "agent_action" and tx.reasoning_hash != "N/A":
                s2["checks"] += 1
                v = store.verify_reasoning(tx.reasoning_hash)
                if v["verified"]:
                    s2["passed"] += 1
                elif v["reason"] == "Not found in off-chain store":
                    s2["missing"] += 1
    results["sections"]["2_reasoning_verification"] = s2

    # Section 3: Telemetry anchor verification
    s3 = {"checks": 0, "passed": 0}
    for b in chain.chain:
        for tx in b.transactions:
            if tx.tx_type == "telemetry_anchor":
                s3["checks"] += 1
                batch_id = tx.params.get("batch_id")
                v = store.verify_anchor(batch_id)
                if v["verified"]:
                    s3["passed"] += 1
    results["sections"]["3_anchor_verification"] = s3

    # Section 4: Off-chain database stats
    s4 = store.stats()
    results["sections"]["4_offchain_stats"] = s4

    # Section 5: ML/DL readiness
    s5 = {
        "continuous_data": len(store.query_continuous(limit=5)) > 0,
        "event_data": len(store.query_events(limit=5)) > 0,
        "alert_data": len(store.query_alerts(limit=5)) > 0,
    }
    results["sections"]["5_ml_readiness"] = s5

    # Section 6: Rejected transactions
    s6 = {"count": len(chain.rejected_tx), "rejected": []}
    for r in chain.rejected_tx[:20]:
        tx_info = r.get("tx", {})
        s6["rejected"].append({
            "agent_id": tx_info.get("agent_id", "?"),
            "action": tx_info.get("action", "?"),
            "reason": r.get("reason", "?"),
        })
    results["sections"]["6_rejected_transactions"] = s6

    # Section 7: Per-agent reasoning
    agent_reasoning = {}
    for b in chain.chain:
        for tx in b.transactions:
            if tx.tx_type == "agent_action":
                agent_reasoning.setdefault(tx.agent_id, []).append(tx)

    s7 = {}
    for agent_id in AGENT_DEFINITIONS:
        txs = agent_reasoning.get(agent_id, [])
        verified = 0
        for tx in txs:
            v = store.verify_reasoning(tx.reasoning_hash)
            if v.get("verified"):
                verified += 1
        s7[agent_id] = {"total_tx": len(txs), "verified": verified}
    results["sections"]["7_per_agent_reasoning"] = s7

    # Section 8: Conflict resolution
    conflicts = store.query_conflicts()
    s8 = {"count": len(conflicts), "priority_correct": 0}
    for c in conflicts:
        pa, pb = c["agent_a_priority"], c["agent_b_priority"]
        if pa != pb:
            expected = c["agent_a_id"] if pa > pb else c["agent_b_id"]
            if c["winner_id"] == expected:
                s8["priority_correct"] += 1
        else:
            s8["priority_correct"] += 1
    results["sections"]["8_conflict_resolution"] = s8

    # Section 9: Priority hierarchy
    s9 = {"correct": True, "priorities": {}}
    for agent_id, defn in AGENT_DEFINITIONS.items():
        expected = defn["priority"]
        actual = chain.priorities.get_priority(agent_id)
        s9["priorities"][agent_id] = {
            "expected": expected, "actual": actual,
            "match": abs(actual - expected) < 0.01,
        }
        if abs(actual - expected) >= 0.01:
            s9["correct"] = False
    results["sections"]["9_priority_hierarchy"] = s9

    # Section 10: MCP protocol (check for mcp_audit tx)
    s10 = {"mcp_audit_found": False}
    for b in chain.chain:
        for tx in b.transactions:
            if tx.tx_type == "mcp_audit":
                s10["mcp_audit_found"] = True
                s10["total_mcp_calls"] = tx.params.get("total_mcp_calls", 0)
                break
    results["sections"]["10_mcp_protocol"] = s10

    # Section 11: Async pipeline
    agents_on_chain = set()
    for b in chain.chain:
        for tx in b.transactions:
            if tx.tx_type == "agent_action":
                agents_on_chain.add(tx.agent_id)
    s11 = {
        "agents_on_chain": len(agents_on_chain),
        "total_agents": len(AGENT_DEFINITIONS),
        "all_present": len(agents_on_chain) == len(AGENT_DEFINITIONS),
    }
    results["sections"]["11_async_pipeline"] = s11

    # Section 12: Health monitoring
    health_summary = store.get_health_summary()
    s12 = {
        "total_checks": health_summary["total_checks"],
        "uptime_pct": health_summary.get("uptime_pct", 0),
        "fallback_activations": health_summary.get("fallback_activations", 0),
    }
    results["sections"]["12_health_monitoring"] = s12

    # Section 13: Feedback & model audit
    s13 = {
        "decision_outcomes": s4.get("agent_decision_outcomes_count", 0),
        "model_audit_found": False,
    }
    for b in chain.chain:
        for tx in b.transactions:
            if tx.tx_type == "model_audit":
                s13["model_audit_found"] = True
                break
    results["sections"]["13_feedback_model_audit"] = s13

    # Section 14: NLU communication
    conv_stats = store.get_conversation_stats()
    s14 = {
        "total_interactions": conv_stats.get("total_interactions", 0),
        "by_intent": conv_stats.get("by_intent", {}),
    }
    results["sections"]["14_nlu_communication"] = s14

    # Section 15: Anomaly detection
    anom_stats = store.get_anomaly_stats()
    s15 = {
        "total_scans": anom_stats.get("total_scans", 0),
        "anomalies_detected": anom_stats.get("anomalies_detected", 0),
    }
    results["sections"]["15_anomaly_detection"] = s15

    # Section 16: Arbitration
    arb_stats = store.get_arbitration_stats()
    s16 = {
        "total_arbitrations": arb_stats.get("total_arbitrations", 0),
        "safety_overrides": arb_stats.get("safety_overrides", 0),
    }
    results["sections"]["16_arbitration"] = s16

    # Section 17: Adaptive PoW
    adaptive_stats = chain.adaptive.get_stats()
    s17 = {
        "enabled": adaptive_stats["enabled"],
        "current_difficulty": adaptive_stats.get("current_difficulty", 0),
        "difficulty_changes": adaptive_stats.get("difficulty_changes", 0),
    }
    results["sections"]["17_adaptive_pow"] = s17

    # Section 18: Governance (Society 5.0)
    gov_stats = store.get_governance_stats()
    s18 = {
        "total_changes": gov_stats.get("total_changes", 0),
        "locked_violations": gov_stats.get("by_tier", {}).get(4, 0),
        "by_type": gov_stats.get("by_type", {}),
    }
    results["sections"]["18_governance"] = s18

    # Build summary
    results["summary"] = {
        "blockchain_valid": s1["valid"],
        "reasoning_verified": s2["passed"] == s2["checks"],
        "anchors_verified": s3["passed"] == s3["checks"],
        "priority_correct": s9["correct"],
        "locked_violations": s18["locked_violations"],
        "total_sections": 18,
    }

    return results


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/run")
def run_audit() -> dict[str, Any]:
    """Run the 18-section audit on the active session.

    Returns structured results for each section.
    """
    global _last_audit_results

    state = _require_active_session()

    if not state.chain or not state.store:
        raise HTTPException(
            status_code=400,
            detail="Blockchain or off-chain store not initialized.",
        )

    try:
        results = _run_audit(state)
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Audit error: {e}"
        )

    _last_audit_results = results
    return results


@router.get("/results")
def get_audit_results() -> dict[str, Any]:
    """Get the most recent audit results.

    Returns 404 if no audit has been run yet.
    """
    _require_active_session()

    if _last_audit_results is None:
        raise HTTPException(
            status_code=404,
            detail="No audit results available. Run POST /audit/run first.",
        )

    return _last_audit_results
