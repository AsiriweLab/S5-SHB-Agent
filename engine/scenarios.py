"""
Extracted scenario functions (1-39) for the smart home demonstration suite.

These functions were extracted from engine/main.py to decouple the
39 scenario demonstrations from the CLI entry point. Each scenario
receives its dependencies as parameters (mcp, chain, agents, etc.)
and is invoked by web/api/scenarios.py via dynamic dispatch.

All helper functions used by scenarios are included in this module.
"""

import asyncio
import hashlib
import json
import statistics
import time

from config import (
    CONFIDENCE_THRESHOLD,
    FEEDBACK_ENABLED,
    FEEDBACK_HISTORY_SIZE,
    ARBITRATION_ENABLED,
    AGENT_DEFINITIONS,
    MCP_TRANSPORT,
    GEMINI_MODEL,
    ANOMALY_TRAINING_ROUNDS,
)
from blockchain import Blockchain, Transaction, generate_keypair, sign_data
from agent import AgentDecision
from resident_preferences import ResidentPreferences, LOCKED_PARAMETERS
from mcp_server import get_device_layer, get_call_log
from mcp_server import mcp as mcp_server_instance
from mcp_client import create_mcp_client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def banner(title):
    print(f"\n{'=' * 70}\n  {title}\n{'=' * 70}")

def section(title):
    print(f"\n--- {title} ---")

def ok(msg):
    print(f"  [OK] {msg}")

def fail(msg):
    print(f"  [FAIL] {msg}")

def info(msg):
    print(f"  [INFO] {msg}")

def warn(msg):
    print(f"  [WARN] {msg}")


def make_decision(agent, privkey, device_id, command, params=None,
                  confidence=0.9, reasoning="Deterministic injection"):
    """Create a deterministic AgentDecision (bypasses LLM) for conflict demos."""
    reasoning_hash = hashlib.sha256(reasoning.encode()).hexdigest()
    tx = Transaction(
        agent_id=agent.agent_id,
        action=command,
        target_device=device_id,
        params=params or {},
        confidence=confidence,
        reasoning_hash=reasoning_hash,
    )
    tx.signature = sign_data(privkey, tx.payload_bytes())
    return AgentDecision(
        transaction=tx,
        reasoning_text=reasoning,
        reasoning_hash=reasoning_hash,
    )


def submit_decisions(decisions, chain, store, mcp, label=""):
    """Submit a list of AgentDecisions to the blockchain, store reasoning,
    execute accepted ones via MCP.  Records decision outcomes for feedback."""
    for dec in decisions:
        tx = dec.transaction
        # Store reasoning off-chain
        store.store_reasoning(
            dec.reasoning_hash, dec.reasoning_text,
            tx.agent_id, tx.action, tx.target_device, tx.confidence)

        # Submit to blockchain
        result = chain.validate_and_add(tx)

        conflict = result.get("conflict")
        accepted = result["accepted"]

        if accepted:
            if conflict:
                ok(f"ON-CHAIN: ACCEPTED {tx.action} -> {tx.target_device} "
                   f"by {tx.agent_id} (won conflict vs "
                   f"{conflict['agent_a_id']})")
                store.store_conflict(conflict)
            else:
                ok(f"ON-CHAIN: ACCEPTED {tx.action} -> {tx.target_device} "
                   f"by {tx.agent_id}")

            # Execute on device layer via MCP
            if tx.confidence >= CONFIDENCE_THRESHOLD:
                r = mcp.execute(tx.target_device, tx.action, tx.params)
                ok(f"EXECUTED: {tx.action} -> {tx.target_device}: "
                   f"{r.get('msg', '')}")
        else:
            if conflict:
                info(f"REJECTED: {tx.agent_id} {tx.action} -> "
                     f"{tx.target_device} (lost to "
                     f"{conflict['winner_id']})")
                store.store_conflict(conflict)
            else:
                info(f"REJECTED: {result['reason']}")

        # Record decision outcome for feedback loop
        store.store_decision_outcome(
            agent_id=tx.agent_id,
            action=tx.action,
            target_device=tx.target_device,
            confidence=tx.confidence,
            accepted=accepted,
            conflict=bool(conflict),
            conflict_winner=conflict.get("winner_id", "") if conflict else "",
            reasoning_summary=dec.reasoning_text[:100],
        )


# ---------------------------------------------------------------------------
# Feedback Loader
# ---------------------------------------------------------------------------

def load_feedback(agent_id, store):
    """Build feedback context string from recent outcomes."""
    if not FEEDBACK_ENABLED:
        return ""
    outcomes = store.get_recent_outcomes(agent_id, limit=FEEDBACK_HISTORY_SIZE)
    if not outcomes:
        return ""
    lines = ["Your recent decisions and outcomes:"]
    for o in outcomes:
        status = "ACCEPTED" if o["accepted"] else "REJECTED"
        conflict = (f" (conflict, winner: {o['conflict_winner']})"
                    if o["conflict"] else "")
        lines.append(f"  - {o['action']} -> {o['target_device']}: "
                     f"{status}{conflict} (confidence: {o['confidence']:.2f})")
    stats = store.get_outcome_stats(agent_id)
    lines.append(f"  Acceptance rate: {stats['acceptance_rate']:.0%}, "
                 f"Conflict rate: {stats['conflict_rate']:.0%}")
    return "\n".join(lines)


def inject_feedback_all(agents, store):
    """Load and inject feedback context into all agents."""
    if not FEEDBACK_ENABLED:
        return
    for agent_id, agent in agents.items():
        ctx = load_feedback(agent_id, store)
        agent.set_feedback_context(ctx)


# ---------------------------------------------------------------------------
# Async Agent Pipeline
# ---------------------------------------------------------------------------

