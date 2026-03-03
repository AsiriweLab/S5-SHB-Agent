"""
Simulation Orchestrator -- Bridges S5-HES simulation with ABC session engine.

When a simulation starts, the orchestrator:
1. Auto-creates an ABC session (blockchain, agents, off-chain DB) if none active
2. Runs a telemetry bridge loop storing MCP readings to off-chain DB every 10s
3. Runs agent cycles every 20s:
   a. 7 LLM agents perceive + decide in parallel
   b. Anomaly agent: accumulate → auto-train → detect anomalies
   c. Arbitration agent: resolve conflicts (safety override → LLM → ML → priority)
   d. Submit decisions + mine

All loops check state.simulation_active and exit cleanly when False.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Optional

from loguru import logger

from web.core.state import get_app_state
from web.core.home_store import get_home_store
from web.ws.streams import emit_agent_event, emit_blockchain_event


# ---------------------------------------------------------------------------
# Module-level background task references
# ---------------------------------------------------------------------------

_telemetry_bridge_task: Optional[asyncio.Task] = None
_agent_cycle_task: Optional[asyncio.Task] = None

# Anomaly agent accumulation counter (reset on each simulation start)
_anomaly_accumulations: int = 0
_ANOMALY_TRAIN_AFTER: int = 3  # auto-train after this many accumulations


# ---------------------------------------------------------------------------
# Auto-session creation
# ---------------------------------------------------------------------------

async def auto_ensure_session() -> bool:
    """Auto-create an ABC session from HomeStore if none is active.

    Returns True if a session is active (existing or newly created).
    """
    state = get_app_state()
    if state.is_active:
        logger.debug("Session already active, skipping auto-creation")
        return True

    # Check home config availability
    store = get_home_store()
    home = store.get_current_home()
    if home is None:
        logger.warning("No home configured — cannot auto-create session")
        return False

    home_dict = store.to_session_dict()
    home_devices = home_dict.get("devices", [])
    home_rooms = home_dict.get("rooms", [])

    if not home_devices:
        logger.warning("No devices in home config — cannot auto-create session")
        return False

    # Generate timestamped session name
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    session_name = f"sim_{ts}"

    # Ensure name is unique
    from web.core.bridge import _get_session_manager
    session_mgr = _get_session_manager()
    if session_mgr.session_exists(session_name):
        import uuid
        session_name = f"sim_{ts}_{uuid.uuid4().hex[:4]}"

    # Save S5-HES references and simulation flag before session creation.
    # setup_fresh_session() calls reset_app_state() which creates a NEW AppState,
    # wiping s5_hes_client, s5_hes_available, and simulation_active.
    old_state = state
    s5_client = old_state.s5_hes_client
    s5_available = old_state.s5_hes_available
    sim_active = old_state.simulation_active

    # Create session (sync — offload to thread)
    from web.core.bridge import setup_fresh_session
    try:
        state, _ = await asyncio.to_thread(
            setup_fresh_session,
            session_name=session_name,
            home_devices=home_devices,
            home_rooms=home_rooms,
            home_config=home_dict,
        )
    except Exception as e:
        logger.error(f"Auto-session creation failed: {e}")
        return False

    # Restore S5-HES references and simulation flag on the new state
    new_state = get_app_state()
    new_state.s5_hes_client = s5_client
    new_state.s5_hes_available = s5_available
    new_state.simulation_active = sim_active

    logger.info(f"Auto-created session '{session_name}' for simulation")
    emit_agent_event("session_auto_created", session_name=session_name)
    return True


# ---------------------------------------------------------------------------
# Telemetry bridge loop
# ---------------------------------------------------------------------------

async def telemetry_bridge_loop() -> None:
    """Store MCP telemetry to off-chain DB and anchor to blockchain every 10s.

    Each tick:
      1. Read all device telemetry from MCP
      2. Store batch to off-chain SQLite
      3. Create Merkle anchor of accumulated records
      4. Record anchor transaction on blockchain
      5. Mine the anchor block
    """
    state = get_app_state()
    bridge_tick = 0
    logger.info("Telemetry bridge loop started")

    while state.simulation_active:
        try:
            if state.mcp and state.store and state.chain:
                bridge_tick += 1

                # Sync HES telemetry into device states before reading
                if state.hes_sync and state.s5_hes_client:
                    from engine.mcp_server import get_device_layer
                    dl = get_device_layer()
                    if dl:
                        sync_result = await state.hes_sync.sync(
                            state.s5_hes_client, dl
                        )
                        _updated = sync_result.get("devices_updated", 0)
                        if _updated > 0:
                            logger.debug(
                                f"HES sync (bridge #{bridge_tick}): "
                                f"{_updated} devices updated"
                            )

                # Store telemetry to off-chain DB
                telemetry = await asyncio.to_thread(state.mcp.get_all_telemetry)
                counts = await asyncio.to_thread(
                    state.store.store_telemetry_batch, telemetry
                )
                logger.debug(
                    f"Telemetry bridge #{bridge_tick}: stored "
                    f"{counts['continuous']}C {counts['events']}E {counts['alerts']}A"
                )

                # Anchor accumulated telemetry to blockchain
                anchor = await asyncio.to_thread(state.store.create_anchor)
                if anchor:
                    await asyncio.to_thread(
                        state.chain.record_telemetry_anchor,
                        anchor["merkle_root"],
                        anchor["batch_id"],
                        anchor["record_count"],
                    )
                    anchor_block = await asyncio.to_thread(
                        state.chain.mine_pending
                    )
                    if anchor_block:
                        await asyncio.to_thread(
                            state.store.update_anchor_block,
                            anchor["batch_id"],
                            anchor_block.index,
                        )
                        emit_blockchain_event(
                            "telemetry_anchored",
                            batch_id=anchor["batch_id"],
                            merkle_root=anchor["merkle_root"][:32],
                            record_count=anchor["record_count"],
                            block_index=anchor_block.index,
                            source="telemetry_bridge",
                        )
                        logger.info(
                            f"Telemetry bridge #{bridge_tick}: anchored "
                            f"{anchor['record_count']} records → "
                            f"block #{anchor_block.index}"
                        )
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.debug(f"Telemetry bridge error: {e}")

        await asyncio.sleep(10.0)

    logger.info(f"Telemetry bridge loop stopped after {bridge_tick} ticks")


# ---------------------------------------------------------------------------
# Agent cycle loop
# ---------------------------------------------------------------------------

async def _run_single_agent_cycle(state, cycle_label: str) -> dict[str, Any]:
    """Run one full agent cycle: LLM agents + anomaly + arbitration + mine.

    Phases:
      1.   Read telemetry
      1.5  Anomaly: accumulate → auto-train → detect
      2.   Inject feedback into 7 LLM agents
      3.   Run 7 LLM agents in parallel
      3.5  Merge anomaly corrective decisions
      4.   Arbitrate conflicts (same device, different actions)
      4.5  Submit winning decisions to blockchain
      5.   Mine pending transactions

    Returns summary dict. Extracted so stop_orchestration() can run a final cycle.
    """
    global _anomaly_accumulations

    from web.api.agents import submit_decisions, load_feedback
    from engine.config import AGENT_DEFINITIONS

    if not state.mcp or not state.chain or not state.agents:
        return {"skipped": True}

    logger.info(f"Agent cycle [{cycle_label}] starting")

    # Phase 0.5: Sync HES telemetry into device states
    if state.hes_sync and state.s5_hes_client:
        from engine.mcp_server import get_device_layer
        dl = get_device_layer()
        if dl:
            await state.hes_sync.sync(state.s5_hes_client, dl)

    # Phase 1: Read telemetry
    telemetry = await asyncio.to_thread(state.mcp.get_all_telemetry)

    # Phase 1.5: Anomaly detection (accumulate → train → detect)
    anomaly_decisions = []
    if state.anomaly_agent:
        try:
            # Always accumulate
            await asyncio.to_thread(
                state.anomaly_agent.accumulate_telemetry, telemetry
            )
            _anomaly_accumulations += 1
            logger.debug(
                f"Anomaly agent: accumulated round {_anomaly_accumulations}"
            )

            # Auto-train once we have enough data (dynamic threshold)
            train_after = _ANOMALY_TRAIN_AFTER
            if state.preferences:
                train_after = state.preferences.get(
                    "anomaly_train_cycles", _ANOMALY_TRAIN_AFTER
                )
            if (
                _anomaly_accumulations >= train_after
                and not state.anomaly_agent.trained
            ):
                train_result = await asyncio.to_thread(state.anomaly_agent.train)
                logger.info(
                    f"Anomaly agent auto-trained: "
                    f"{train_result.get('devices_profiled', 0)} devices, "
                    f"{train_result.get('total_samples', 0)} samples"
                )
                emit_agent_event(
                    "anomaly_trained",
                    agent_id="anomaly-agent-009",
                    devices_profiled=train_result.get("devices_profiled", 0),
                    total_samples=train_result.get("total_samples", 0),
                )

            # Detect anomalies if trained
            if state.anomaly_agent.trained:
                anomalies, decisions = await asyncio.to_thread(
                    state.anomaly_agent.detect_and_decide, telemetry
                )
                anomaly_decisions = decisions or []

                # Emit WS events for detected anomalies
                for a in (anomalies or []):
                    if a.is_anomaly:
                        emit_agent_event(
                            "anomaly_detected",
                            agent_id="anomaly-agent-009",
                            device_id=a.device_id,
                            device_type=a.device_type,
                            anomaly_score=round(a.anomaly_score, 3),
                            detectors_triggered=a.detectors_triggered,
                        )

                if anomalies:
                    detected = sum(1 for a in anomalies if a.is_anomaly)
                    logger.info(
                        f"Anomaly agent: scanned {len(anomalies)} devices, "
                        f"{detected} anomalies, "
                        f"{len(anomaly_decisions)} corrective decisions"
                    )
        except Exception as e:
            logger.warning(f"Anomaly agent error: {e}")

    # Phase 2: Inject feedback into 7 LLM agents
    for aid, agent in state.agents.items():
        feedback = load_feedback(aid, state)
        agent.set_feedback_context(feedback)

    # Phase 3: Run all 7 LLM agents in parallel
    agents_list = list(state.agents.values())
    tasks = [
        agent.perceive_and_decide_async(telemetry)
        for agent in agents_list
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_decisions = []
    errors = []
    for agent, result in zip(agents_list, results):
        if isinstance(result, Exception):
            errors.append({"agent_id": agent.agent_id, "error": str(result)})
            logger.warning(f"Agent '{agent.agent_id}' cycle error: {result}")
        elif result:
            all_decisions.extend(result)

    # Phase 3.5: Merge anomaly corrective decisions
    if anomaly_decisions:
        all_decisions.extend(anomaly_decisions)

    # Phase 4: Arbitrate conflicts (intelligent resolution)
    arbitration_count = 0
    if state.arb_agent and len(all_decisions) >= 2:
        try:
            all_decisions, arbitration_count = await _arbitrate_conflicts(
                state, all_decisions, telemetry
            )
        except Exception as e:
            logger.warning(f"Arbitration error (falling back to priority): {e}")

    # Phase 4.5: Submit decisions to blockchain
    if all_decisions:
        await asyncio.to_thread(submit_decisions, all_decisions, state)

    # Phase 5: Mine pending transactions (agent decisions)
    block = await asyncio.to_thread(state.chain.mine_pending)
    if block:
        emit_blockchain_event(
            "block_mined",
            block_index=block.index,
            block_hash=block.hash[:32],
            tx_count=len(block.transactions),
            difficulty=getattr(block, "difficulty_used", None),
            source="agent_cycle",
        )
        logger.info(
            f"Agent cycle [{cycle_label}]: block #{block.index} mined "
            f"({len(block.transactions)} tx)"
        )

    # Broadcast cycle summary
    emit_agent_event(
        "auto_cycle_complete",
        cycle_label=cycle_label,
        agents_run=len(agents_list),
        decisions=len(all_decisions),
        errors=len(errors),
        block_mined=block.index if block else None,
        anomaly_decisions=len(anomaly_decisions),
        arbitrations=arbitration_count,
    )

    logger.info(
        f"Agent cycle [{cycle_label}] complete: "
        f"{len(all_decisions)} decisions, "
        f"{len(errors)} errors, "
        f"{len(anomaly_decisions)} anomaly, "
        f"{arbitration_count} arbitrations"
    )

    return {
        "decisions": len(all_decisions),
        "errors": len(errors),
        "block": block.index if block else None,
        "anomaly_decisions": len(anomaly_decisions),
        "arbitrations": arbitration_count,
    }


async def _arbitrate_conflicts(
    state, decisions: list, telemetry: list
) -> tuple[list, int]:
    """Pre-filter conflicting decisions using the ArbitrationAgent.

    Groups decisions by target_device. For devices with 2+ decisions proposing
    different actions, calls arb_agent.arbitrate() to pick a winner.
    Non-conflicting decisions pass through unchanged.

    Returns (filtered_decisions, arbitration_count).
    """
    from engine.config import AGENT_DEFINITIONS

    # Group by target device
    by_device: dict[str, list] = defaultdict(list)
    for dec in decisions:
        device_id = dec.transaction.target_device
        by_device[device_id].append(dec)

    # Build agent priorities dict
    priorities = {}
    for aid, defn in AGENT_DEFINITIONS.items():
        if state.chain:
            priorities[aid] = state.chain.priorities.get_priority(aid)
        else:
            priorities[aid] = defn["priority"]

    filtered = []
    arb_count = 0

    for device_id, device_decisions in by_device.items():
        if len(device_decisions) < 2:
            # No conflict — pass through
            filtered.extend(device_decisions)
            continue

        # Check if actions actually conflict (different actions on same device)
        actions = {d.transaction.action for d in device_decisions}
        if len(actions) < 2:
            # Same action from multiple agents — no real conflict
            filtered.extend(device_decisions)
            continue

        # Read arbitration_mode from live preferences
        arb_mode = "ai"
        if state.preferences:
            arb_mode = state.preferences.get("arbitration_mode", "ai")

        # Mode: ask_me — skip auto-arbitration, keep all decisions
        if arb_mode == "ask_me":
            filtered.extend(device_decisions)
            emit_agent_event(
                "arbitration_deferred",
                agent_id="arbitration-agent-010",
                device=device_id,
                conflicting_agents=[
                    d.transaction.agent_id for d in device_decisions
                ],
                reason="ask_me mode: deferred to user",
            )
            continue

        # Mode: priority — skip LLM, use priority comparison only
        if arb_mode == "priority":
            sorted_decs = sorted(
                device_decisions,
                key=lambda d: priorities.get(d.transaction.agent_id, 0.0),
                reverse=True,
            )
            winner = sorted_decs[0]
            losers = sorted_decs[1:]
            filtered.append(winner)
            arb_count += 1

            emit_agent_event(
                "arbitration_resolved",
                agent_id="arbitration-agent-010",
                device=device_id,
                winner_agent=winner.transaction.agent_id,
                winner_action=winner.transaction.action,
                loser_agents=[d.transaction.agent_id for d in losers],
                method="priority_only",
                reasoning="Priority-based resolution (arbitration_mode=priority)",
                confidence=0.6,
            )
            logger.info(
                f"Priority arbitration on {device_id}: "
                f"{winner.transaction.agent_id} wins"
            )
            continue

        # Mode: ai (default) — full arbitration pipeline
        try:
            result = await asyncio.to_thread(
                state.arb_agent.arbitrate,
                device_decisions,
                telemetry,
                priorities,
            )
            arb_count += 1

            # Keep winner, discard losers
            filtered.append(result.winner)

            loser_agents = [
                d.transaction.agent_id for d in result.losers
            ]
            emit_agent_event(
                "arbitration_resolved",
                agent_id="arbitration-agent-010",
                device=device_id,
                winner_agent=result.winner.transaction.agent_id,
                winner_action=result.winner.transaction.action,
                loser_agents=loser_agents,
                method=result.method,
                reasoning=result.reasoning[:200],
                confidence=round(result.confidence, 2),
            )
            logger.info(
                f"Arbitration on {device_id}: "
                f"{result.winner.transaction.agent_id} wins "
                f"({result.method}, conf={result.confidence:.2f})"
            )
        except Exception as e:
            # Fallback: keep all decisions, let blockchain priority resolve
            logger.warning(f"Arbitration failed for {device_id}: {e}")
            filtered.extend(device_decisions)

    return filtered, arb_count


async def agent_cycle_loop() -> None:
    """Run full agent + mining + anchoring cycle every 20 seconds."""
    state = get_app_state()
    cycle_count = 0
    CYCLE_INTERVAL = 20

    logger.info(f"Agent cycle loop started (interval={CYCLE_INTERVAL}s)")

    # Short initial wait to let telemetry accumulate
    await asyncio.sleep(5.0)

    while state.simulation_active:
        try:
            cycle_count += 1
            await _run_single_agent_cycle(state, f"#{cycle_count}")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning(f"Agent cycle #{cycle_count} error: {e}")

        await asyncio.sleep(CYCLE_INTERVAL)

    logger.info(f"Agent cycle loop stopped after {cycle_count} cycles")


# ---------------------------------------------------------------------------
# Coordination: start / stop orchestration
# ---------------------------------------------------------------------------

async def start_orchestration() -> dict[str, Any]:
    """Start all orchestration background tasks.

    Called from POST /api/simulation/start after S5-HES simulation begins.
    Returns status dict for the response.
    """
    global _telemetry_bridge_task, _agent_cycle_task

    result: dict[str, Any] = {
        "session_auto_created": False,
        "orchestration_started": False,
    }

    # Auto-create session if needed
    state = get_app_state()
    was_active = state.is_active
    created = await auto_ensure_session()

    if not created:
        logger.warning("Orchestration skipped — no session available")
        return result

    result["session_auto_created"] = not was_active
    result["session_name"] = get_app_state().session_name

    # Verify HES-agent is available (required for telemetry)
    state = get_app_state()
    if not state.s5_hes_client or not state.s5_hes_available:
        logger.error(
            "Orchestration requires S5-HES-Agent for telemetry. "
            "Simulation cannot proceed without it."
        )
        result["orchestration_started"] = False
        result["error"] = "S5-HES-Agent not available"
        return result

    # Start telemetry bridge
    _telemetry_bridge_task = asyncio.create_task(telemetry_bridge_loop())

    # Start agent cycle loop
    _agent_cycle_task = asyncio.create_task(agent_cycle_loop())

    # Reset anomaly accumulation counter for fresh simulation
    global _anomaly_accumulations
    _anomaly_accumulations = 0

    result["orchestration_started"] = True
    logger.info("Orchestration started (telemetry bridge + agent cycles)")
    return result


async def stop_orchestration() -> None:
    """Stop all orchestration background tasks.

    Called from POST /api/simulation/stop and on natural simulation end.
    Runs one final agent cycle to process remaining telemetry, then auto-saves.
    """
    global _telemetry_bridge_task, _agent_cycle_task

    # Cancel background loops (they check simulation_active, but cancel to be safe)
    for task in (_telemetry_bridge_task, _agent_cycle_task):
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    _telemetry_bridge_task = None
    _agent_cycle_task = None

    # Run one final agent cycle to process any remaining telemetry
    state = get_app_state()
    if state.is_active and state.mcp and state.chain and state.agents:
        try:
            logger.info("Running final agent cycle before shutdown")
            await _run_single_agent_cycle(state, "final")
        except Exception as e:
            logger.warning(f"Final agent cycle failed: {e}")

    # Auto-save session
    if state.is_active:
        try:
            from web.core.bridge import save_current_session
            await asyncio.to_thread(save_current_session)
            logger.info(f"Session '{state.session_name}' auto-saved after simulation")
        except Exception as e:
            logger.warning(f"Auto-save after simulation failed: {e}")

    logger.info("Orchestration stopped")
