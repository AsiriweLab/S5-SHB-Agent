"""
Agent operations endpoints.

Provides REST access to the 10-agent multi-agent system:
- List agents with roles, priorities, models
- Agent detail (permissions, feedback stats)
- Run single agent cycle
- Run all 7 LLM agents in parallel
- Decision and feedback history
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
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


def submit_decisions(decisions, state) -> list[dict[str, Any]]:
    """Submit agent decisions to blockchain, store reasoning, execute via MCP.

    Submit decisions to blockchain, store reasoning, execute via MCP.
    """
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

        if accepted and conflict:
            state.store.store_conflict(conflict)
        elif not accepted and conflict:
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
                logger.warning(f"MCP execution failed: {e}")

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
            "conflict": conflict,
            "reasoning": dec.reasoning_text[:200],
        })

        # Broadcast agent event via WebSocket
        try:
            from web.ws.streams import emit_agent_event
            emit_agent_event(
                "agent_decision",
                agent_id=tx.agent_id,
                action=tx.action,
                target_device=tx.target_device,
                confidence=tx.confidence,
                accepted=accepted,
                executed=executed,
            )
            if conflict:
                emit_agent_event(
                    "agent_conflict",
                    agent_a=conflict.get("agent_a_id", ""),
                    agent_b=conflict.get("agent_b_id", ""),
                    winner=conflict.get("winner_id", ""),
                    device=tx.target_device,
                )
        except Exception:
            pass

    return results


def load_feedback(agent_id: str, state) -> str:
    """Build feedback context string from recent outcomes."""
    from engine.config import FEEDBACK_ENABLED, FEEDBACK_HISTORY_SIZE
    if not FEEDBACK_ENABLED:
        return ""
    outcomes = state.store.get_recent_outcomes(agent_id, limit=FEEDBACK_HISTORY_SIZE)
    if not outcomes:
        return ""
    lines = ["Your recent decisions and outcomes:"]
    for o in outcomes:
        status = "ACCEPTED" if o["accepted"] else "REJECTED"
        conflict_str = (
            f" (conflict, winner: {o['conflict_winner']})"
            if o["conflict"] else ""
        )
        lines.append(
            f"  - {o['action']} -> {o['target_device']}: "
            f"{status}{conflict_str} (confidence: {o['confidence']:.2f})"
        )
    stats = state.store.get_outcome_stats(agent_id)
    lines.append(
        f"  Acceptance rate: {stats['acceptance_rate']:.0%}, "
        f"Conflict rate: {stats['conflict_rate']:.0%}"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/")
async def list_agents() -> dict[str, Any]:
    """List all 10 agents with roles, priorities, and models.

    Works with or without an active session — falls back to static
    AGENT_DEFINITIONS when no session is active (no blockchain priorities).
    """
    state = get_app_state()

    from engine.config import AGENT_DEFINITIONS

    agents_list = []
    for agent_id, defn in AGENT_DEFINITIONS.items():
        priority = defn["priority"]
        if state.is_active and state.chain:
            priority = state.chain.priorities.get_priority(agent_id)

        agents_list.append({
            "agent_id": agent_id,
            "role": defn["role"],
            "priority": priority,
            "model": defn.get("model", "n/a"),
            "description": defn.get("description", ""),
            "allowed_device_types": defn["allowed_device_types"],
            "is_llm_agent": defn["role"] not in ("nlu", "anomaly", "arbitration"),
            "is_specialized": defn["role"] in ("nlu", "anomaly", "arbitration"),
        })

    # Sort by priority descending
    agents_list.sort(key=lambda a: a["priority"], reverse=True)

    return {
        "total_agents": len(agents_list),
        "llm_agents": sum(1 for a in agents_list if a["is_llm_agent"]),
        "specialized_agents": sum(1 for a in agents_list if a["is_specialized"]),
        "agents": agents_list,
    }


@router.get("/activity-history")
async def get_activity_history(
    limit: int = Query(200, ge=1, le=500),
) -> dict[str, Any]:
    """Get full activity history across all agents (persisted in SQLite).

    Returns decisions, conflicts, and summary counters so the frontend
    can restore its activity feed and stats after a page refresh.

    Only returns data for the currently active session to prevent
    cross-session data leakage.
    """
    state = get_app_state()
    store = state.store

    if store is None:
        return {"entries": [], "counters": {
            "total_decisions": 0, "total_accepted": 0, "total_conflicts": 0,
        }}

    outcomes = store.get_all_recent_outcomes(limit=limit)

    entries = []
    total_decisions = 0
    total_accepted = 0
    total_conflicts = 0

    for row in outcomes:
        total_decisions += 1
        accepted = bool(row.get("accepted"))
        conflict = bool(row.get("conflict"))
        if accepted:
            total_accepted += 1
        if conflict:
            total_conflicts += 1

        agent_id = row.get("agent_id", "")
        role = agent_id.split("-")[0] if "-" in agent_id else agent_id

        entries.append({
            "type": "decision",
            "timestamp": row.get("timestamp", 0),
            "agentId": agent_id,
            "agentRole": role,
            "action": row.get("action", ""),
            "targetDevice": row.get("target_device", ""),
            "confidence": row.get("confidence", 0),
            "accepted": accepted,
            "executed": accepted,
            "reasoning": row.get("reasoning_summary", ""),
        })

        if conflict:
            entries.append({
                "type": "conflict",
                "timestamp": row.get("timestamp", 0),
                "winner": row.get("conflict_winner", ""),
                "device": row.get("target_device", ""),
            })

    return {
        "entries": entries,
        "counters": {
            "total_decisions": total_decisions,
            "total_accepted": total_accepted,
            "total_conflicts": total_conflicts,
        },
    }


@router.get("/{agent_id}")
async def get_agent_detail(agent_id: str) -> dict[str, Any]:
    """Get detailed info for a specific agent.

    Works with or without an active session — returns static definition data
    always, and adds permissions/stats when a session is active.
    """
    from engine.config import AGENT_DEFINITIONS

    defn = AGENT_DEFINITIONS.get(agent_id)
    if not defn:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")

    state = get_app_state()

    # Permission details from blockchain (only when session active)
    permissions = []
    if state.is_active and state.chain:
        raw_perms = state.chain.permissions.to_dict()
        agent_perms = raw_perms.get(agent_id, [])
        permissions = [
            {"device_id": p[0], "command": p[1]}
            for p in agent_perms
        ]

    # Decision stats from offchain store (only when session active)
    stats = {}
    if state.is_active and state.store:
        try:
            stats = state.store.get_outcome_stats(agent_id)
        except Exception:
            pass

    return {
        "agent_id": agent_id,
        "role": defn["role"],
        "priority": defn["priority"],
        "model": defn.get("model", "n/a"),
        "description": defn.get("description", ""),
        "allowed_device_types": defn["allowed_device_types"],
        "permissions": permissions,
        "decision_stats": stats,
    }


@router.post("/run-cycle")
async def run_agent_cycle(
    agent_id: Optional[str] = Query(
        None, description="Run a specific agent (omit for all 7 LLM agents)"
    ),
    duration: float = Query(
        0, ge=0, le=86400,
        description="Duration in seconds. 0 = single run. >0 = repeat sequentially for this long.",
    ),
    interval: float = Query(
        60, ge=5, le=86400,
        description="Seconds between cycles when duration > 0.",
    ),
) -> dict[str, Any]:
    """Run one perception-decision cycle (sequential).

    With duration=0: runs once and returns.
    With duration>0: starts a background loop running sequential cycles
    for the specified duration, at the specified interval.
    """
    state = _require_active_session()

    if not state.mcp or not state.chain:
        raise HTTPException(status_code=400, detail="MCP or blockchain not initialized")

    # Duration mode — start as auto-run with sequential cycles
    if duration > 0:
        if state.auto_run_active:
            return {"status": "already_running", "cycles_completed": state.auto_run_cycles}
        state.auto_run_active = True
        state.auto_run_interval = interval
        state.auto_run_duration = duration
        state.auto_run_start_time = time.time()
        state.auto_run_task = asyncio.create_task(_auto_run_loop(interval, duration))
        return {
            "status": "started",
            "mode": "cycle",
            "interval": interval,
            "duration": duration,
            "cycles_completed": state.auto_run_cycles,
        }

    # Single run (original behavior)
    telemetry = await asyncio.to_thread(state.mcp.get_all_telemetry)

    if agent_id:
        agent = state.agents.get(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail=f"LLM agent '{agent_id}' not found")
        feedback = load_feedback(agent_id, state)
        agent.set_feedback_context(feedback)
        t0 = time.perf_counter()
        try:
            decisions = await asyncio.to_thread(agent.perceive_and_decide, telemetry)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Agent '{agent_id}' error: {e}")
        elapsed = time.perf_counter() - t0
        submission_results = await asyncio.to_thread(submit_decisions, decisions, state)
        return {
            "agent_id": agent_id,
            "decisions": len(decisions),
            "elapsed_seconds": round(elapsed, 3),
            "results": submission_results,
        }
    else:
        all_results = []
        t0 = time.perf_counter()
        for aid, agent in state.agents.items():
            feedback = load_feedback(aid, state)
            agent.set_feedback_context(feedback)
            try:
                decisions = await asyncio.to_thread(agent.perceive_and_decide, telemetry)
                results = await asyncio.to_thread(submit_decisions, decisions, state)
                all_results.extend(results)
            except Exception as e:
                logger.warning(f"Agent '{aid}' error: {e}")
                all_results.append({"agent_id": aid, "error": str(e)})
        elapsed = time.perf_counter() - t0
        return {
            "agents_run": len(state.agents),
            "total_decisions": len(all_results),
            "elapsed_seconds": round(elapsed, 3),
            "results": all_results,
        }


@router.post("/run-parallel")
async def run_agents_parallel(
    duration: float = Query(
        0, ge=0, le=86400,
        description="Duration in seconds. 0 = single run. >0 = repeat in parallel for this long.",
    ),
    interval: float = Query(
        60, ge=5, le=86400,
        description="Seconds between cycles when duration > 0.",
    ),
) -> dict[str, Any]:
    """Run all 7 LLM agents concurrently (async parallel execution).

    With duration=0: runs once and returns.
    With duration>0: starts a background loop running parallel cycles
    for the specified duration, at the specified interval.
    """
    state = _require_active_session()

    if not state.mcp or not state.chain:
        raise HTTPException(status_code=400, detail="MCP or blockchain not initialized")

    # Duration mode — start as auto-run
    if duration > 0:
        if state.auto_run_active:
            return {"status": "already_running", "cycles_completed": state.auto_run_cycles}
        state.auto_run_active = True
        state.auto_run_interval = interval
        state.auto_run_duration = duration
        state.auto_run_start_time = time.time()
        state.auto_run_task = asyncio.create_task(_auto_run_loop(interval, duration))
        return {
            "status": "started",
            "mode": "parallel",
            "interval": interval,
            "duration": duration,
            "cycles_completed": state.auto_run_cycles,
        }

    # Single run (original behavior)
    telemetry = await asyncio.to_thread(state.mcp.get_all_telemetry)

    for aid, agent in state.agents.items():
        feedback = load_feedback(aid, state)
        agent.set_feedback_context(feedback)

    t0 = time.perf_counter()
    agents_list = list(state.agents.values())
    tasks = [agent.perceive_and_decide_async(telemetry) for agent in agents_list]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_decisions = []
    errors = []
    for agent, result in zip(agents_list, results):
        if isinstance(result, Exception):
            errors.append({"agent_id": agent.agent_id, "error": str(result)})
        elif result:
            all_decisions.extend(result)

    elapsed_reasoning = time.perf_counter() - t0

    submission_results = await asyncio.to_thread(submit_decisions, all_decisions, state)

    block = state.chain.mine_pending()
    block_info = None
    if block:
        block_info = {
            "index": block.index, "hash": block.hash[:32],
            "transaction_count": len(block.transactions),
            "difficulty_used": getattr(block, "difficulty_used", None),
        }
        try:
            from web.ws.streams import emit_blockchain_event
            emit_blockchain_event(
                "block_mined", block_index=block.index,
                block_hash=block.hash[:32], tx_count=len(block.transactions),
                difficulty=getattr(block, "difficulty_used", None),
            )
        except Exception:
            pass

    elapsed_total = time.perf_counter() - t0

    return {
        "agents_run": len(agents_list),
        "total_decisions": len(all_decisions),
        "elapsed_reasoning_seconds": round(elapsed_reasoning, 3),
        "elapsed_total_seconds": round(elapsed_total, 3),
        "results": submission_results,
        "errors": errors,
        "block_mined": block_info,
    }


# ---------------------------------------------------------------------------
# Auto-Run: Continuous agent cycles for real-mode operation
# ---------------------------------------------------------------------------

async def _auto_run_loop(interval: float, duration: float) -> None:
    """Background loop that runs agent cycles at a fixed interval.

    Args:
        interval: Seconds between each cycle.
        duration: Total seconds to run (0 = run until manually stopped).
    """
    state = get_app_state()
    dur_label = f"{duration}s" if duration > 0 else "unlimited"
    logger.info(f"Auto-run started: interval={interval}s, duration={dur_label}")

    while state.auto_run_active:
        # Check duration limit
        if duration > 0:
            elapsed_total = time.time() - state.auto_run_start_time
            if elapsed_total >= duration:
                logger.info(f"Auto-run duration reached ({duration}s), auto-stopping")
                try:
                    from web.ws.streams import emit_agent_event
                    emit_agent_event(
                        "auto_run_stopped",
                        reason="duration_reached",
                        duration=duration,
                        cycles_completed=state.auto_run_cycles,
                    )
                except Exception:
                    pass
                break
        try:
            # Run one parallel cycle (reuse the existing logic)
            if not state.is_active or not state.mcp or not state.chain:
                logger.warning("Auto-run: session not active, stopping")
                break

            telemetry = await asyncio.to_thread(state.mcp.get_all_telemetry)

            # Inject feedback
            for aid, agent in state.agents.items():
                feedback = load_feedback(aid, state)
                agent.set_feedback_context(feedback)

            # Run all agents with staggered starts to avoid 429 rate limits
            t0 = time.perf_counter()
            agents_list = list(state.agents.values())

            all_decisions = []
            errors_count = 0

            # Stagger: launch agents 2s apart to spread API calls
            async def _run_agent_with_delay(agent, delay: float):
                if delay > 0:
                    await asyncio.sleep(delay)
                return await agent.perceive_and_decide_async(telemetry)

            tasks = [
                _run_agent_with_delay(agent, i * 2.0)
                for i, agent in enumerate(agents_list)
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for agent, result in zip(agents_list, results):
                if isinstance(result, Exception):
                    err_str = str(result)
                    # Retry once on 429 rate limit after a backoff
                    if "429" in err_str or "too many" in err_str.lower():
                        logger.warning(f"Auto-run agent '{agent.agent_id}' rate-limited, retrying in 10s...")
                        await asyncio.sleep(10)
                        try:
                            retry_result = await agent.perceive_and_decide_async(telemetry)
                            if retry_result:
                                all_decisions.extend(retry_result)
                                continue
                        except Exception:
                            pass
                    errors_count += 1
                    logger.warning(f"Auto-run agent '{agent.agent_id}' error: {result}")
                elif result:
                    all_decisions.extend(result)

            # Submit decisions
            submission_results = await asyncio.to_thread(
                submit_decisions, all_decisions, state
            )

            # Mine pending transactions
            block = state.chain.mine_pending()
            block_index = None
            if block:
                block_index = block.index
                try:
                    from web.ws.streams import emit_blockchain_event
                    emit_blockchain_event(
                        "block_mined",
                        block_index=block.index,
                        block_hash=block.hash[:32],
                        tx_count=len(block.transactions),
                        difficulty=getattr(block, "difficulty_used", None),
                    )
                except Exception:
                    pass

            elapsed = time.perf_counter() - t0
            state.auto_run_cycles += 1

            # Broadcast cycle complete event
            try:
                from web.ws.streams import emit_agent_event
                emit_agent_event(
                    "auto_cycle_complete",
                    cycle_label=str(state.auto_run_cycles),
                    agents_run=len(agents_list),
                    decisions=len(all_decisions),
                    errors=errors_count,
                    block_mined=block_index,
                    elapsed_seconds=round(elapsed, 3),
                )
            except Exception:
                pass

            logger.info(
                f"Auto-run cycle #{state.auto_run_cycles}: "
                f"{len(all_decisions)} decisions, {errors_count} errors, "
                f"{round(elapsed, 1)}s"
            )

        except asyncio.CancelledError:
            logger.info("Auto-run cancelled")
            break
        except Exception as e:
            logger.error(f"Auto-run cycle error: {e}")

        # Wait for next interval (check cancellation every second)
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            logger.info("Auto-run cancelled during sleep")
            break

    state.auto_run_active = False
    state.auto_run_task = None
    logger.info(f"Auto-run stopped after {state.auto_run_cycles} cycles")


@router.post("/auto-run/start")
async def start_auto_run(
    interval: float = Query(60, ge=5, le=86400, description="Seconds between cycles (min 5s)"),
    duration: float = Query(0, ge=0, le=86400, description="Total seconds to run (0 = unlimited)"),
) -> dict[str, Any]:
    """Start continuous auto-run of agent cycles.

    Args:
        interval: Seconds between each cycle (min 5s).
        duration: Total duration in seconds. Auto-stops when reached. 0 = run until manually stopped.
    """
    state = _require_active_session()

    if not state.mcp or not state.chain:
        raise HTTPException(status_code=400, detail="MCP or blockchain not initialized")

    if state.auto_run_active:
        return {
            "status": "already_running",
            "interval": state.auto_run_interval,
            "duration": state.auto_run_duration,
            "cycles_completed": state.auto_run_cycles,
        }

    state.auto_run_active = True
    state.auto_run_interval = interval
    state.auto_run_duration = duration
    state.auto_run_start_time = time.time()
    state.auto_run_task = asyncio.create_task(_auto_run_loop(interval, duration))

    return {
        "status": "started",
        "interval": interval,
        "duration": duration,
        "cycles_completed": state.auto_run_cycles,
    }


@router.post("/auto-run/stop")
async def stop_auto_run() -> dict[str, Any]:
    """Stop the auto-run background loop."""
    state = get_app_state()

    if not state.auto_run_active:
        return {"status": "not_running", "cycles_completed": state.auto_run_cycles}

    state.auto_run_active = False
    if state.auto_run_task:
        state.auto_run_task.cancel()
        try:
            await state.auto_run_task
        except (asyncio.CancelledError, Exception):
            pass
        state.auto_run_task = None

    return {"status": "stopped", "cycles_completed": state.auto_run_cycles}


@router.get("/auto-run/status")
async def get_auto_run_status() -> dict[str, Any]:
    """Get the current auto-run status."""
    state = get_app_state()
    remaining = 0.0
    if state.auto_run_active and state.auto_run_duration > 0:
        elapsed = time.time() - state.auto_run_start_time
        remaining = max(0.0, state.auto_run_duration - elapsed)
    return {
        "active": state.auto_run_active,
        "interval": state.auto_run_interval,
        "duration": state.auto_run_duration,
        "remaining": round(remaining, 1),
        "cycles_completed": state.auto_run_cycles,
    }


@router.get("/{agent_id}/feedback")
async def get_agent_feedback(
    agent_id: str,
    limit: int = Query(10, ge=1, le=100),
) -> dict[str, Any]:
    """Get recent feedback history for an agent."""
    state = _require_active_session()

    from engine.config import AGENT_DEFINITIONS
    if agent_id not in AGENT_DEFINITIONS:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")

    if not state.store:
        return {"agent_id": agent_id, "outcomes": [], "stats": {}}

    outcomes = state.store.get_recent_outcomes(agent_id, limit=limit)
    stats = state.store.get_outcome_stats(agent_id)

    return {
        "agent_id": agent_id,
        "outcomes": outcomes,
        "stats": stats,
    }


@router.get("/{agent_id}/decisions")
async def get_agent_decisions(
    agent_id: str,
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    """Get decision history for an agent from off-chain DB.

    Works with or without an active session — returns empty list when
    no session/store is available.
    """
    from engine.config import AGENT_DEFINITIONS
    if agent_id not in AGENT_DEFINITIONS:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")

    state = get_app_state()

    if not state.is_active or not state.store:
        return {"agent_id": agent_id, "decisions": [], "total_decisions": 0}

    outcomes = state.store.get_recent_outcomes(agent_id, limit=limit)

    return {
        "agent_id": agent_id,
        "total_decisions": len(outcomes),
        "decisions": outcomes,
    }