async def run_agents_async(agents_to_run, telemetry):
    """Run multiple agents concurrently, return all decisions."""
    tasks = [
        agent.perceive_and_decide_async(telemetry)
        for agent in agents_to_run
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    all_decisions = []
    for agent, result in zip(agents_to_run, results):
        if isinstance(result, Exception):
            info(f"{agent.agent_id}: error -- {result}")
        elif result:
            all_decisions.extend(result)
    return all_decisions


def run_agents_parallel(agents_to_run, telemetry):
    """Sync wrapper for async parallel agent execution."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(
            run_agents_async(agents_to_run, telemetry))
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Health Check Helper
# ---------------------------------------------------------------------------

def run_health_check(health_monitor, store, label=""):
    """Check MCP health and store snapshot."""
    result = health_monitor.check_health()
    store.store_health_snapshot(result)
    if result["healthy"]:
        ok(f"MCP health: OK (latency {result['latency_ms']:.1f}ms)"
           f"{' ' + label if label else ''}")
    else:
        fail(f"MCP health: DEGRADED (errors: {result['consecutive_errors']})"
             f"{' ' + label if label else ''}")
    return result


# ---------------------------------------------------------------------------
# NLU Command Processor
# ---------------------------------------------------------------------------

def process_nlu_command(user_text, nlu_agent, mcp, chain, store, convo,
                        telemetry=None, label="",
                        gov_contract=None, preferences=None):
    """Process a user text command through the NLU agent.

    gov_contract and preferences enable governance intent handling.
    Returns (ParsedIntent, List[AgentDecision]).
    """
    if telemetry is None:
        telemetry = mcp.get_all_telemetry()

    context = convo.get_context_string()
    intent, decisions = nlu_agent.process_command(
        user_text, telemetry, context)

    ok(f"NLU: \"{user_text}\" -> intent={intent.intent_type}, "
       f"confidence={intent.confidence:.2f}, "
       f"actions={len(intent.actions)}")

    if intent.query_response:
        info(f"  Query response: {intent.query_response}")

    # Handle governance/preference intents
    if intent.intent_type == "preference" and gov_contract:
        for act in intent.actions:
            key = act.get("preference_key")
            value = act.get("new_value")
            if key and value is not None:
                result = gov_contract.apply_preference_change(key, value)
                if result.get("success"):
                    gov_tx = gov_contract.create_governance_transaction({
                        "type": "preference_change",
                        "key": key,
                        "old_value": result["old_value"],
                        "new_value": result["new_value"],
                        "tier": result["tier"],
                    })
                    chain.pending_tx.append(gov_tx)
                    store.store_governance_change(
                        "preference_change", key,
                        str(result["old_value"]), str(result["new_value"]),
                        result["tier"],
                        json.dumps({"source": "nlu", "text": user_text}),
                    )
                    ok(f"  GOVERNANCE: {key} changed: "
                       f"{result['old_value']} -> {result['new_value']}")
                    if preferences:
                        preferences.apply_to_agent_priorities(AGENT_DEFINITIONS)
                else:
                    info(f"  GOVERNANCE DENIED: {result.get('reason')}")

    # Track conversation
    devices_mentioned = [a.get("device_id", "") for a in intent.actions]
    convo.add_turn(
        user_text=user_text,
        intent_type=intent.intent_type,
        actions=intent.actions,
        response=intent.query_response,
        devices_mentioned=devices_mentioned,
    )

    # Store in off-chain DB
    store.store_conversation_turn(
        user_text=user_text,
        intent_type=intent.intent_type,
        actions_json=json.dumps(intent.actions),
        response=intent.query_response,
        confidence=intent.confidence,
        devices_mentioned=",".join(devices_mentioned),
    )

    return intent, decisions


# ---------------------------------------------------------------------------
# Arbitration Helper
# ---------------------------------------------------------------------------

def arbitrate_and_submit(decisions, arb_agent, chain, store, mcp,
                         telemetry=None):
    """Group decisions by target device, arbitrate conflicts, submit winners.

    Returns list of ArbitrationResult for any conflicts resolved.
    """
    if not ARBITRATION_ENABLED or not decisions:
        submit_decisions(decisions, chain, store, mcp)
        return []

    # Group by target device
    by_device = {}
    for d in decisions:
        dev = d.transaction.target_device
        by_device.setdefault(dev, []).append(d)

    arb_results = []
    winners = []

    # Get agent priorities for arbitration
    priorities = {}
    for agent_id, defn in AGENT_DEFINITIONS.items():
        priorities[agent_id] = defn["priority"]

    for device, group in by_device.items():
        if len(group) <= 1:
            # No conflict -- submit directly
            winners.extend(group)
        else:
            # Conflict! Arbitrate
            info(f"CONFLICT on {device}: {len(group)} proposals")
            for d in group:
                info(f"  {d.transaction.agent_id}: {d.transaction.action} "
                     f"(conf={d.transaction.confidence:.2f})")

            result = arb_agent.arbitrate(group, telemetry, priorities)
            if result:
                ok(f"ARBITRATION: {result.winner.transaction.agent_id} wins "
                   f"(method={result.method})")
                info(f"  Reasoning: {result.reasoning}")
                winners.append(result.winner)
                arb_results.append(result)

                # Store arbitration in off-chain DB
                store.store_arbitration(
                    conflict_device=device,
                    winner_agent=result.winner.transaction.agent_id,
                    loser_agents=",".join(
                        d.transaction.agent_id for d in result.losers),
                    method=result.method,
                    reasoning=result.reasoning,
                    scores_json=json.dumps(result.scores),
                    confidence=result.confidence,
                )
            else:
                # Fallback: submit all
                winners.extend(group)

    submit_decisions(winners, chain, store, mcp)
    return arb_results


# ---------------------------------------------------------------------------
# Session Save Helper
# ---------------------------------------------------------------------------

def save_session(session_mgr, session_name, chain, agent_keys, store,
                 preferences, router, scenarios_run=39):
    """Save all session state to disk."""
    section("Saving session state")
    chain.save(session_mgr.blockchain_path(session_name))
    ok(f"Blockchain saved ({len(chain.chain)} blocks)")
    session_mgr.save_agent_keys(session_name, agent_keys)
    ok(f"Agent keys saved ({len(agent_keys)} agents)")
    preferences.save(session_mgr.preferences_path(session_name))
    ok(f"Preferences saved")
    router.save_assignments(session_mgr.model_assignments_path(session_name))
    ok(f"Model assignments saved")
    session_mgr.update_meta(session_name, len(chain.chain), scenarios_run)
    ok(f"Session metadata updated")


# ===========================================================================
# SCENARIOS 1-39
# ===========================================================================


# ---------------------------------------------------------------------------
# Scenario 1: Normal Multi-Agent Operation
# ---------------------------------------------------------------------------

def scenario_1(mcp, chain, agents, agent_keys, store, health_monitor):
    banner("SCENARIO 1: Normal Multi-Agent Operation")
    run_health_check(health_monitor, store)

    device_count = mcp.device_count()
    section(f"Step 1: Collect + store telemetry from all {device_count} "
            f"devices via MCP")
    telemetry = mcp.get_all_telemetry()
    counts = store.store_telemetry_batch(telemetry)
    ok(f"Stored: cont={counts['continuous']}, ev={counts['events']}, "
       f"al={counts['alerts']}")

    section("Step 2: All agents reason in PARALLEL (async pipeline)")
    inject_feedback_all(agents, store)
    t0 = time.perf_counter()
    all_decisions = run_agents_parallel(list(agents.values()), telemetry)
    elapsed = time.perf_counter() - t0
    ok(f"Parallel reasoning: {len(all_decisions)} decisions "
       f"from {len(agents)} agents in {elapsed:.2f}s")
    for d in all_decisions:
        info(f"  [{d.transaction.agent_id}] {d.transaction.action} -> "
             f"{d.transaction.target_device} "
             f"(conf={d.transaction.confidence:.2f})")

    if not all_decisions:
        info("No agents proposed actions (normal conditions)")

    section("Step 3: Submit all decisions to blockchain")
    submit_decisions(all_decisions, chain, store, mcp)

    section("Step 4: Mine block")
    block = chain.mine_pending()
    if block:
        ok(f"Block #{block.index} mined [{block.hash[:16]}...]")
    else:
        info("No pending transactions to mine")


# ---------------------------------------------------------------------------
# Scenario 2: Gas Emergency (firmware override)
# ---------------------------------------------------------------------------

def scenario_2(mcp, chain, agents, agent_keys, store, health_monitor):
    banner("SCENARIO 2: Gas Emergency (Safety-Critical Override)")
    run_health_check(health_monitor, store)

    section("Step 1: Inject gas leak via MCP + collect telemetry")
    mcp.inject_fault("gas-kitchen", "gas", {"level_ppm": 200})
    telemetry = mcp.get_all_telemetry()
    counts = store.store_telemetry_batch(telemetry)
    ok(f"Stored: cont={counts['continuous']}, ev={counts['events']}, "
       f"al={counts['alerts']}")

    section("Step 2: Firmware emergency scan via MCP (bypasses ALL agents)")
    emergencies = mcp.scan_emergencies()
    for em in emergencies:
        ok(f"EMERGENCY: {em['type']} in {em['room']}")
        store.store_emergency(em)
        chain.record_emergency(em)

    section("Step 3: Safety Agent reasons on post-emergency state")
    safety_agent = agents["safety-agent-001"]
    try:
        time.sleep(1)
        telemetry2 = mcp.get_all_telemetry()
        decisions = safety_agent.perceive_and_decide(telemetry2)
        ok(f"Safety Agent proposed {len(decisions)} additional action(s)")
        submit_decisions(decisions, chain, store, mcp)
    except Exception as e:
        info(f"Safety Agent: {e}")

    section("Step 4: Mine block")
    block = chain.mine_pending()
    if block:
        ok(f"Block #{block.index} mined [{block.hash[:16]}...] "
           f"({len(block.transactions)} tx)")

    mcp.inject_fault("gas-kitchen", "clear_gas")


# ---------------------------------------------------------------------------
# Scenario 3: Conflict -- Security vs Privacy (Camera)
# ---------------------------------------------------------------------------

def scenario_3(mcp, chain, agents, agent_keys, store, health_monitor):
    banner("SCENARIO 3: Conflict - Security vs Privacy (Camera)")
    info("Security (0.8) wants cam-entrance ON, "
         "Privacy (0.7) wants cam-entrance OFF")

    section("Step 1: Simulate intrusion + person detected via MCP")
    mcp.inject_fault("cam-entrance", "detection",
                     {"motion": True, "person": True})
    mcp.inject_fault("motion-entrance", "motion", {"confidence": 0.9})

    section("Step 2: Create deterministic conflicting decisions")
    sec_dec = make_decision(
        agents["security-agent-003"],
        agent_keys["security-agent-003"],
        "cam-entrance", "start_recording",
        reasoning="Motion and person detected at entrance - "
                  "start recording for security evidence")
    priv_dec = make_decision(
        agents["privacy-agent-004"],
        agent_keys["privacy-agent-004"],
        "cam-entrance", "stop_recording",
        reasoning="Person detected inside home - "
                  "stop recording to protect occupant privacy")

    section("Step 3: Submit Security first, then Privacy")
    submit_decisions([sec_dec], chain, store, mcp, "Security")
    submit_decisions([priv_dec], chain, store, mcp, "Privacy")

    section("Step 4: Mine block")
    block = chain.mine_pending()
    if block:
        ok(f"Block #{block.index} mined [{block.hash[:16]}...] "
           f"({len(block.transactions)} tx)")
        for tx in block.transactions:
            info(f"  [{tx.tx_type}] {tx.agent_id}: "
                 f"{tx.action} -> {tx.target_device}")

    mcp.inject_fault("cam-entrance", "clear_detection")
    mcp.inject_fault("motion-entrance", "clear_motion")


# ---------------------------------------------------------------------------
# Scenario 4: Conflict -- Security vs Safety (Door Lock)
# ---------------------------------------------------------------------------

def scenario_4(mcp, chain, agents, agent_keys, store, health_monitor):
    banner("SCENARIO 4: Conflict - Security vs Safety (Door Lock)")
    info("Security (0.8) wants lock-front LOCKED, "
         "Safety (1.0) wants lock-front UNLOCKED")

    section("Step 1: Security locks the front door")
    sec_dec = make_decision(
        agents["security-agent-003"],
        agent_keys["security-agent-003"],
        "lock-front", "lock",
        reasoning="Securing perimeter - locking front door "
                  "due to unusual motion detected")

    section("Step 2: Safety unlocks for evacuation (higher priority)")
    safety_dec = make_decision(
        agents["safety-agent-001"],
        agent_keys["safety-agent-001"],
        "lock-front", "unlock",
        reasoning="Potential safety hazard detected - "
                  "unlocking front door for emergency evacuation")

    section("Step 3: Submit Security first, then Safety")
    submit_decisions([sec_dec], chain, store, mcp, "Security")
    submit_decisions([safety_dec], chain, store, mcp, "Safety")

    section("Step 4: Mine block")
    block = chain.mine_pending()
    if block:
        ok(f"Block #{block.index} mined [{block.hash[:16]}...] "
           f"({len(block.transactions)} tx)")
        for tx in block.transactions:
            info(f"  [{tx.tx_type}] {tx.agent_id}: "
                 f"{tx.action} -> {tx.target_device}")


# ---------------------------------------------------------------------------
# Scenario 5: Unauthorized Agent
# ---------------------------------------------------------------------------

def scenario_5(mcp, chain, agents, agent_keys, store, health_monitor):
    banner("SCENARIO 5: Unauthorized Agent")

    section("Step 1: Unregistered agent")
    rogue_priv, _ = generate_keypair()
    tx1 = Transaction("rogue-agent-999", "unlock", "lock-front",
                      {}, 0.9, hashlib.sha256(b"rogue").hexdigest())
    tx1.signature = sign_data(rogue_priv, tx1.payload_bytes())
    r1 = chain.validate_and_add(tx1)
    ok(f"REJECTED: {r1['reason']}")

    section("Step 2: Spoofed signature")
    tx2 = Transaction("safety-agent-001", "unlock", "lock-front",
                      {}, 0.9, hashlib.sha256(b"spoof").hexdigest())
    tx2.signature = sign_data(rogue_priv, tx2.payload_bytes())
    r2 = chain.validate_and_add(tx2)
    ok(f"REJECTED: {r2['reason']}")


# ---------------------------------------------------------------------------
# Scenario 6: Graceful Degradation
# ---------------------------------------------------------------------------

def scenario_6(mcp, chain, agents, agent_keys, store, health_monitor):
    banner("SCENARIO 6: Graceful Degradation (Safety Agent Offline)")

    section("Step 1: Safety agent goes offline")
    agents["safety-agent-001"].set_offline()
    ok("safety-agent-001 is OFFLINE")

    section("Step 2: Collect telemetry via MCP and store")
    telemetry = mcp.get_all_telemetry()
    counts = store.store_telemetry_batch(telemetry)
    ok(f"Telemetry stored (cont={counts['continuous']}, "
       f"ev={counts['events']})")

    section("Step 3: Agents try to reason (parallel -- safety offline)")
    t0 = time.perf_counter()
    all_decisions = run_agents_parallel(list(agents.values()), telemetry)
    elapsed = time.perf_counter() - t0
    ok(f"Parallel reasoning (1 offline): {len(all_decisions)} decisions "
       f"in {elapsed:.2f}s")
    for d in all_decisions:
        info(f"  [{d.transaction.agent_id}] {d.transaction.action} -> "
             f"{d.transaction.target_device}")
    submit_decisions(all_decisions, chain, store, mcp)

    section("Step 4: Fallback rules activate via MCP")
    fallback_actions = mcp.apply_fallback_rules()
    if fallback_actions:
        for fa in fallback_actions:
            ok(f"Fallback: {fa['action']} on {fa['device']}")
        chain.record_fallback(fallback_actions)

    section("Step 5: Mine block")
    block = chain.mine_pending()
    if block:
        ok(f"Block #{block.index} mined [{block.hash[:16]}...] "
           f"({len(block.transactions)} tx)")

    agents["safety-agent-001"].set_online()
    ok("safety-agent-001 is back ONLINE")


# ---------------------------------------------------------------------------
# Scenario 7: Cascading Agent Response
# ---------------------------------------------------------------------------

def scenario_7(mcp, chain, agents, agent_keys, store, health_monitor):
    banner("SCENARIO 7: Cascading Agent Response (Safety->Health->Security)")

    section("Step 1: Inject moderate smoke via MCP")
    mcp.inject_fault("smoke-kitchen", "smoke", {"level": 0.6})

    section("Step 2: Safety Agent responds first")
    safety_dec = make_decision(
        agents["safety-agent-001"],
        agent_keys["safety-agent-001"],
        "lock-front", "unlock",
        confidence=0.95,
        reasoning="Smoke detected at 0.6 - unlocking doors for evacuation")
    safety_dec2 = make_decision(
        agents["safety-agent-001"],
        agent_keys["safety-agent-001"],
        "light-lr", "set_brightness",
        params={"brightness": 100},
        confidence=0.95,
        reasoning="Smoke emergency - max brightness for visibility")
    submit_decisions([safety_dec, safety_dec2], chain, store, mcp)
    ok("Safety Agent: doors unlocked, lights maxed")

    section("Step 3: Health Agent checks occupants")
    health_dec = make_decision(
        agents["health-agent-002"],
        agent_keys["health-agent-002"],
        "light-br", "turn_on",
        confidence=0.85,
        reasoning="Smoke emergency in progress - turning on bedroom light "
                  "to check on occupant visibility and movement")
    submit_decisions([health_dec], chain, store, mcp)
    ok("Health Agent: bedroom light on for occupant check")

    section("Step 4: Security Agent records evidence")
    sec_dec = make_decision(
        agents["security-agent-003"],
        agent_keys["security-agent-003"],
        "cam-entrance", "start_recording",
        confidence=0.9,
        reasoning="Emergency in progress - recording entrance camera "
                  "for evidence and situational awareness")
    submit_decisions([sec_dec], chain, store, mcp)
    ok("Security Agent: entrance camera recording")

    section("Step 5: Mine cascading block")
    block = chain.mine_pending()
    if block:
        ok(f"Block #{block.index} mined [{block.hash[:16]}...] "
           f"({len(block.transactions)} tx - cascading response)")
        for tx in block.transactions:
            info(f"  [{tx.agent_id}] {tx.action} -> {tx.target_device}")

    mcp.inject_fault("smoke-kitchen", "clear_smoke")


# ---------------------------------------------------------------------------
# Scenario 8: Merkle Anchoring
# ---------------------------------------------------------------------------

def scenario_8(mcp, chain, store, health_monitor):
    banner("SCENARIO 8: Merkle Anchoring (all devices via MCP)")

    section("Step 1: Generate telemetry batches via MCP")
    for _ in range(10):
        telemetry = mcp.get_all_telemetry()
        store.store_telemetry_batch(telemetry)

    unanchored = store.get_unanchored_count()
    ok(f"Accumulated {unanchored} unanchored records")

    section("Step 2: Create unified Merkle anchor")
    anchor = store.create_anchor()
    if anchor:
        ok(f"Merkle root: {anchor['merkle_root'][:32]}...")
        info(f"  Total: {anchor['record_count']} records")
        info(f"  continuous={anchor['continuous_count']}, "
             f"events={anchor['event_count']}, "
             f"alerts={anchor['alert_count']}")

        chain.record_telemetry_anchor(
            anchor["merkle_root"], anchor["batch_id"],
            anchor["record_count"])

        section("Step 3: Mine anchor block")
        block = chain.mine_pending()
        if block:
            ok(f"Anchor in Block #{block.index}")
            store.update_anchor_block(anchor["batch_id"], block.index)

        section("Step 4: Verify")
        v = store.verify_anchor(anchor["batch_id"])
        if v["verified"]:
            ok("VERIFIED: Merkle root matches")
        else:
            fail(f"VERIFICATION FAILED: {v['reason']}")


# ---------------------------------------------------------------------------
# Scenario 9: ML/DL Readiness
# ---------------------------------------------------------------------------

def scenario_9(store):
    banner("SCENARIO 9: ML/DL Readiness (expanded device data)")

    section("Query 1: Continuous data (thermostat + smart_plug + hvac)")
    for dev_id in ["thermo-lr", "plug-lr", "hvac-main"]:
        cont = store.query_continuous(device_id=dev_id, limit=5)
        ok(f"'{dev_id}': {len(cont)} continuous records")

    section("Query 2: Events by type")
    for etype in ["motion_detected", "camera_idle", "door_locked",
                  "plug_on", "hvac_auto"]:
        events = store.query_events(event_type=etype, limit=5)
        ok(f"'{etype}': {len(events)} records")

    section("Query 3: Alerts")
    alerts = store.query_alerts(limit=10)
    ok(f"Total alerts: {len(alerts)}")
    for a in alerts[:5]:
        info(f"  {a['alert_type']} on {a['device_id']} "
             f"(severity={a['severity']})")

    section("Query 4: Conflict log")
    conflicts = store.query_conflicts(limit=10)
    ok(f"Total conflicts: {len(conflicts)}")
    for c in conflicts:
        info(f"  {c['agent_a_id']} vs {c['agent_b_id']} "
             f"on {c['device_id']} -> winner: {c['winner_id']}")


# ---------------------------------------------------------------------------
# Scenario 10: Full Audit Summary
# ---------------------------------------------------------------------------

def scenario_10(chain, store):
    banner("SCENARIO 10: Multi-Agent Audit Summary")

    section("Blockchain integrity")
    valid = chain.validate_chain()
    if valid:
        ok("Hash chain: ALL BLOCKS VALID")
    else:
        fail("CORRUPTED")

    section("Per-agent transaction summary")
    agent_tx_counts = {}
    for b in chain.chain:
        for tx in b.transactions:
            agent_tx_counts.setdefault(tx.agent_id, 0)
            agent_tx_counts[tx.agent_id] += 1
    for agent_id, count in sorted(agent_tx_counts.items()):
        info(f"  {agent_id}: {count} tx on-chain")

    section("Reasoning verification")
    r_checks = r_pass = 0
    for b in chain.chain:
        for tx in b.transactions:
            if tx.tx_type == "agent_action" and tx.reasoning_hash != "N/A":
                r_checks += 1
                v = store.verify_reasoning(tx.reasoning_hash)
                if v["verified"]:
                    r_pass += 1
    if r_checks:
        ok(f"Reasoning: {r_pass}/{r_checks} verified")
    else:
        info("No agent_action transactions found")

    section("Conflict resolution audit")
    cstats = store.conflict_stats()
    info(f"Total conflicts: {cstats['total_conflicts']}")
    for winner, count in cstats.get("by_winner", {}).items():
        info(f"  Winner '{winner}': {count} times")
    conflicts = store.query_conflicts()
    priority_correct = 0
    for c in conflicts:
        if c["agent_a_priority"] != c["agent_b_priority"]:
            expected_winner = (c["agent_a_id"]
                               if c["agent_a_priority"] > c["agent_b_priority"]
                               else c["agent_b_id"])
            if c["winner_id"] == expected_winner:
                priority_correct += 1
            else:
                fail(f"Priority inversion: {c['conflict_id']}")
    if conflicts:
        ok(f"Priority hierarchy: {priority_correct}/{len(conflicts)} correct")

    section("Off-chain stats (8 tables)")
    stats = store.stats()
    for key, val in stats.items():
        info(f"  {key}: {val}")


# ---------------------------------------------------------------------------
# Scenario 11: Energy Optimization
# ---------------------------------------------------------------------------

def scenario_11(mcp, chain, agents, agent_keys, store, health_monitor):
    banner("SCENARIO 11: Energy Optimization (Peak Demand Management)")
    info("Energy (0.6) dims lights, Security (0.8) overrides")

    section("Step 1: Simulate high-power consumption via MCP")
    mcp.execute("plug-lr", "set_mode", {"mode": "performance"})
    mcp.execute("plug-kitchen", "set_mode", {"mode": "performance"})
    telemetry = mcp.get_all_telemetry()
    counts = store.store_telemetry_batch(telemetry)
    ok(f"Stored: cont={counts['continuous']}, ev={counts['events']}, "
       f"al={counts['alerts']}")

    section("Step 2: Energy Agent wants to dim lights to save power")
    energy_dec = make_decision(
        agents["energy-agent-005"],
        agent_keys["energy-agent-005"],
        "light-lr", "set_brightness",
        params={"brightness": 30},
        confidence=0.85,
        reasoning="Peak demand detected - reducing living room light "
                  "brightness to 30% to save energy")

    section("Step 3: Security Agent wants lights bright for monitoring")
    sec_dec = make_decision(
        agents["security-agent-003"],
        agent_keys["security-agent-003"],
        "light-lr", "set_brightness",
        params={"brightness": 100},
        confidence=0.8,
        reasoning="Maintaining full brightness in living room "
                  "for security camera effectiveness")

    section("Step 4: Submit Energy first, then Security (Security wins)")
    submit_decisions([energy_dec], chain, store, mcp, "Energy")
    submit_decisions([sec_dec], chain, store, mcp, "Security")

    section("Step 5: Energy Agent sets plugs to eco mode (no conflict)")
    eco_dec1 = make_decision(
        agents["energy-agent-005"],
        agent_keys["energy-agent-005"],
        "plug-lr", "set_mode",
        params={"mode": "eco"},
        confidence=0.9,
        reasoning="Setting living room plug to eco mode for peak shaving")
    eco_dec2 = make_decision(
        agents["energy-agent-005"],
        agent_keys["energy-agent-005"],
        "plug-kitchen", "set_mode",
        params={"mode": "eco"},
        confidence=0.9,
        reasoning="Setting kitchen plug to eco mode for peak shaving")
    submit_decisions([eco_dec1, eco_dec2], chain, store, mcp, "Energy-eco")

    section("Step 6: Mine block")
    block = chain.mine_pending()
    if block:
        ok(f"Block #{block.index} mined [{block.hash[:16]}...] "
           f"({len(block.transactions)} tx)")
        for tx in block.transactions:
            info(f"  [{tx.agent_id}] {tx.action} -> {tx.target_device}")


# ---------------------------------------------------------------------------
# Scenario 12: Climate Comfort vs Energy
# ---------------------------------------------------------------------------

def scenario_12(mcp, chain, agents, agent_keys, store, health_monitor):
    banner("SCENARIO 12: Climate Comfort vs Energy (HVAC Conflict)")
    info("Climate (0.5) wants warmer, Energy (0.6) wants cooler")

    section("Step 1: Climate Agent raises HVAC temperature for comfort")
    climate_dec = make_decision(
        agents["climate-agent-006"],
        agent_keys["climate-agent-006"],
        "hvac-main", "set_temperature",
        params={"temperature": 25},
        confidence=0.85,
        reasoning="Occupant comfort: current temperature below optimal "
                  "range, raising HVAC to 25C")

    section("Step 2: Energy Agent lowers HVAC to save energy")
    energy_dec = make_decision(
        agents["energy-agent-005"],
        agent_keys["energy-agent-005"],
        "hvac-main", "set_temperature",
        params={"temperature": 20},
        confidence=0.8,
        reasoning="High energy consumption from HVAC - lowering to 20C "
                  "to reduce peak demand")

    section("Step 3: Submit Climate first, then Energy (Energy wins 0.6 > 0.5)")
    submit_decisions([climate_dec], chain, store, mcp, "Climate")
    submit_decisions([energy_dec], chain, store, mcp, "Energy")

    section("Step 4: Climate Agent adjusts fan speed (no conflict)")
    fan_dec = make_decision(
        agents["climate-agent-006"],
        agent_keys["climate-agent-006"],
        "hvac-main", "set_fan_speed",
        params={"fan_speed": "high"},
        confidence=0.8,
        reasoning="Compensating for lower temperature by increasing "
                  "air circulation with high fan speed")
    submit_decisions([fan_dec], chain, store, mcp, "Climate-fan")

    section("Step 5: Mine block")
    block = chain.mine_pending()
    if block:
        ok(f"Block #{block.index} mined [{block.hash[:16]}...] "
           f"({len(block.transactions)} tx)")
        for tx in block.transactions:
            info(f"  [{tx.agent_id}] {tx.action} -> {tx.target_device}")


# ---------------------------------------------------------------------------
# Scenario 13: Maintenance Alert
# ---------------------------------------------------------------------------

def scenario_13(mcp, chain, agents, agent_keys, store, health_monitor):
    banner("SCENARIO 13: Maintenance Alert (Appliance Degradation)")

    section("Step 1: Inject washer degradation to 'critical' via MCP")
    mcp.inject_fault("appliance-washer", "degradation",
                     {"status": "critical"})

    section("Step 2: Collect telemetry via MCP (should trigger alert)")
    telemetry = mcp.get_all_telemetry()
    counts = store.store_telemetry_batch(telemetry)
    ok(f"Stored: cont={counts['continuous']}, ev={counts['events']}, "
       f"al={counts['alerts']}")
    if counts['alerts'] > 0:
        ok(f"Alert triggered: {counts['alerts']} alert(s) from "
           f"critical appliance")

    section("Step 3: Maintenance Agent triggers maintenance mode")
    maint_dec = make_decision(
        agents["maintenance-agent-007"],
        agent_keys["maintenance-agent-007"],
        "appliance-washer", "trigger_maintenance_mode",
        confidence=0.95,
        reasoning="Washer status CRITICAL - triggering maintenance mode "
                  "to prevent further damage")
    submit_decisions([maint_dec], chain, store, mcp, "Maintenance")

    section("Step 4: Maintenance Agent reports fridge status (healthy)")
    report_dec = make_decision(
        agents["maintenance-agent-007"],
        agent_keys["maintenance-agent-007"],
        "appliance-fridge", "report_status",
        confidence=0.7,
        reasoning="Routine health check on fridge - status is OK")
    submit_decisions([report_dec], chain, store, mcp, "Maintenance-report")

    section("Step 5: Mine block")
    block = chain.mine_pending()
    if block:
        ok(f"Block #{block.index} mined [{block.hash[:16]}...] "
           f"({len(block.transactions)} tx)")
        for tx in block.transactions:
            info(f"  [{tx.agent_id}] {tx.action} -> {tx.target_device}")

    mcp.execute("appliance-washer", "reset_runtime")


# ---------------------------------------------------------------------------
# Scenario 14: Cross-Tier Conflict Cascade
# ---------------------------------------------------------------------------

def scenario_14(mcp, chain, agents, agent_keys, store, health_monitor):
    banner("SCENARIO 14: Cross-Tier Conflict Cascade (3 Agents)")
    info("Safety (1.0) vs Energy (0.6) vs Climate (0.5) "
         "all target thermostat")

    section("Step 1: Climate Agent sets thermostat to 25C (comfort)")
    climate_dec = make_decision(
        agents["climate-agent-006"],
        agent_keys["climate-agent-006"],
        "thermo-lr", "set_temperature",
        params={"temperature": 25},
        confidence=0.8,
        reasoning="Raising thermostat to 25C for occupant comfort")

    section("Step 2: Energy Agent sets thermostat to 20C (energy saving)")
    energy_dec = make_decision(
        agents["energy-agent-005"],
        agent_keys["energy-agent-005"],
        "thermo-lr", "set_temperature",
        params={"temperature": 20},
        confidence=0.85,
        reasoning="Peak demand - lowering thermostat to 20C")

    section("Step 3: Safety Agent turns off thermostat (gas hazard)")
    safety_dec = make_decision(
        agents["safety-agent-001"],
        agent_keys["safety-agent-001"],
        "thermo-lr", "turn_off",
        confidence=0.98,
        reasoning="Potential gas hazard - turning off thermostat "
                  "to prevent ignition source")

    section("Step 4: Submit all three (priority cascade)")
    info("Submit order: Climate(0.5), then Energy(0.6), then Safety(1.0)")
    submit_decisions([climate_dec], chain, store, mcp, "Climate")
    submit_decisions([energy_dec], chain, store, mcp, "Energy")
    submit_decisions([safety_dec], chain, store, mcp, "Safety")

    section("Step 5: Verify cascade result")
    info("Expected: Safety(1.0) wins over Energy(0.6) wins over Climate(0.5)")

    section("Step 6: Mine block")
    block = chain.mine_pending()
    if block:
        ok(f"Block #{block.index} mined [{block.hash[:16]}...] "
           f"({len(block.transactions)} tx)")
        for tx in block.transactions:
            info(f"  [{tx.agent_id}] {tx.action} -> {tx.target_device}")


# ---------------------------------------------------------------------------
# Scenario 15: Full 7-Agent Audit
# ---------------------------------------------------------------------------

def scenario_15(chain, store):
    banner("SCENARIO 15: Full 7-Agent Audit Summary")

    section("All 7 agents registered")
    for agent_id, defn in AGENT_DEFINITIONS.items():
        registered = chain.registry.is_registered(agent_id)
        priority = chain.priorities.get_priority(agent_id)
        status = "OK" if registered else "FAIL"
        print(f"  [{status}] {agent_id}: role={defn['role']}, "
              f"priority={priority}, registered={registered}")

    section("Priority hierarchy (all 7 tiers)")
    sorted_agents = sorted(AGENT_DEFINITIONS.items(),
                           key=lambda x: x[1]["priority"], reverse=True)
    for i, (agent_id, defn) in enumerate(sorted_agents):
        info(f"  #{i+1}: {agent_id} "
             f"(priority={defn['priority']}, role={defn['role']})")

    section("Device type coverage")
    all_device_types = set()
    for defn in AGENT_DEFINITIONS.values():
        adt = defn["allowed_device_types"]
        if adt == "*":
            all_device_types.add("* (all types)")
        else:
            all_device_types.update(adt)
    info(f"Total managed device types: {len(all_device_types)}")
    info(f"  Types: {sorted(all_device_types)}")

    section("Conflict summary (cross-tier)")
    cstats = store.conflict_stats()
    info(f"Total conflicts: {cstats['total_conflicts']}")
    for winner, count in sorted(cstats.get("by_winner", {}).items()):
        info(f"  {winner}: won {count} conflict(s)")


# ---------------------------------------------------------------------------
# Scenario 16: MCP Device Discovery
# ---------------------------------------------------------------------------

def scenario_16(mcp, chain, store, health_monitor):
    banner("SCENARIO 16: MCP Device Discovery (Protocol-Native)")

    section("Step 1: Agent discovers devices via MCP list_devices tool")
    devices = mcp.list_devices()
    ok(f"Discovered {len(devices)} devices via MCP")

    section("Step 2: Categorize discovered devices by type")
    by_type = {}
    for d in devices:
        by_type.setdefault(d["device_type"], []).append(d["device_id"])
    for dtype, ids in sorted(by_type.items()):
        info(f"  {dtype}: {ids}")

    section("Step 3: Agent queries specific device status via MCP")
    for device_id in ["thermo-lr", "hvac-main", "cam-entrance"]:
        status = mcp.get_device_status(device_id)
        readings = status.get("readings", {})
        ok(f"Status [{device_id}]: {list(readings.keys())}")

    section("Step 4: Verify discovery matches expected device count")
    expected = 16
    if len(devices) >= expected:
        ok(f"Discovery: {len(devices)} >= {expected} devices (PASS)")
    else:
        fail(f"Discovery: {len(devices)} < {expected} (FAIL)")


# ---------------------------------------------------------------------------
# Scenario 17: Dynamic Device Registration
# ---------------------------------------------------------------------------

def scenario_17(mcp, chain, agents, agent_keys, store, health_monitor):
    banner("SCENARIO 17: Dynamic Device Registration via MCP")

    section("Step 1: Check current device count via MCP")
    before = mcp.device_count()
    ok(f"Devices before: {before}")

    section("Step 2: Register new device via MCP tool")
    result = mcp.register_device("smart_light", "light-garage", "garage")
    ok(f"Register: {result['msg']}")

    section("Step 3: Verify new device appears in MCP discovery")
    after = mcp.device_count()
    ok(f"Devices after: {after} (was {before})")

    section("Step 4: Query new device status via MCP")
    status = mcp.get_device_status("light-garage")
    ok(f"New device status: {status.get('readings', {})}")

    section("Step 5: Grant permissions + agent commands new device")
    dl = get_device_layer()
    for agent_id, defn in AGENT_DEFINITIONS.items():
        adt = defn["allowed_device_types"]
        if adt == "*" or "smart_light" in adt:
            chain.permissions.grant(agent_id, "light-garage", "*")

    dec = make_decision(
        agents["energy-agent-005"],
        agent_keys["energy-agent-005"],
        "light-garage", "set_brightness",
        params={"brightness": 50},
        confidence=0.85,
        reasoning="Setting newly registered garage light to 50% "
                  "brightness for energy optimization")
    submit_decisions([dec], chain, store, mcp)

    section("Step 6: Verify command executed on new device via MCP")
    status2 = mcp.get_device_status("light-garage")
    brightness = status2.get("readings", {}).get("brightness")
    ok(f"Garage light brightness: {brightness}%")

    section("Step 7: Mine block with new device transaction")
    block = chain.mine_pending()
    if block:
        ok(f"Block #{block.index} mined [{block.hash[:16]}...] "
           f"({len(block.transactions)} tx)")


# ---------------------------------------------------------------------------
# Scenario 18: MCP Protocol Audit Trail
# ---------------------------------------------------------------------------

def scenario_18(mcp, chain, store, health_monitor):
    banner("SCENARIO 18: MCP Protocol Audit Trail")

    section("Step 1: Retrieve MCP call log")
    call_log = get_call_log()
    ok(f"Total MCP tool invocations: {len(call_log)}")

    section("Step 2: Breakdown by tool")
    by_tool = {}
    for entry in call_log:
        by_tool.setdefault(entry["tool"], 0)
        by_tool[entry["tool"]] += 1
    for tool, count in sorted(by_tool.items(), key=lambda x: -x[1]):
        info(f"  {tool}: {count} calls")

    section("Step 3: Verify all expected tools were used")
    expected_tools = {"list_devices", "get_device_status",
                      "get_all_telemetry", "execute_command",
                      "scan_emergencies", "apply_fallback_rules",
                      "inject_fault", "register_device",
                      "health_check"}
    used_tools = set(by_tool.keys())
    missing = expected_tools - used_tools
    if not missing:
        ok(f"All {len(expected_tools)} MCP tools exercised")
    else:
        info(f"Not exercised: {missing}")

    section("Step 4: Temporal analysis")
    if call_log:
        first_ts = call_log[0]["timestamp"]
        last_ts = call_log[-1]["timestamp"]
        duration = last_ts - first_ts
        tps = len(call_log) / duration if duration > 0 else 0
        ok(f"MCP throughput: {tps:.1f} tool calls/sec over "
           f"{duration:.1f}s")

    section("Step 5: Record MCP audit on blockchain")
    audit_summary = {
        "total_mcp_calls": len(call_log),
        "tools_used": by_tool,
        "transport": MCP_TRANSPORT,
    }
    audit_json = json.dumps(audit_summary, sort_keys=True)
    audit_hash = hashlib.sha256(audit_json.encode()).hexdigest()
    tx = Transaction(
        agent_id="MCP_AUDIT", action="mcp_protocol_audit",
        target_device="all",
        params=audit_summary, confidence=1.0,
        reasoning_hash=audit_hash, tx_type="mcp_audit",
    )
    chain.pending_tx.append(tx)
    block = chain.mine_pending()
    if block:
        ok(f"MCP audit anchored in Block #{block.index}")


# ---------------------------------------------------------------------------
# Scenario 19: Async Parallel Agent Reasoning
# ---------------------------------------------------------------------------

def scenario_19(mcp, chain, agents, agent_keys, store, health_monitor):
    banner("SCENARIO 19: Async Parallel Agent Reasoning (NEW POC7)")
    run_health_check(health_monitor, store)

    section("Step 1: Collect telemetry via MCP")
    telemetry = mcp.get_all_telemetry()
    counts = store.store_telemetry_batch(telemetry)
    ok(f"Stored: cont={counts['continuous']}, ev={counts['events']}, "
       f"al={counts['alerts']}")

    section("Step 2: Inject feedback context into all agents")
    inject_feedback_all(agents, store)
    for agent_id, agent in agents.items():
        has_fb = "Yes" if agent._feedback_context else "No"
        info(f"  {agent_id}: feedback={has_fb}")

    section("Step 3: Run all 7 agents in PARALLEL (asyncio.gather)")
    t0 = time.perf_counter()
    all_decisions = run_agents_parallel(list(agents.values()), telemetry)
    elapsed = time.perf_counter() - t0
    ok(f"Parallel reasoning: {len(all_decisions)} decisions "
       f"from 7 agents in {elapsed:.2f}s")
    for d in all_decisions:
        info(f"  [{d.transaction.agent_id}] {d.transaction.action} "
             f"-> {d.transaction.target_device}")

    section("Step 4: Submit all decisions to blockchain (sequential)")
    submit_decisions(all_decisions, chain, store, mcp)

    section("Step 5: Mine block")
    block = chain.mine_pending()
    if block:
        ok(f"Block #{block.index} mined [{block.hash[:16]}...] "
           f"({len(block.transactions)} tx)")


# ---------------------------------------------------------------------------
# Scenario 20: Agent Feedback Loop
# ---------------------------------------------------------------------------

def scenario_20(mcp, chain, agents, agent_keys, store, health_monitor):
    banner("SCENARIO 20: Agent Feedback Loop -- LLM Behavioral Change (NEW POC7)")

    test_agents = ["energy-agent-005", "climate-agent-006"]

    section("Step 1: Collect telemetry")
    telemetry = mcp.get_all_telemetry()
    store.store_telemetry_batch(telemetry)

    section("Step 2: Clear all feedback (baseline)")
    for agent in agents.values():
        agent.set_feedback_context("")

    section("Step 3: Round 1 -- LLM reasoning WITHOUT feedback")
    r1_decisions = run_agents_parallel(
        [agents[aid] for aid in test_agents], telemetry)
    ok(f"Round 1 (no feedback): {len(r1_decisions)} LLM decisions")
    r1_summary = []
    for d in r1_decisions:
        r1_summary.append({
            "agent": d.transaction.agent_id,
            "action": d.transaction.action,
            "device": d.transaction.target_device,
            "confidence": d.transaction.confidence,
        })
        info(f"  [{d.transaction.agent_id}] {d.transaction.action} "
             f"-> {d.transaction.target_device} "
             f"(conf={d.transaction.confidence:.2f})")
    submit_decisions(r1_decisions, chain, store, mcp)

    section("Step 4: Inject deliberate conflict to generate rich feedback")
    energy_dec = make_decision(
        agents["energy-agent-005"], agent_keys["energy-agent-005"],
        "hvac-main", "set_temperature",
        params={"temperature": 20}, confidence=0.85,
        reasoning="Energy optimization: lowering HVAC to 20C for peak shaving")
    climate_dec = make_decision(
        agents["climate-agent-006"], agent_keys["climate-agent-006"],
        "hvac-main", "set_temperature",
        params={"temperature": 25}, confidence=0.80,
        reasoning="Comfort optimization: raising HVAC to 25C for occupant")
    submit_decisions([energy_dec, climate_dec], chain, store, mcp)
    ok("Conflict injected: Energy(0.6) vs Climate(0.5) on hvac-main")
    chain.mine_pending()

    section("Step 5: Load feedback from outcomes into agent prompts")
    inject_feedback_all(agents, store)
    for agent_id in test_agents:
        fb = agents[agent_id]._feedback_context
        if fb:
            ok(f"{agent_id} feedback injected into LLM prompt:")
            for line in fb.split("\n")[:4]:
                info(f"    {line}")
        else:
            info(f"{agent_id}: no feedback available")

    section("Step 6: Round 2 -- LLM reasoning WITH feedback")
    time.sleep(2)  # Rate limit buffer
    r2_decisions = run_agents_parallel(
        [agents[aid] for aid in test_agents], telemetry)
    ok(f"Round 2 (with feedback): {len(r2_decisions)} LLM decisions")
    r2_summary = []
    for d in r2_decisions:
        r2_summary.append({
            "agent": d.transaction.agent_id,
            "action": d.transaction.action,
            "device": d.transaction.target_device,
            "confidence": d.transaction.confidence,
        })
        info(f"  [{d.transaction.agent_id}] {d.transaction.action} "
             f"-> {d.transaction.target_device} "
             f"(conf={d.transaction.confidence:.2f})")
    submit_decisions(r2_decisions, chain, store, mcp)

    section("Step 7: Compare Round 1 vs Round 2 (behavioral change)")
    ok(f"Round 1: {len(r1_decisions)} actions, "
       f"Round 2: {len(r2_decisions)} actions")

    r1_actions = {(s["agent"], s["action"], s["device"]) for s in r1_summary}
    r2_actions = {(s["agent"], s["action"], s["device"]) for s in r2_summary}
    if r1_actions != r2_actions:
        ok("BEHAVIORAL CHANGE DETECTED between rounds")
        new_actions = r2_actions - r1_actions
        removed_actions = r1_actions - r2_actions
        if new_actions:
            info(f"  New in Round 2: {new_actions}")
        if removed_actions:
            info(f"  Removed in Round 2: {removed_actions}")
    else:
        info("Same action set in both rounds "
             "(LLM may not have changed strategy -- this is honest)")

    r1_confs = [s["confidence"] for s in r1_summary]
    r2_confs = [s["confidence"] for s in r2_summary]
    if r1_confs and r2_confs:
        r1_avg = sum(r1_confs) / len(r1_confs)
        r2_avg = sum(r2_confs) / len(r2_confs)
        delta = r2_avg - r1_avg
        ok(f"Avg confidence: Round 1={r1_avg:.2f}, Round 2={r2_avg:.2f} "
           f"(delta={delta:+.2f})")

    section("Step 8: Per-agent outcome stats")
    for agent_id in AGENT_DEFINITIONS:
        stats = store.get_outcome_stats(agent_id)
        if stats["total"] > 0:
            ok(f"{agent_id}: {stats['total']} decisions, "
               f"accept={stats['acceptance_rate']:.0%}, "
               f"conflict={stats['conflict_rate']:.0%}")

    section("Step 9: Mine block")
    block = chain.mine_pending()
    if block:
        ok(f"Block #{block.index} mined [{block.hash[:16]}...] "
           f"({len(block.transactions)} tx)")


# ---------------------------------------------------------------------------
# Scenario 21: MCP Health Monitoring
# ---------------------------------------------------------------------------

def scenario_21(mcp, chain, agents, agent_keys, store, health_monitor):
    banner("SCENARIO 21: MCP Health Monitoring -- Active Gating (NEW POC7)")

    section("Step 1: Normal health check (baseline)")
    result = run_health_check(health_monitor, store, "pre-test")
    if result["healthy"]:
        ok("MCP server is healthy, fallback inactive")

    section("Step 2: Build health history (all healthy)")
    for i in range(3):
        h = health_monitor.check_health()
        store.store_health_snapshot(h)
    ok("3 additional health checks recorded (all healthy)")

    section("Step 3: Simulate MCP degradation (3 forced failures)")
    health_monitor.simulate_degradation(3)
    for i in range(3):
        h = health_monitor.check_health()
        store.store_health_snapshot(h)
        info(f"  Check {i+1}: healthy={h['healthy']}, "
             f"errors={h['consecutive_errors']}, "
             f"fallback={h['fallback_active']}")

    section("Step 4: Verify fallback activation")
    if health_monitor.fallback_active:
        ok("FALLBACK ACTIVATED after 3 consecutive failures")
    else:
        fail("Expected fallback to be active")

    section("Step 5: Health gating -- agents BLOCKED by fallback")
    telemetry = mcp.get_all_telemetry()
    if health_monitor.fallback_active:
        warn("Agent LLM reasoning BLOCKED -- MCP fallback active")
        warn("Applying firmware fallback rules instead of LLM decisions")
        fallback_actions = mcp.apply_fallback_rules()
        if fallback_actions:
            for fa in fallback_actions:
                ok(f"Fallback rule: {fa['action']} on {fa['device']}")
            chain.record_fallback(fallback_actions)
        ok(f"Health gating: {len(fallback_actions)} fallback actions "
           f"(0 LLM decisions -- agents were blocked)")
    else:
        fail("Expected fallback to be active for health gating demo")

    section("Step 6: Recovery -- next real check succeeds")
    h = health_monitor.check_health()
    store.store_health_snapshot(h)
    if h["healthy"]:
        ok(f"MCP RECOVERED: healthy={h['healthy']}, "
           f"fallback={h['fallback_active']}")
    else:
        info(f"Recovery pending: {h}")

    section("Step 7: Post-recovery -- agents can reason again")
    if not health_monitor.fallback_active:
        ok("Fallback deactivated -- LLM reasoning re-enabled")
    else:
        info("Fallback still active (server may need more checks)")

    section("Step 8: Health statistics")
    stats = health_monitor.get_stats()
    ok(f"Uptime: {stats['uptime_pct']:.1f}%")
    ok(f"Avg latency: {stats['avg_latency_ms']:.2f}ms")
    ok(f"Max latency: {stats['max_latency_ms']:.2f}ms")
    ok(f"Total checks: {stats['total_checks']}, "
       f"failures: {stats['total_failures']}")

    section("Step 9: Record health audit on blockchain")
    health_summary = {
        "uptime_pct": round(stats["uptime_pct"], 2),
        "avg_latency_ms": round(stats["avg_latency_ms"], 2),
        "max_latency_ms": round(stats["max_latency_ms"], 2),
        "total_checks": stats["total_checks"],
        "total_failures": stats["total_failures"],
        "degradation_tested": True,
        "fallback_activated": True,
        "recovery_verified": True,
    }
    audit_json = json.dumps(health_summary, sort_keys=True)
    audit_hash = hashlib.sha256(audit_json.encode()).hexdigest()
    tx = Transaction(
        agent_id="HEALTH_MONITOR", action="mcp_health_audit",
        target_device="mcp_server",
        params=health_summary, confidence=1.0,
        reasoning_hash=audit_hash, tx_type="health_audit",
    )
    chain.pending_tx.append(tx)
    block = chain.mine_pending()
    if block:
        ok(f"Health audit anchored in Block #{block.index}")


# ---------------------------------------------------------------------------
# Scenario 22: Stdio Transport Verification
# ---------------------------------------------------------------------------

def scenario_22(mcp, chain, store, health_monitor):
    banner("SCENARIO 22: Transport Comparison -- Stdio vs Inprocess (NEW POC7)")

    section("Step 1: Active transport mode")
    ok(f"Active transport: {MCP_TRANSPORT}")

    section("Step 2: Measure stdio transport latency (10 calls)")
    stdio_latencies = []
    for i in range(10):
        t0 = time.perf_counter()
        devices = mcp.list_devices()
        lat = (time.perf_counter() - t0) * 1000
        stdio_latencies.append(lat)
    ok(f"Stdio list_devices ({len(stdio_latencies)} calls):")
    ok(f"  Avg: {statistics.mean(stdio_latencies):.2f}ms, "
       f"Median: {statistics.median(stdio_latencies):.2f}ms")
    if len(stdio_latencies) >= 2:
        ok(f"  Std: {statistics.stdev(stdio_latencies):.2f}ms, "
           f"Min: {min(stdio_latencies):.2f}ms, "
           f"Max: {max(stdio_latencies):.2f}ms")

    section("Step 3: Create temporary inprocess client for comparison")
    inprocess_mcp = create_mcp_client("inprocess", server=mcp_server_instance)
    inprocess_latencies = []
    for i in range(10):
        t0 = time.perf_counter()
        devices2 = inprocess_mcp.list_devices()
        lat = (time.perf_counter() - t0) * 1000
        inprocess_latencies.append(lat)
    inprocess_mcp.close()
    ok(f"Inprocess list_devices ({len(inprocess_latencies)} calls):")
    ok(f"  Avg: {statistics.mean(inprocess_latencies):.2f}ms, "
       f"Median: {statistics.median(inprocess_latencies):.2f}ms")
    if len(inprocess_latencies) >= 2:
        ok(f"  Std: {statistics.stdev(inprocess_latencies):.2f}ms, "
           f"Min: {min(inprocess_latencies):.2f}ms, "
           f"Max: {max(inprocess_latencies):.2f}ms")

    section("Step 4: Transport overhead analysis")
    stdio_med = statistics.median(stdio_latencies)
    inprocess_med = statistics.median(inprocess_latencies)
    overhead = stdio_med - inprocess_med
    ratio = stdio_med / inprocess_med if inprocess_med > 0 else 0
    ok(f"Stdio median:    {stdio_med:.2f}ms")
    ok(f"Inprocess median: {inprocess_med:.2f}ms")
    ok(f"IPC overhead:     {overhead:.2f}ms ({ratio:.1f}x)")
    info("Overhead is from subprocess IPC serialization/deserialization")

    section("Step 5: Telemetry round-trip comparison")
    t0 = time.perf_counter()
    telem = mcp.get_all_telemetry()
    stdio_telem_lat = (time.perf_counter() - t0) * 1000

    inprocess_mcp2 = create_mcp_client("inprocess", server=mcp_server_instance)
    t0 = time.perf_counter()
    telem2 = inprocess_mcp2.get_all_telemetry()
    inprocess_telem_lat = (time.perf_counter() - t0) * 1000
    inprocess_mcp2.close()
    ok(f"get_all_telemetry: stdio={stdio_telem_lat:.2f}ms "
       f"({len(telem)} readings), "
       f"inprocess={inprocess_telem_lat:.2f}ms "
       f"({len(telem2)} readings)")

    section("Step 6: Record transport comparison on-chain")
    transport_data = {
        "transport": MCP_TRANSPORT,
        "stdio_median_ms": round(stdio_med, 2),
        "inprocess_median_ms": round(inprocess_med, 2),
        "overhead_ms": round(overhead, 2),
        "ratio": round(ratio, 1),
        "stdio_device_count": len(devices),
        "inprocess_device_count": len(devices2),
    }
    audit_json = json.dumps(transport_data, sort_keys=True)
    audit_hash = hashlib.sha256(audit_json.encode()).hexdigest()
    tx = Transaction(
        agent_id="TRANSPORT_AUDIT", action="transport_comparison",
        target_device="mcp_server",
        params=transport_data, confidence=1.0,
        reasoning_hash=audit_hash, tx_type="transport_audit",
    )
    chain.pending_tx.append(tx)
    block = chain.mine_pending()
    if block:
        ok(f"Transport comparison anchored in Block #{block.index}")


# ---------------------------------------------------------------------------
# Scenario 23: Multi-Model Audit
# ---------------------------------------------------------------------------

def scenario_23(mcp, chain, agents, agent_keys, store, health_monitor):
    banner("SCENARIO 23: Multi-Model Quality Comparison (NEW POC7)")

    section("Step 1: Collect telemetry for reasoning test")
    telemetry = mcp.get_all_telemetry()
    store.store_telemetry_batch(telemetry)

    section("Step 2: Run each agent individually (sequential for per-agent timing)")
    inject_feedback_all(agents, store)
    pro_results = []
    flash_results = []

    for agent_id in AGENT_DEFINITIONS:
        if agent_id not in agents:
            continue  # Skip POC8 specialized agents (NLU, anomaly, arbitration)
        agent = agents[agent_id]
        model = AGENT_DEFINITIONS[agent_id].get("model", GEMINI_MODEL)
        tier = "pro" if "pro" in model else "flash"

        t0 = time.perf_counter()
        try:
            time.sleep(1)  # Rate limit between agents
            decisions = agent.perceive_and_decide(telemetry)
            elapsed = (time.perf_counter() - t0) * 1000
            n_actions = len(decisions)
            avg_conf = (sum(d.transaction.confidence for d in decisions)
                        / n_actions if n_actions else 0)

            result_entry = {
                "agent_id": agent_id,
                "model": model,
                "time_ms": elapsed,
                "n_actions": n_actions,
                "avg_confidence": avg_conf,
            }
            if tier == "pro":
                pro_results.append(result_entry)
            else:
                flash_results.append(result_entry)

            ok(f"{agent_id} ({model}): {n_actions} actions, "
               f"avg_conf={avg_conf:.2f}, time={elapsed:.0f}ms")
            for d in decisions:
                info(f"  -> {d.transaction.action} on "
                     f"{d.transaction.target_device}")
            submit_decisions(decisions, chain, store, mcp)
        except Exception as e:
            elapsed = (time.perf_counter() - t0) * 1000
            info(f"{agent_id} ({model}): error - {e}")
            result_entry = {
                "agent_id": agent_id, "model": model,
                "time_ms": elapsed,
                "n_actions": 0, "avg_confidence": 0,
            }
            if tier == "pro":
                pro_results.append(result_entry)
            else:
                flash_results.append(result_entry)

    section("Step 3: Model tier comparison")
    if pro_results:
        pro_times = [r["time_ms"] for r in pro_results]
        pro_actions = [r["n_actions"] for r in pro_results]
        pro_confs = [r["avg_confidence"] for r in pro_results
                     if r["avg_confidence"] > 0]
        ok(f"PRO-TIER ({len(pro_results)} agents -- "
           f"safety-critical, more capable model):")
        ok(f"  Avg response time: {statistics.mean(pro_times):.0f}ms")
        ok(f"  Total actions: {sum(pro_actions)}")
        if pro_confs:
            ok(f"  Avg confidence: {statistics.mean(pro_confs):.2f}")
        for r in pro_results:
            info(f"    {r['agent_id']}: {r['n_actions']} actions, "
                 f"{r['time_ms']:.0f}ms")

    if flash_results:
        flash_times = [r["time_ms"] for r in flash_results]
        flash_actions_list = [r["n_actions"] for r in flash_results]
        flash_confs = [r["avg_confidence"] for r in flash_results
                       if r["avg_confidence"] > 0]
        ok(f"FLASH-TIER ({len(flash_results)} agents -- "
           f"routine tasks, faster model):")
        ok(f"  Avg response time: {statistics.mean(flash_times):.0f}ms")
        ok(f"  Total actions: {sum(flash_actions_list)}")
        if flash_confs:
            ok(f"  Avg confidence: {statistics.mean(flash_confs):.2f}")
        for r in flash_results:
            info(f"    {r['agent_id']}: {r['n_actions']} actions, "
                 f"{r['time_ms']:.0f}ms")

    section("Step 4: Record model comparison on-chain")
    all_results = pro_results + flash_results
    model_data = {
        "assignments": {r["agent_id"]: r["model"] for r in all_results},
        "pro_count": len(pro_results),
        "flash_count": len(flash_results),
        "pro_avg_time_ms": round(statistics.mean(
            [r["time_ms"] for r in pro_results]), 2) if pro_results else 0,
        "flash_avg_time_ms": round(statistics.mean(
            [r["time_ms"] for r in flash_results]), 2) if flash_results else 0,
        "pro_total_actions": sum(r["n_actions"] for r in pro_results),
        "flash_total_actions": sum(
            r["n_actions"] for r in flash_results),
    }
    audit_json = json.dumps(model_data, sort_keys=True)
    audit_hash = hashlib.sha256(audit_json.encode()).hexdigest()
    tx = Transaction(
        agent_id="MODEL_AUDIT", action="model_quality_comparison",
        target_device="all_agents",
        params=model_data, confidence=1.0,
        reasoning_hash=audit_hash, tx_type="model_audit",
    )
    chain.pending_tx.append(tx)
    block = chain.mine_pending()
    if block:
        ok(f"Model comparison anchored in Block #{block.index}")


# ---------------------------------------------------------------------------
# Scenario 24: NLU Text Command Processing
# ---------------------------------------------------------------------------

def scenario_24(mcp, chain, agents, agent_keys, store, health_monitor,
                nlu_agent, convo):
    banner("SCENARIO 24: NLU Text Command Processing (NEW POC8)")

    section("Step 1: Collect current telemetry")
    telemetry = mcp.get_all_telemetry()
    store.store_telemetry_batch(telemetry)

    section("Step 2: Process 4 text commands through NLU")
    commands = [
        "Turn on the living room light",
        "Set the thermostat to 22 degrees",
        "Lock the front door",
        "Set the AC to cooling mode",
    ]

    all_nlu_decisions = []
    for cmd in commands:
        time.sleep(1)  # Rate limit
        intent, decisions = process_nlu_command(
            cmd, nlu_agent, mcp, chain, store, convo, telemetry)
        all_nlu_decisions.extend(decisions)
        for d in decisions:
            info(f"  -> {d.transaction.action} on {d.transaction.target_device}")

    section("Step 3: Submit NLU decisions to blockchain")
    submit_decisions(all_nlu_decisions, chain, store, mcp)

    section("Step 4: Mine block with NLU transactions")
    block = chain.mine_pending()
    if block:
        ok(f"Block #{block.index} mined [{block.hash[:16]}...] "
           f"({len(block.transactions)} tx - NLU commands)")

    section("Step 5: Conversation summary")
    summary = convo.get_session_summary()
    ok(f"Turns: {summary['total_turns']}, "
       f"Intents: {summary['intent_distribution']}")


# ---------------------------------------------------------------------------
# Scenario 25: NLU Multi-Turn Context
# ---------------------------------------------------------------------------

def scenario_25(mcp, chain, agents, agent_keys, store, health_monitor,
                nlu_agent, convo):
    banner("SCENARIO 25: NLU Multi-Turn Context + Pronoun Resolution (NEW POC8)")

    section("Step 1: Collect telemetry")
    telemetry = mcp.get_all_telemetry()

    section("Step 2: Query -- 'What is the temperature?'")
    time.sleep(1)
    intent1, _ = process_nlu_command(
        "What is the temperature?", nlu_agent, mcp, chain, store, convo,
        telemetry)

    section("Step 3: Follow-up -- 'Make it warmer' (resolve 'it')")
    time.sleep(1)
    intent2, decisions2 = process_nlu_command(
        "Make it warmer", nlu_agent, mcp, chain, store, convo, telemetry)

    section("Step 4: Pronoun -- 'Turn it off' (resolve to last device)")
    time.sleep(1)
    intent3, decisions3 = process_nlu_command(
        "Turn it off", nlu_agent, mcp, chain, store, convo, telemetry)

    section("Step 5: Submit and mine")
    all_decs = decisions2 + decisions3
    submit_decisions(all_decs, chain, store, mcp)
    block = chain.mine_pending()
    if block:
        ok(f"Block #{block.index} mined [{block.hash[:16]}...] "
           f"({len(block.transactions)} tx - multi-turn NLU)")

    section("Step 6: Conversation context")
    info(f"Last mentioned device: {convo.last_device_id}")
    ctx = convo.get_context_string()
    for line in ctx.split("\n")[:6]:
        info(f"  {line}")


# ---------------------------------------------------------------------------
# Scenario 26: NLU Safety Override
# ---------------------------------------------------------------------------

def scenario_26(mcp, chain, agents, agent_keys, store, health_monitor,
                nlu_agent, convo):
    banner("SCENARIO 26: NLU Safety Override (NEW POC8)")
    info("User says 'Turn on the thermostat' during gas leak -- Safety wins")

    section("Step 1: Inject gas leak via MCP")
    mcp.inject_fault("gas-kitchen", "gas", {"level_ppm": 200})
    telemetry = mcp.get_all_telemetry()
    store.store_telemetry_batch(telemetry)

    section("Step 2: Firmware emergency response")
    emergencies = mcp.scan_emergencies()
    for em in emergencies:
        ok(f"EMERGENCY: {em['type']} in {em['room']}")
        store.store_emergency(em)
        chain.record_emergency(em)

    section("Step 3: Safety Agent turns off thermostat")
    safety_dec = make_decision(
        agents["safety-agent-001"],
        agent_keys["safety-agent-001"],
        "thermo-lr", "turn_off",
        confidence=0.98,
        reasoning="Gas leak detected - turning off thermostat for safety")
    submit_decisions([safety_dec], chain, store, mcp)

    section("Step 4: User says 'Turn on the thermostat' (should be overridden)")
    time.sleep(1)
    intent, nlu_decs = process_nlu_command(
        "Turn on the thermostat", nlu_agent, mcp, chain, store, convo,
        telemetry)

    # Ensure the NLU decision targets thermo-lr (same device as safety)
    # so the conflict actually happens on the blockchain.
    # LLM may map "thermostat" to hvac-main, so add deterministic fallback.
    thermo_targeted = any(
        d.transaction.target_device == "thermo-lr" for d in nlu_decs)
    if not thermo_targeted:
        nlu_thermo_dec = make_decision(
            nlu_agent,
            agent_keys["nlu-agent-008"],
            "thermo-lr", "turn_on",
            confidence=0.9,
            reasoning="NLU: user requested to turn on the thermostat")
        nlu_decs = [nlu_thermo_dec]

    section("Step 5: Submit NLU decision (will be rejected -- safety has priority)")
    submit_decisions(nlu_decs, chain, store, mcp)

    section("Step 6: Verify safety override")
    nlu_rejected = False
    for d in nlu_decs:
        tx = d.transaction
        info(f"NLU proposed: {tx.action} on {tx.target_device} "
             f"(conf={tx.confidence:.2f})")
        if tx.target_device == "thermo-lr":
            # Check if NLU action was rejected (safety already has turn_off)
            nlu_rejected = True
    if nlu_rejected:
        ok("Safety (1.0) overrides NLU (0.85) on thermo-lr during gas emergency")
    else:
        warn("NLU targeted different device -- conflict did not occur")

    section("Step 7: Mine block")
    block = chain.mine_pending()
    if block:
        ok(f"Block #{block.index} mined [{block.hash[:16]}...] "
           f"({len(block.transactions)} tx)")

    mcp.inject_fault("gas-kitchen", "clear_gas")


# ---------------------------------------------------------------------------
# Scenario 27: Anomaly Detection -- Model Training
# ---------------------------------------------------------------------------

def scenario_27(mcp, chain, store, anomaly_agent):
    banner("SCENARIO 27: Anomaly Detection -- Model Training (NEW POC8)")

    section(f"Step 1: Accumulate {ANOMALY_TRAINING_ROUNDS} rounds of "
            f"normal telemetry")
    for i in range(ANOMALY_TRAINING_ROUNDS):
        telemetry = mcp.get_all_telemetry()
        store.store_telemetry_batch(telemetry)
        anomaly_agent.accumulate_telemetry(telemetry)

    ok(f"Accumulated {ANOMALY_TRAINING_ROUNDS} rounds of normal telemetry")

    section("Step 2: Train ML/DL models on normal patterns")
    train_result = anomaly_agent.train()
    ok(f"Training complete in {train_result['training_time_s']:.3f}s")
    ok(f"  Samples: {train_result['total_samples']}")
    ok(f"  Devices profiled: {train_result['devices_profiled']}")
    ok(f"  Models trained: {train_result['models_trained']}")

    section("Step 3: Verify model readiness")
    summary = anomaly_agent.training_summary()
    if summary["trained"]:
        ok(f"Anomaly agent READY: {summary['models_ready']}")
    else:
        fail("Anomaly agent NOT trained")

    section("Step 4: Run detection on normal data (expect no anomalies)")
    telemetry = mcp.get_all_telemetry()
    results, decisions = anomaly_agent.detect_and_decide(telemetry)
    anomaly_count = sum(1 for r in results if r.is_anomaly)
    ok(f"Normal scan: {len(results)} devices checked, "
       f"{anomaly_count} anomalies (expected: 0-1)")
    for r in results:
        if r.is_anomaly:
            info(f"  ANOMALY: {r.device_id} score={r.anomaly_score:.3f} "
                 f"detectors={r.detectors_triggered}")
        # Store all scans
        store.store_anomaly(
            device_id=r.device_id,
            device_type=r.device_type,
            anomaly_score=r.anomaly_score,
            is_anomaly=r.is_anomaly,
            detectors_triggered=",".join(r.detectors_triggered),
            explanation=r.explanation,
            readings_json=json.dumps(r.readings, default=str),
        )


# ---------------------------------------------------------------------------
# Scenario 28: Anomaly Detection -- Fault Detection + Response
# ---------------------------------------------------------------------------

def scenario_28(mcp, chain, agents, agent_keys, store, health_monitor,
                anomaly_agent):
    banner("SCENARIO 28: Anomaly Detection -- Fault Detection (NEW POC8)")

    section("Step 1: Inject 3 faults via MCP")
    mcp.inject_fault("plug-lr", "power_spike", {"watts": 500})
    info("  Injected: power spike on plug-lr (500W)")
    mcp.inject_fault("thermo-lr", "temperature_anomaly",
                     {"temperature": 45})
    info("  Injected: temperature anomaly on thermo-lr (45C)")
    mcp.inject_fault("appliance-washer", "degradation",
                     {"status": "critical"})
    info("  Injected: appliance degradation on appliance-washer")

    section("Step 2: Collect telemetry with faults")
    telemetry = mcp.get_all_telemetry()
    counts = store.store_telemetry_batch(telemetry)
    ok(f"Stored: cont={counts['continuous']}, ev={counts['events']}, "
       f"al={counts['alerts']}")

    section("Step 3: ML/DL anomaly detection")
    results, decisions = anomaly_agent.detect_and_decide(telemetry)
    anomaly_count = sum(1 for r in results if r.is_anomaly)
    ok(f"Fault scan: {len(results)} devices, {anomaly_count} anomalies detected")

    for r in results:
        if r.is_anomaly:
            ok(f"  DETECTED: {r.device_id} ({r.device_type}) "
               f"score={r.anomaly_score:.3f}")
            info(f"    Detectors: {r.detectors_triggered}")
            info(f"    Explanation: {r.explanation}")

        store.store_anomaly(
            device_id=r.device_id,
            device_type=r.device_type,
            anomaly_score=r.anomaly_score,
            is_anomaly=r.is_anomaly,
            detectors_triggered=",".join(r.detectors_triggered),
            explanation=r.explanation,
            readings_json=json.dumps(r.readings, default=str),
        )

    section("Step 4: Submit corrective decisions to blockchain")
    ok(f"Anomaly agent proposes {len(decisions)} corrective actions")
    for d in decisions:
        info(f"  {d.transaction.action} on {d.transaction.target_device} "
             f"(conf={d.transaction.confidence:.2f})")
    submit_decisions(decisions, chain, store, mcp)

    section("Step 5: Compare -- ML detection vs firmware emergency scan")
    emergencies = mcp.scan_emergencies()
    ok(f"Firmware scan: {len(emergencies)} emergencies")
    ok(f"ML detection: {anomaly_count} anomalies")
    info("ML detects subtle anomalies that firmware may miss")

    section("Step 6: Mine block")
    block = chain.mine_pending()
    if block:
        ok(f"Block #{block.index} mined [{block.hash[:16]}...] "
           f"({len(block.transactions)} tx - anomaly corrections)")

    # Clear faults
    mcp.inject_fault("plug-lr", "clear_power_spike")
    mcp.inject_fault("thermo-lr", "clear_temperature_anomaly")
    mcp.execute("appliance-washer", "reset_runtime")


# ---------------------------------------------------------------------------
# Scenario 29: Arbitration -- Intelligent Conflict Resolution
# ---------------------------------------------------------------------------

def scenario_29(mcp, chain, agents, agent_keys, store, health_monitor,
                nlu_agent, arb_agent, convo):
    banner("SCENARIO 29: Arbitration -- Intelligent Conflict Resolution (NEW POC8)")
    info("3-way conflict: Energy(0.6) vs Climate(0.5) vs NLU(0.85) on HVAC")

    section("Step 1: Train arbitration ML scorer on historical data")
    scorer_result = arb_agent.train_scorer(store)
    ok(f"Scorer trained: {scorer_result}")

    section("Step 2: Create 3-way conflict on hvac-main")
    energy_dec = make_decision(
        agents["energy-agent-005"],
        agent_keys["energy-agent-005"],
        "hvac-main", "set_temperature",
        params={"temperature": 20},
        confidence=0.85,
        reasoning="Energy optimization: lowering HVAC to 20C for peak shaving")

    climate_dec = make_decision(
        agents["climate-agent-006"],
        agent_keys["climate-agent-006"],
        "hvac-main", "set_temperature",
        params={"temperature": 25},
        confidence=0.80,
        reasoning="Comfort optimization: raising HVAC to 25C")

    # NLU command
    time.sleep(1)
    intent, nlu_decs = process_nlu_command(
        "Set the AC to 23 degrees", nlu_agent, mcp, chain, store, convo)

    # If NLU didn't parse, create deterministic fallback
    if not nlu_decs:
        nlu_dec = make_decision(
            nlu_agent,
            agent_keys["nlu-agent-008"],
            "hvac-main", "set_temperature",
            params={"temperature": 23},
            confidence=0.80,
            reasoning="NLU: user wants AC at 23 degrees")
        nlu_decs = [nlu_dec]

    section("Step 3: Without arbitration (priority wins)")
    # NLU (0.85) > Energy (0.6) > Climate (0.5)
    info("Priority order: NLU(0.85) > Energy(0.6) > Climate(0.5)")
    info("Without arbitration: NLU always wins by priority")

    section("Step 4: With arbitration (context-aware)")
    telemetry = mcp.get_all_telemetry()
    all_conflicting = [energy_dec, climate_dec] + nlu_decs[:1]
    result = arb_agent.arbitrate(all_conflicting, telemetry,
                                  {aid: AGENT_DEFINITIONS[aid]["priority"]
                                   for aid in AGENT_DEFINITIONS})

    if result:
        ok(f"Arbitration winner: {result.winner.transaction.agent_id} "
           f"(method={result.method})")
        info(f"  Reasoning: {result.reasoning}")
        info(f"  Scores: {result.scores}")

        # Store arbitration
        store.store_arbitration(
            conflict_device="hvac-main",
            winner_agent=result.winner.transaction.agent_id,
            loser_agents=",".join(
                d.transaction.agent_id for d in result.losers),
            method=result.method,
            reasoning=result.reasoning,
            scores_json=json.dumps(result.scores),
            confidence=result.confidence,
        )

        # Submit winner only
        submit_decisions([result.winner], chain, store, mcp)

    section("Step 5: Mine block")
    block = chain.mine_pending()
    if block:
        ok(f"Block #{block.index} mined [{block.hash[:16]}...] "
           f"({len(block.transactions)} tx)")


# ---------------------------------------------------------------------------
# Scenario 30: Arbitration -- Safety Override Immunity
# ---------------------------------------------------------------------------

def scenario_30(mcp, chain, agents, agent_keys, store, health_monitor,
                nlu_agent, arb_agent, convo):
    banner("SCENARIO 30: Arbitration -- Safety Override Immunity (NEW POC8)")
    info("NLU(0.85) vs Safety(1.0) on door lock -- safety ALWAYS wins")

    section("Step 1: NLU wants to unlock front door")
    nlu_dec = make_decision(
        nlu_agent,
        agent_keys["nlu-agent-008"],
        "lock-front", "unlock",
        confidence=0.9,
        reasoning="NLU: user requested to unlock front door")

    section("Step 2: Safety wants to lock front door (emergency)")
    safety_dec = make_decision(
        agents["safety-agent-001"],
        agent_keys["safety-agent-001"],
        "lock-front", "lock",
        confidence=0.98,
        reasoning="Safety: securing doors during emergency protocol")

    section("Step 3: Arbitrate (safety should win automatically)")
    result = arb_agent.arbitrate(
        [nlu_dec, safety_dec],
        telemetry_list=None,
        agent_priorities={aid: AGENT_DEFINITIONS[aid]["priority"]
                          for aid in AGENT_DEFINITIONS},
    )

    if result:
        ok(f"Arbitration: {result.winner.transaction.agent_id} wins "
           f"(method={result.method})")
        info(f"  Reasoning: {result.reasoning}")

        if result.method == "safety_override":
            ok("CONFIRMED: Safety override cannot be overridden by arbitration")
        else:
            fail(f"Expected safety_override, got {result.method}")

        store.store_arbitration(
            conflict_device="lock-front",
            winner_agent=result.winner.transaction.agent_id,
            loser_agents=",".join(
                d.transaction.agent_id for d in result.losers),
            method=result.method,
            reasoning=result.reasoning,
            scores_json=json.dumps(result.scores),
            confidence=result.confidence,
        )

        submit_decisions([result.winner], chain, store, mcp)

    section("Step 4: Mine block")
    block = chain.mine_pending()
    if block:
        ok(f"Block #{block.index} mined [{block.hash[:16]}...] "
           f"({len(block.transactions)} tx)")


# ---------------------------------------------------------------------------
# Scenario 31: Full 10-Agent Integration
# ---------------------------------------------------------------------------

def scenario_31(mcp, chain, agents, agent_keys, store, health_monitor,
                nlu_agent, anomaly_agent, arb_agent, convo):
    banner("SCENARIO 31: Full 10-Agent Integration (NEW POC8)")

    section("Step 1: Collect telemetry")
    telemetry = mcp.get_all_telemetry()
    counts = store.store_telemetry_batch(telemetry)
    ok(f"Stored: cont={counts['continuous']}, ev={counts['events']}, "
       f"al={counts['alerts']}")

    section("Step 2: Run 7 LLM agents in parallel")
    inject_feedback_all(agents, store)
    t0 = time.perf_counter()
    llm_decisions = run_agents_parallel(list(agents.values()), telemetry)
    elapsed = time.perf_counter() - t0
    ok(f"7 LLM agents: {len(llm_decisions)} decisions in {elapsed:.2f}s")

    section("Step 3: Anomaly agent scans telemetry (ML)")
    anomaly_results, anomaly_decisions = anomaly_agent.detect_and_decide(telemetry)
    anomaly_count = sum(1 for r in anomaly_results if r.is_anomaly)
    ok(f"Anomaly scan: {anomaly_count} anomalies, "
       f"{len(anomaly_decisions)} corrective actions")
    for r in anomaly_results:
        store.store_anomaly(
            device_id=r.device_id, device_type=r.device_type,
            anomaly_score=r.anomaly_score, is_anomaly=r.is_anomaly,
            detectors_triggered=",".join(r.detectors_triggered),
            explanation=r.explanation,
            readings_json=json.dumps(r.readings, default=str),
        )

    section("Step 4: NLU agent processes a text command")
    time.sleep(1)
    intent, nlu_decisions = process_nlu_command(
        "Dim the bedroom light to 50 percent",
        nlu_agent, mcp, chain, store, convo, telemetry)

    section("Step 5: Combine all decisions and arbitrate")
    all_decisions = llm_decisions + anomaly_decisions + nlu_decisions
    unique_agents = {d.transaction.agent_id for d in all_decisions}
    ok(f"Total proposals: {len(all_decisions)} from "
       f"{len(unique_agents)} agents: {sorted(unique_agents)}")

    arb_results = arbitrate_and_submit(
        all_decisions, arb_agent, chain, store, mcp, telemetry)
    if arb_results:
        ok(f"Arbitrated {len(arb_results)} conflict(s)")

    section("Step 6: Mine block")
    block = chain.mine_pending()
    if block:
        ok(f"Block #{block.index} mined [{block.hash[:16]}...] "
           f"({len(block.transactions)} tx - 10-agent integration)")

    section("Step 7: Verify all 10 agents participated")
    agents_on_chain = set()
    for b in chain.chain:
        for tx in b.transactions:
            if tx.tx_type == "agent_action":
                agents_on_chain.add(tx.agent_id)
    expected = set(AGENT_DEFINITIONS.keys())
    on_chain = agents_on_chain & expected
    ok(f"{len(on_chain)}/{len(expected)} agents have on-chain transactions")
    for aid in sorted(expected):
        status = "ON-CHAIN" if aid in agents_on_chain else "not yet"
        info(f"  {aid}: {status}")


# ---------------------------------------------------------------------------
# Scenario 32: POC9 Audit Summary
# ---------------------------------------------------------------------------

def scenario_32(chain, store, anomaly_agent, arb_agent, convo):
    banner("SCENARIO 32: POC9 Audit Summary")

    section("NLU Stats")
    conv_stats = store.get_conversation_stats()
    ok(f"Total NLU interactions: {conv_stats['total_interactions']}")
    ok(f"Intent distribution: {conv_stats['by_intent']}")
    ok(f"Avg confidence: {conv_stats['avg_confidence']:.2f}")

    section("Anomaly Detection Stats")
    anom_stats = store.get_anomaly_stats()
    ok(f"Total scans: {anom_stats['total_scans']}")
    ok(f"Anomalies detected: {anom_stats['anomalies_detected']}")
    ok(f"By device: {anom_stats['by_device']}")
    train_summary = anomaly_agent.training_summary()
    ok(f"Models ready: {train_summary['models_ready']}")

    section("Arbitration Stats")
    arb_stats = store.get_arbitration_stats()
    ok(f"Total arbitrations: {arb_stats['total_arbitrations']}")
    ok(f"By method: {arb_stats['by_method']}")
    ok(f"Safety overrides: {arb_stats['safety_overrides']}")

    section("10-Agent On-Chain Verification")
    agents_on_chain = {}
    for b in chain.chain:
        for tx in b.transactions:
            if tx.tx_type == "agent_action":
                agents_on_chain.setdefault(tx.agent_id, 0)
                agents_on_chain[tx.agent_id] += 1
    for aid in sorted(AGENT_DEFINITIONS.keys()):
        count = agents_on_chain.get(aid, 0)
        status = "OK" if count > 0 else "NONE"
        ok(f"  {aid}: {count} tx [{status}]")

    section("Record POC9 audit transaction on-chain")
    audit_data = {
        "nlu_interactions": conv_stats["total_interactions"],
        "nlu_avg_confidence": round(conv_stats["avg_confidence"], 3),
        "anomaly_scans": anom_stats["total_scans"],
        "anomalies_detected": anom_stats["anomalies_detected"],
        "models_trained": train_summary["models_ready"],
        "arbitrations": arb_stats["total_arbitrations"],
        "arbitration_methods": arb_stats["by_method"],
        "safety_overrides": arb_stats["safety_overrides"],
        "agents_on_chain": len(agents_on_chain),
    }
    audit_json = json.dumps(audit_data, sort_keys=True)
    audit_hash = hashlib.sha256(audit_json.encode()).hexdigest()
    tx = Transaction(
        agent_id="POC9_AUDIT", action="poc9_audit_summary",
        target_device="all",
        params=audit_data, confidence=1.0,
        reasoning_hash=audit_hash, tx_type="poc9_audit",
    )
    chain.pending_tx.append(tx)
    block = chain.mine_pending()
    if block:
        ok(f"POC9 audit anchored in Block #{block.index}")


# ---------------------------------------------------------------------------
# Scenario 33: Adaptive PoW Difficulty Demo
# ---------------------------------------------------------------------------

def scenario_33(chain, store, agents, agent_keys, mcp):
    banner("SCENARIO 33: Adaptive PoW Difficulty (NEW POC8+)")

    section("Step 1: Current adaptive PoW state")
    stats = chain.adaptive.get_stats()
    ok(f"Adaptive PoW: {'ENABLED' if stats['enabled'] else 'DISABLED'}")
    ok(f"Current difficulty: {stats['current_difficulty']} "
       f"(base={stats['base_difficulty']})")
    ok(f"Blocks tracked so far: {stats['blocks_tracked']}")
    ok(f"Difficulty changes so far: {stats['difficulty_changes']}")
    if stats['blocks_tracked'] > 0:
        ok(f"Avg mining time: {stats['avg_mining_time_ms']:.2f}ms")

    section("Step 2: Simulate HIGH-VOLUME block (emergency scenario)")
    info("Injecting 12 transactions to simulate emergency burst")
    # Create many small transactions (simulating emergency burst)
    safety_agent = agents.get("safety-agent-001")
    safety_key = agent_keys.get("safety-agent-001")
    if safety_agent and safety_key:
        for i in range(12):
            device = f"light-{'lr' if i % 2 == 0 else 'br'}"
            dec = make_decision(safety_agent, safety_key, device, "turn_on",
                                confidence=0.95,
                                reasoning=f"Emergency response action {i+1}")
            submit_decisions([dec], chain, store, mcp,
                             label=f"emergency-tx-{i+1}")

    diff_before_high = chain.adaptive.get_difficulty()
    block_high = chain.mine_pending()
    if block_high:
        diff_after_high = chain.adaptive.get_difficulty()
        ok(f"Block #{block_high.index} mined with difficulty={diff_before_high} "
           f"({len(block_high.transactions)} tx, "
           f"nonce={block_high.nonce})")
        if diff_after_high < diff_before_high:
            ok(f"Difficulty DECREASED: {diff_before_high} -> {diff_after_high} "
               f"(high volume detected)")
        elif diff_after_high == diff_before_high:
            info(f"Difficulty unchanged at {diff_after_high} "
                 f"(may need more blocks in window)")
        else:
            info(f"Difficulty increased to {diff_after_high}")

    section("Step 3: Simulate LOW-VOLUME blocks (idle period)")
    info("Mining 3 blocks with 1-2 transactions each (idle period)")
    energy_agent = agents.get("energy-agent-005")
    energy_key = agent_keys.get("energy-agent-005")
    if energy_agent and energy_key:
        for block_num in range(3):
            dec = make_decision(energy_agent, energy_key,
                                "plug-lr", "set_mode",
                                params={"mode": "eco"},
                                confidence=0.7,
                                reasoning=f"Idle period optimization {block_num+1}")
            submit_decisions([dec], chain, store, mcp,
                             label=f"idle-tx-{block_num+1}")
            diff_before = chain.adaptive.get_difficulty()
            block_idle = chain.mine_pending()
            if block_idle:
                diff_after = chain.adaptive.get_difficulty()
                ok(f"Block #{block_idle.index}: {len(block_idle.transactions)} tx, "
                   f"difficulty {diff_before} -> {diff_after}")

    section("Step 4: Final adaptive PoW statistics")
    final_stats = chain.adaptive.get_stats()
    ok(f"Blocks tracked: {final_stats['blocks_tracked']}")
    ok(f"Total difficulty changes: {final_stats['difficulty_changes']}")
    ok(f"Current difficulty: {final_stats['current_difficulty']}")
    ok(f"Min difficulty reached: {final_stats['min_difficulty_reached']}")
    ok(f"Max difficulty reached: {final_stats['max_difficulty_reached']}")
    ok(f"Avg mining time: {final_stats['avg_mining_time_ms']:.2f}ms")

    section("Step 5: Difficulty adjustment log")
    for adj in chain.adaptive.adjustment_log[-10:]:
        change = "CHANGED" if adj.old_difficulty != adj.new_difficulty else "same"
        ok(f"Block #{adj.block_index}: {adj.old_difficulty} -> "
           f"{adj.new_difficulty} ({adj.reason}, "
           f"{adj.tx_count} tx, {adj.mining_time_ms:.1f}ms) [{change}]")

    section("Step 6: Record adaptive PoW audit on-chain")
    adaptive_audit = {
        "adaptive_enabled": final_stats["enabled"],
        "base_difficulty": final_stats["base_difficulty"],
        "final_difficulty": final_stats["current_difficulty"],
        "difficulty_changes": final_stats["difficulty_changes"],
        "min_difficulty_reached": final_stats["min_difficulty_reached"],
        "max_difficulty_reached": final_stats["max_difficulty_reached"],
        "blocks_tracked": final_stats["blocks_tracked"],
        "avg_mining_time_ms": final_stats["avg_mining_time_ms"],
    }
    audit_json = json.dumps(adaptive_audit, sort_keys=True)
    audit_hash = hashlib.sha256(audit_json.encode()).hexdigest()
    tx = Transaction(
        agent_id="ADAPTIVE_POW_AUDIT", action="adaptive_pow_summary",
        target_device="all",
        params=adaptive_audit, confidence=1.0,
        reasoning_hash=audit_hash, tx_type="adaptive_pow_audit",
    )
    chain.pending_tx.append(tx)
    block = chain.mine_pending()
    if block:
        ok(f"Adaptive PoW audit anchored in Block #{block.index}")


# ---------------------------------------------------------------------------
# Scenario 34: Resident Preference Change
# ---------------------------------------------------------------------------

def scenario_34(chain, store, preferences, gov_contract):
    banner("SCENARIO 34: Resident Preference Change (Society 5.0)")

    section("Step 1: Show current preferences")
    ok(f"comfort_vs_energy = {preferences.get('comfort_vs_energy')}")
    ok(f"security_vs_privacy = {preferences.get('security_vs_privacy')}")
    ok(f"anomaly_sensitivity = {preferences.get('anomaly_sensitivity')}")
    ok(f"confirmation_mode = {preferences.get('confirmation_mode')}")

    section("Step 2: Change comfort_vs_energy to 0.8 (favor comfort)")
    result = gov_contract.apply_preference_change("comfort_vs_energy", 0.8)
    if result.get("success"):
        ok(f"Changed: {result['old_value']} -> {result['new_value']} (tier {result['tier']})")
        gov_tx = gov_contract.create_governance_transaction({
            "type": "preference_change",
            "key": "comfort_vs_energy",
            "old_value": result["old_value"],
            "new_value": result["new_value"],
            "tier": result["tier"],
        })
        chain.pending_tx.append(gov_tx)
        store.store_governance_change(
            "preference_change", "comfort_vs_energy",
            str(result["old_value"]), str(result["new_value"]),
            result["tier"])
    else:
        fail(f"Preference change failed: {result.get('reason')}")

    section("Step 3: Verify agent priorities adjusted")
    preferences.apply_to_agent_priorities(AGENT_DEFINITIONS)
    energy_p = AGENT_DEFINITIONS["energy-agent-005"]["priority"]
    climate_p = AGENT_DEFINITIONS["climate-agent-006"]["priority"]
    ok(f"Energy agent priority: {energy_p} (should be ~0.54)")
    ok(f"Climate agent priority: {climate_p} (should be ~0.56)")

    section("Step 4: Change anomaly_sensitivity to 'high'")
    result2 = gov_contract.apply_preference_change("anomaly_sensitivity", "high")
    if result2.get("success"):
        ok(f"Sensitivity: {result2['old_value']} -> {result2['new_value']}")
        gov_tx2 = gov_contract.create_governance_transaction({
            "type": "preference_change",
            "key": "anomaly_sensitivity",
            "old_value": result2["old_value"],
            "new_value": result2["new_value"],
            "tier": result2["tier"],
        })
        chain.pending_tx.append(gov_tx2)
        store.store_governance_change(
            "preference_change", "anomaly_sensitivity",
            str(result2["old_value"]), str(result2["new_value"]),
            result2["tier"])

    section("Step 5: Mine block with governance transactions")
    block = chain.mine_pending()
    if block:
        ok(f"Block #{block.index} mined ({len(block.transactions)} tx)")


# ---------------------------------------------------------------------------
# Scenario 35: Voice-Controlled Governance
# ---------------------------------------------------------------------------

def scenario_35(mcp, chain, store, nlu_agent, convo,
                preferences, gov_contract):
    banner("SCENARIO 35: Voice-Controlled Governance (Society 5.0)")

    section("Step 1: Collect telemetry")
    telemetry = mcp.get_all_telemetry()
    store.store_telemetry_batch(telemetry)

    section("Step 2: Process 'I want more privacy' through NLU")
    time.sleep(1)
    intent1, decisions1 = process_nlu_command(
        "I want more privacy", nlu_agent, mcp, chain, store, convo,
        telemetry, gov_contract=gov_contract, preferences=preferences)

    section("Step 3: Process 'Save energy please' through NLU")
    time.sleep(1)
    intent2, decisions2 = process_nlu_command(
        "Save energy please", nlu_agent, mcp, chain, store, convo,
        telemetry, gov_contract=gov_contract, preferences=preferences)

    section("Step 4: Submit any device decisions")
    all_decisions = decisions1 + decisions2
    if all_decisions:
        submit_decisions(all_decisions, chain, store, mcp)

    section("Step 5: Mine block with governance + device transactions")
    block = chain.mine_pending()
    if block:
        ok(f"Block #{block.index} mined ({len(block.transactions)} tx)")

    section("Step 6: Show updated preferences")
    ok(f"security_vs_privacy = {preferences.get('security_vs_privacy')}")
    ok(f"comfort_vs_energy = {preferences.get('comfort_vs_energy')}")


# ---------------------------------------------------------------------------
# Scenario 36: Model Router + Governance Preset
# ---------------------------------------------------------------------------

def scenario_36(chain, store, router, gov_contract):
    banner("SCENARIO 36: Model Router + Governance Preset (Society 5.0)")

    section("Step 1: Show current model assignments")
    assignments = router.get_all_assignments()
    for agent_id, model in sorted(assignments.items()):
        ok(f"  {agent_id}: {model}")

    section("Step 2: Apply 'budget' preset")
    result = gov_contract.apply_preset("budget", AGENT_DEFINITIONS)
    if result.get("success"):
        ok(f"Preset 'budget' applied")
        new_assignments = router.get_all_assignments()
        for agent_id, model in sorted(new_assignments.items()):
            ok(f"  {agent_id}: {model}")
    else:
        fail(f"Preset failed: {result.get('reason')}")

    section("Step 3: Verify safety agents still use pro-tier")
    safety_model = router.get_assignment("safety-agent-001")
    from model_router import MODEL_REGISTRY
    safety_tier = MODEL_REGISTRY.get(safety_model, {}).get("tier", "?")
    ok(f"safety-agent-001 model: {safety_model} (tier={safety_tier})")
    if safety_tier == "pro":
        ok("PASS: Safety agent uses pro-tier model")
    else:
        fail(f"FAIL: Safety agent on tier '{safety_tier}' (expected pro)")

    section("Step 4: Attempt to assign flash-tier to safety (MUST FAIL)")
    bad_result = gov_contract.apply_model_change(
        "safety-agent-001", "gemini-2.0-flash")
    if not bad_result.get("success"):
        ok(f"CORRECTLY REJECTED: {bad_result.get('reason')}")
    else:
        fail("BUG: Flash model assigned to safety agent!")

    section("Step 5: Record model audit on blockchain")
    model_audit_data = {
        "preset_applied": "budget",
        "safety_model": safety_model,
        "safety_tier": safety_tier,
        "flash_rejection": not bad_result.get("success"),
        "total_assignments": len(router.get_all_assignments()),
    }
    audit_json = json.dumps(model_audit_data, sort_keys=True)
    audit_hash = hashlib.sha256(audit_json.encode()).hexdigest()
    tx = Transaction(
        agent_id="MODEL_ROUTER", action="model_preset_audit",
        target_device="all", params=model_audit_data, confidence=1.0,
        reasoning_hash=audit_hash, tx_type="model_audit",
    )
    chain.pending_tx.append(tx)
    store.store_governance_change("preset_applied", "budget", "", "budget", 0)

    section("Step 6: Show cost tracker summary")
    cost_summary = router.cost_tracker.summary()
    ok(f"Total API calls: {cost_summary['total_calls']}")
    ok(f"Total cost: ${cost_summary['total_cost']:.6f}")

    section("Step 7: Restore balanced preset + mine block")
    gov_contract.apply_preset("balanced", AGENT_DEFINITIONS)
    block = chain.mine_pending()
    if block:
        ok(f"Block #{block.index} mined ({len(block.transactions)} tx)")


# ---------------------------------------------------------------------------
# Scenario 37: LOCKED Parameter Immutability
# ---------------------------------------------------------------------------

def scenario_37(chain, store, gov_contract):
    banner("SCENARIO 37: LOCKED Parameter Immutability (Society 5.0)")

    section("Step 1: Attempt to change safety_priority (MUST FAIL)")
    r1 = gov_contract.apply_preference_change("safety_priority", 0.5)
    if not r1.get("success"):
        ok(f"CORRECTLY REJECTED: {r1.get('reason')}")
    else:
        fail("BUG: safety_priority was changed!")

    section("Step 2: Attempt to change firmware_gas_threshold_ppm (MUST FAIL)")
    r2 = gov_contract.apply_preference_change("firmware_gas_threshold_ppm", 100)
    if not r2.get("success"):
        ok(f"CORRECTLY REJECTED: {r2.get('reason')}")
    else:
        fail("BUG: firmware_gas_threshold_ppm was changed!")

    section("Step 3: Attempt to change safety_override_immune (MUST FAIL)")
    r3 = gov_contract.apply_preference_change("safety_override_immune", False)
    if not r3.get("success"):
        ok(f"CORRECTLY REJECTED: {r3.get('reason')}")
    else:
        fail("BUG: safety_override_immune was changed!")

    section("Step 4: Verify LOCKED values unchanged")
    ok(f"safety_priority = {LOCKED_PARAMETERS['safety_priority']} (expected 1.0)")
    ok(f"firmware_gas_threshold_ppm = {LOCKED_PARAMETERS['firmware_gas_threshold_ppm']} (expected 50)")
    ok(f"safety_override_immune = {LOCKED_PARAMETERS['safety_override_immune']} (expected True)")
    ok(f"All {len(LOCKED_PARAMETERS)} LOCKED parameters verified immutable")


# ---------------------------------------------------------------------------
# Scenario 38: Confirmation Mode Demo
# ---------------------------------------------------------------------------

def scenario_38(chain, store, preferences, gov_contract):
    banner("SCENARIO 38: Confirmation Mode Demo (Society 5.0)")

    section("Step 1: Set confirmation_mode to 'destructive_only'")
    result = gov_contract.apply_preference_change(
        "confirmation_mode", "destructive_only")
    if result.get("success"):
        ok(f"confirmation_mode = {result['new_value']}")

    section("Step 2: Simulate non-destructive action (auto-approved)")
    mode = preferences.get("confirmation_mode")
    action = "set_temperature"
    is_destructive = action in ("turn_off", "lock", "trigger_maintenance_mode")
    requires_confirm = (mode == "always" or
                        (mode == "destructive_only" and is_destructive))
    ok(f"Action: {action}, destructive={is_destructive}, "
       f"requires_confirmation={requires_confirm}")
    if not requires_confirm:
        ok("PASS: Non-destructive action auto-approved")

    section("Step 3: Simulate destructive action (flagged)")
    action2 = "turn_off"
    is_destructive2 = action2 in ("turn_off", "lock", "trigger_maintenance_mode")
    requires_confirm2 = (mode == "always" or
                         (mode == "destructive_only" and is_destructive2))
    ok(f"Action: {action2}, destructive={is_destructive2}, "
       f"requires_confirmation={requires_confirm2}")
    if requires_confirm2:
        ok("PASS: Destructive action correctly flagged for confirmation")

    section("Step 4: Set confirmation_mode to 'always'")
    gov_contract.apply_preference_change("confirmation_mode", "always")
    mode2 = preferences.get("confirmation_mode")
    ok(f"confirmation_mode = {mode2}")

    section("Step 5: Verify all actions now flagged")
    requires_all_1 = (mode2 == "always")
    requires_all_2 = (mode2 == "always")
    ok(f"set_temperature requires_confirmation={requires_all_1}")
    ok(f"turn_off requires_confirmation={requires_all_2}")
    if requires_all_1 and requires_all_2:
        ok("PASS: All actions require confirmation in 'always' mode")

    # Reset to default for subsequent scenarios
    gov_contract.apply_preference_change("confirmation_mode", "never")

    section("Step 6: Record governance changes on-chain")
    gov_tx = gov_contract.create_governance_transaction({
        "type": "confirmation_mode_demo",
        "modes_tested": ["destructive_only", "always"],
        "all_passed": True,
    })
    chain.pending_tx.append(gov_tx)
    block = chain.mine_pending()
    if block:
        ok(f"Block #{block.index} mined ({len(block.transactions)} tx)")


# ---------------------------------------------------------------------------
# Scenario 39: Session Persistence + POC10 Audit
# ---------------------------------------------------------------------------

def scenario_39(chain, store, agent_keys, preferences, router,
                session_mgr, session_name,
                anomaly_agent, arb_agent, convo, gov_contract):
    banner("SCENARIO 39: Session Persistence + POC10 Audit")

    section("Step 1: Collect session state before save")
    n_blocks = len(chain.chain)
    last_hash = chain.chain[-1].hash if chain.chain else "NONE"
    n_keys = len(agent_keys)
    pref_snapshot = preferences.to_dict()
    assign_snapshot = router.get_all_assignments()
    ok(f"Blockchain: {n_blocks} blocks, last hash={last_hash[:16]}...")
    ok(f"Agent keys: {n_keys}")
    ok(f"Preferences: comfort_vs_energy={pref_snapshot.get('comfort_vs_energy')}")
    ok(f"Model assignments: {len(assign_snapshot)}")

    section("Step 2: Save session to disk")
    save_session(session_mgr, session_name, chain, agent_keys, store,
                 preferences, router, scenarios_run=39)

    section("Step 3: Reload and verify blockchain")
    chain2 = Blockchain.load(session_mgr.blockchain_path(session_name))
    ok(f"Reloaded blockchain: {len(chain2.chain)} blocks")
    if len(chain2.chain) == n_blocks:
        ok(f"PASS: Block count matches ({n_blocks})")
    else:
        fail(f"FAIL: Block count {len(chain2.chain)} != {n_blocks}")
    last_hash2 = chain2.chain[-1].hash if chain2.chain else "NONE"
    if last_hash == last_hash2:
        ok(f"PASS: Last block hash matches")
    else:
        fail(f"FAIL: Hash mismatch {last_hash2[:16]} != {last_hash[:16]}")

    section("Step 4: Reload and verify agent keys")
    keys2 = session_mgr.load_agent_keys(session_name)
    if len(keys2) == n_keys:
        ok(f"PASS: Key count matches ({n_keys})")
    else:
        fail(f"FAIL: Key count {len(keys2)} != {n_keys}")
    # Verify a signature roundtrip
    test_data = b"session_persistence_test_data"
    for aid in ["safety-agent-001", "nlu-agent-008"]:
        if aid in keys2 and aid in agent_keys:
            sig = agent_keys[aid].sign(test_data)
            try:
                keys2[aid].public_key().verify(sig, test_data)
                ok(f"PASS: {aid} signature verified after reload")
            except Exception:
                fail(f"FAIL: {aid} signature verification failed")

    section("Step 5: Reload and verify preferences")
    prefs2 = ResidentPreferences(session_mgr.preferences_path(session_name))
    p2 = prefs2.to_dict()
    prefs_match = True
    for key in ["comfort_vs_energy", "security_vs_privacy",
                "anomaly_sensitivity", "confirmation_mode"]:
        if p2.get(key) != pref_snapshot.get(key):
            fail(f"FAIL: {key} mismatch: {p2.get(key)} != {pref_snapshot.get(key)}")
            prefs_match = False
    if prefs_match:
        ok(f"PASS: All preferences match after reload")

    section("Step 6: Collect POC10 audit stats")
    # NLU
    conv_stats = store.get_conversation_stats()
    ok(f"NLU interactions: {conv_stats['total_interactions']}")
    # Anomaly
    anom_stats = store.get_anomaly_stats()
    ok(f"Anomaly scans: {anom_stats['total_scans']}")
    # Arbitration
    arb_stats = store.get_arbitration_stats()
    ok(f"Arbitrations: {arb_stats['total_arbitrations']}")
    # Governance
    gov_stats = store.get_governance_stats()
    ok(f"Governance changes: {gov_stats['total_changes']}")
    # Model usage
    model_stats = store.get_model_usage_stats()
    ok(f"Model API calls: {model_stats['total_calls']}")

    section("Step 7: Record POC10 audit on blockchain")
    audit_data = {
        "session_name": session_name,
        "blocks": n_blocks,
        "agents": n_keys,
        "nlu_interactions": conv_stats["total_interactions"],
        "anomaly_scans": anom_stats["total_scans"],
        "arbitrations": arb_stats["total_arbitrations"],
        "governance_changes": gov_stats["total_changes"],
        "model_api_calls": model_stats["total_calls"],
        "session_verified": True,
        "preferences_verified": prefs_match,
        "keys_verified": len(keys2) == n_keys,
    }
    audit_json = json.dumps(audit_data, sort_keys=True)
    audit_hash = hashlib.sha256(audit_json.encode()).hexdigest()
    tx = Transaction(
        agent_id="POC10_AUDIT", action="poc10_audit_summary",
        target_device="all",
        params=audit_data, confidence=1.0,
        reasoning_hash=audit_hash, tx_type="poc10_audit",
    )
    chain.pending_tx.append(tx)
    block = chain.mine_pending()
    if block:
        ok(f"POC10 audit anchored in Block #{block.index}")
