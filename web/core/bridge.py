"""
Bridge -- Connects the web layer to core engine modules.

Wraps setup logic for the web context (no CLI output, inprocess MCP,
home data from HES-agent local store).

All created objects are stored in the AppState singleton.
"""

from __future__ import annotations

import os
from typing import Any

from loguru import logger

# Engine core imports
from engine.config import (
    GEMINI_API_KEY, GEMINI_MODEL, DIFFICULTY, API_KEYS,
    AGENT_DEFINITIONS, SESSIONS_DIR,
    MCP_SERVER_NAME,
    DL_ANOMALY_ENABLED, ANOMALY_THRESHOLD, ANOMALY_ZSCORE_THRESHOLD,
    SAFETY_OVERRIDE_IMMUNE,
    ADAPTIVE_DIFFICULTY_ENABLED, DIFFICULTY_MIN, DIFFICULTY_MAX,
    DIFFICULTY_TX_VOLUME_LOW, DIFFICULTY_TX_VOLUME_HIGH,
    DIFFICULTY_ADJUSTMENT_WINDOW,
    DEFAULT_GOVERNANCE_PRESET, CONVERSATION_HISTORY_SIZE,
    agent_has_device_access,
)
from engine.blockchain import Blockchain, generate_keypair
from engine.agent import SmartHomeAgent, AgentRole
import sqlite3 as _sqlite3
from engine.offchain import OffChainStore
from engine.mcp_server import init_server, get_device_layer
from engine.mcp_server import mcp as mcp_server_instance
from engine.mcp_client import create_mcp_client
from engine.health import MCPHealthMonitor
from engine.nlu_agent import NLUAgent
from engine.anomaly_agent import AnomalyDetectionAgent
from engine.arbitration_agent import ArbitrationAgent
from engine.conversation import ConversationManager
from engine.session_manager import SessionManager
from engine.resident_preferences import ResidentPreferences
from engine.model_router import ModelRouter
from engine.governance_contract import GovernanceContract

from web.core.state import AppState, get_app_state, reset_app_state
from web.core.home_adapter import create_device_layer_from_home, create_device_layer_with_config


def _enable_crossthread_sqlite(store: OffChainStore) -> None:
    """Reopen the store's SQLite connection with check_same_thread=False.

    Needed for the web context where the connection is created in one thread
    (main / fixture) but used from another (ASGI event loop / thread pool).
    The engine is single-writer so cross-thread access is safe.
    """
    db_path = store.db_path
    store.conn.close()
    store.conn = _sqlite3.connect(db_path, check_same_thread=False)
    store.conn.row_factory = _sqlite3.Row


def _get_session_manager() -> SessionManager:
    """Get or create the SessionManager using the configured sessions dir."""
    import engine
    engine_dir = os.path.dirname(os.path.abspath(engine.__file__))
    return SessionManager(engine_dir)


def _init_mcp(device_layer) -> Any:
    """Initialize MCP server + client in inprocess mode (web context)."""
    init_server(device_layer)
    mcp = create_mcp_client("inprocess", server=mcp_server_instance)
    devices = mcp.list_devices()
    logger.info(f"MCP Server (inprocess): {len(devices)} devices registered")
    return mcp


def _init_blockchain() -> Blockchain:
    """Create a fresh blockchain with adaptive PoW configuration."""
    chain = Blockchain(
        DIFFICULTY,
        adaptive_enabled=ADAPTIVE_DIFFICULTY_ENABLED,
        adaptive_min=DIFFICULTY_MIN,
        adaptive_max=DIFFICULTY_MAX,
        adaptive_tx_low=DIFFICULTY_TX_VOLUME_LOW,
        adaptive_tx_high=DIFFICULTY_TX_VOLUME_HIGH,
        adaptive_window=DIFFICULTY_ADJUSTMENT_WINDOW,
    )
    logger.info(
        f"Blockchain initialized (adaptive PoW: "
        f"{'ENABLED' if ADAPTIVE_DIFFICULTY_ENABLED else 'DISABLED'}, "
        f"difficulty={DIFFICULTY}, range=[{DIFFICULTY_MIN}-{DIFFICULTY_MAX}])"
    )
    return chain


def _init_governance(
    session_mgr: SessionManager | None,
    session_name: str,
) -> tuple[ResidentPreferences, ModelRouter, GovernanceContract]:
    """Initialize governance layer (preferences, model router, contract)."""
    prefs_path = ""
    model_path = ""
    if session_mgr and session_name:
        prefs_path = session_mgr.preferences_path(session_name)
        model_path = session_mgr.model_assignments_path(session_name)

    preferences = ResidentPreferences(prefs_path)
    router = ModelRouter(api_keys=API_KEYS)
    router.apply_preset(DEFAULT_GOVERNANCE_PRESET, AGENT_DEFINITIONS)
    if model_path:
        router.save_assignments(model_path)
    gov_contract = GovernanceContract(preferences, router)
    preferences.apply_to_agent_priorities(AGENT_DEFINITIONS)

    logger.info(f"Governance initialized: preset={DEFAULT_GOVERNANCE_PRESET}")
    return preferences, router, gov_contract


def _init_agents(
    chain: Blockchain,
    router: ModelRouter,
    session_mgr: SessionManager | None,
    session_name: str,
) -> tuple[dict[str, Any], dict[str, Any], NLUAgent, AnomalyDetectionAgent, ArbitrationAgent]:
    """Create all 10 agents with keypairs, register with blockchain."""
    agents = {}
    agent_keys = {}
    dl = get_device_layer()

    # Generate keys and register all 10 agents
    for agent_id, defn in AGENT_DEFINITIONS.items():
        privkey, pubkey = generate_keypair()
        agent_keys[agent_id] = privkey
        chain.registry.register(agent_id, pubkey)
        chain.priorities.set_priority(agent_id, defn["priority"])

        # Grant permissions per allowed device types
        for dev_id, dev in dl.devices.items():
            if agent_has_device_access(defn, dev.device_type):
                chain.permissions.grant(agent_id, dev_id, "*")

    # Save agent keys to session
    if session_mgr and session_name:
        session_mgr.save_agent_keys(session_name, agent_keys)

    # Instantiate the 7 LLM agents
    for agent_id, defn in AGENT_DEFINITIONS.items():
        if defn["role"] in ("nlu", "anomaly", "arbitration"):
            continue
        role = AgentRole(defn["role"])
        model_name = defn.get("model", GEMINI_MODEL)
        agent = SmartHomeAgent(
            agent_id, agent_keys[agent_id], role,
            GEMINI_API_KEY, model_name, model_router=router,
        )
        agents[agent_id] = agent

    # Specialized agents
    nlu_agent = NLUAgent(
        "nlu-agent-008", agent_keys["nlu-agent-008"],
        api_key=GEMINI_API_KEY,
        model_name=AGENT_DEFINITIONS["nlu-agent-008"]["model"],
        model_router=router,
    )
    anomaly_agent = AnomalyDetectionAgent(
        "anomaly-agent-009", agent_keys["anomaly-agent-009"],
        dl_enabled=DL_ANOMALY_ENABLED,
        iforest_threshold=ANOMALY_THRESHOLD,
        zscore_threshold=ANOMALY_ZSCORE_THRESHOLD,
    )
    arb_agent = ArbitrationAgent(
        "arbitration-agent-010", agent_keys["arbitration-agent-010"],
        api_key=GEMINI_API_KEY,
        model_name=AGENT_DEFINITIONS["arbitration-agent-010"]["model"],
        safety_immune=SAFETY_OVERRIDE_IMMUNE,
        model_router=router,
    )

    logger.info(f"All {len(AGENT_DEFINITIONS)} agents initialized")
    return agents, agent_keys, nlu_agent, anomaly_agent, arb_agent


def _resume_agents(
    chain: Blockchain,
    agent_keys: dict[str, Any],
    router: ModelRouter,
) -> tuple[dict[str, Any], NLUAgent, AnomalyDetectionAgent, ArbitrationAgent]:
    """Reconstruct agents from loaded keys for a resumed session."""
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    agents = {}
    dl = get_device_layer()

    # Re-register agents with blockchain
    for agent_id, privkey in agent_keys.items():
        pubkey = privkey.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        defn = AGENT_DEFINITIONS.get(agent_id)
        if not defn:
            continue
        if agent_id not in chain.registry._agents:
            chain.registry.register(agent_id, pubkey)
        chain.priorities.set_priority(agent_id, defn["priority"])
        for dev_id, dev in dl.devices.items():
            if agent_has_device_access(defn, dev.device_type):
                chain.permissions.grant(agent_id, dev_id, "*")

    # Instantiate 7 LLM agents
    for agent_id, defn in AGENT_DEFINITIONS.items():
        if defn["role"] in ("nlu", "anomaly", "arbitration"):
            continue
        if agent_id not in agent_keys:
            continue
        role = AgentRole(defn["role"])
        model_name = defn.get("model", GEMINI_MODEL)
        agent = SmartHomeAgent(
            agent_id, agent_keys[agent_id], role,
            GEMINI_API_KEY, model_name, model_router=router,
        )
        agents[agent_id] = agent

    # Specialized agents
    nlu_agent = NLUAgent(
        "nlu-agent-008", agent_keys["nlu-agent-008"],
        api_key=GEMINI_API_KEY,
        model_name=AGENT_DEFINITIONS["nlu-agent-008"]["model"],
        model_router=router,
    )
    anomaly_agent = AnomalyDetectionAgent(
        "anomaly-agent-009", agent_keys["anomaly-agent-009"],
        dl_enabled=DL_ANOMALY_ENABLED,
        iforest_threshold=ANOMALY_THRESHOLD,
        zscore_threshold=ANOMALY_ZSCORE_THRESHOLD,
    )
    arb_agent = ArbitrationAgent(
        "arbitration-agent-010", agent_keys["arbitration-agent-010"],
        api_key=GEMINI_API_KEY,
        model_name=AGENT_DEFINITIONS["arbitration-agent-010"]["model"],
        safety_immune=SAFETY_OVERRIDE_IMMUNE,
        model_router=router,
    )

    logger.info(f"All agents reconstructed from loaded keys")
    return agents, nlu_agent, anomaly_agent, arb_agent


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def setup_fresh_session(
    session_name: str,
    home_devices: list[dict[str, Any]],
    home_rooms: list[dict[str, Any]] | None = None,
    home_config: dict[str, Any] | None = None,
    device_config: Any = None,
) -> tuple[AppState, dict[str, Any]]:
    """Create a brand-new session from home configuration data.

    Uses the home adapter to create a DeviceLayer from S5-HES-Agent
    device data.

    In REAL or HYBRID mode, device_config specifies which devices
    use protocol adapters (MQTT, HTTP) instead of S5-HES simulation.

    Args:
        session_name: Name for the new session.
        home_devices: Device list from GET /api/simulation/home/devices.
        home_rooms: Room list from GET /api/simulation/home/rooms.
        home_config: Full home config to snapshot in session directory.
        device_config: SessionDeviceConfig for dual-mode support.

    Returns:
        The populated AppState singleton.
    """
    logger.info(f"Creating fresh session: {session_name}")

    # Save S5-HES references before reset (they live on the old AppState)
    old_state = get_app_state()
    s5_client = old_state.s5_hes_client
    s5_available = old_state.s5_hes_available

    # Reset application state
    state = reset_app_state()
    state.session_name = session_name
    state.is_fresh = True
    state.home_config = home_config or {}
    state.device_config = device_config

    # Restore S5-HES references on the new state
    state.s5_hes_client = s5_client
    state.s5_hes_available = s5_available

    # Session manager
    session_mgr = _get_session_manager()
    _device_mode = "simulation"
    if device_config is not None and hasattr(device_config, 'mode'):
        _device_mode = device_config.mode.value
    session_mgr.create_session(session_name, device_mode=_device_mode)
    state.session_mgr = session_mgr

    # Layer 1: Device Layer (mode-aware — simulation, real, or hybrid)
    device_layer, mapping_report = create_device_layer_with_config(
        home_devices, home_rooms, device_config
    )
    mode_label = mapping_report.get("mode", "simulation")
    logger.info(
        f"Device layer created ({mode_label}): "
        f"{mapping_report['mapped_devices']} mapped, "
        f"{mapping_report.get('real_devices', 0)} real, "
        f"{mapping_report['skipped_devices']} skipped"
    )

    # Save home config snapshot in session directory
    if home_config:
        import json
        config_path = os.path.join(
            session_mgr.session_dir(session_name), "home_config.json"
        )
        with open(config_path, "w") as f:
            json.dump(home_config, f, indent=2)

    # Save device config snapshot (dual-mode support)
    if device_config is not None:
        import json as _json_dc
        dc_path = os.path.join(
            session_mgr.session_dir(session_name), "device_config.json"
        )
        with open(dc_path, "w") as f:
            _json_dc.dump(device_config.to_dict(), f, indent=2)
        logger.info(f"Device config saved: mode={device_config.mode.value}")

    # Layer 1: MCP
    state.mcp = _init_mcp(device_layer)

    # Layer 2: Blockchain
    state.chain = _init_blockchain()

    # Governance
    state.preferences, state.model_router, state.gov_contract = _init_governance(
        session_mgr, session_name
    )

    # Layer 3: Agents
    (state.agents, agent_keys, state.nlu_agent,
     state.anomaly_agent, state.arb_agent) = _init_agents(
        state.chain, state.model_router, session_mgr, session_name
    )
    state.agent_keys = agent_keys

    # Conversation manager (no voice in web context)
    state.convo = ConversationManager(max_turns=CONVERSATION_HISTORY_SIZE)

    # Off-chain store (cross-thread enabled for web context)
    offchain_path = session_mgr.offchain_path(session_name)
    state.store = OffChainStore(offchain_path)
    _enable_crossthread_sqlite(state.store)
    logger.info(f"Off-chain store: {offchain_path}")

    # Health monitor
    state.health_monitor = MCPHealthMonitor(state.mcp)

    # HES telemetry sync (bridges S5-HES data into DeviceLayer)
    from web.core.hes_telemetry_sync import HESTelemetrySync
    state.hes_sync = HESTelemetrySync()

    # Mark active
    state.is_active = True

    logger.info(
        f"Session '{session_name}' created: "
        f"{mapping_report['mapped_devices']} devices, "
        f"{len(state.agents) + 3} agents, "  # +3 for nlu, anomaly, arb
        f"blockchain genesis block ready"
    )

    return state, mapping_report


def setup_resume_session(session_name: str) -> AppState:
    """Resume an existing session from disk.

    Resume an existing session from saved state.
    Device layer is recreated from saved home_config.json snapshot.

    Args:
        session_name: Name of the session to resume.

    Returns:
        The populated AppState singleton.
    """
    logger.info(f"Resuming session: {session_name}")

    session_mgr = _get_session_manager()
    if not session_mgr.session_exists(session_name):
        raise ValueError(f"Session '{session_name}' does not exist")

    # Save S5-HES references before reset (they live on the old AppState)
    old_state = get_app_state()
    s5_client = old_state.s5_hes_client
    s5_available = old_state.s5_hes_available

    # Reset application state
    state = reset_app_state()
    state.session_name = session_name
    state.is_fresh = False
    state.session_mgr = session_mgr

    # Restore S5-HES references on the new state
    state.s5_hes_client = s5_client
    state.s5_hes_available = s5_available

    # Load home config snapshot
    import json
    config_path = os.path.join(
        session_mgr.session_dir(session_name), "home_config.json"
    )
    if os.path.isfile(config_path):
        with open(config_path, "r") as f:
            state.home_config = json.load(f)

    # Clear in-memory stores before restoring (prevent stale cross-session data)
    from web.core.home_store import get_home_store
    from web.core.threat_store import get_threat_store, ThreatConfig
    get_home_store().clear()
    get_threat_store().clear()

    # Restore HomeStore from the loaded config so UI pages can display it
    if state.home_config:
        get_home_store().restore_from_dict(state.home_config)

    # Restore ThreatStore from saved threat config
    threat_path = os.path.join(
        session_mgr.session_dir(session_name), "threat_config.json"
    )
    if os.path.isfile(threat_path):
        with open(threat_path, "r") as f:
            threat_list = json.load(f)
        ts = get_threat_store()
        for t in threat_list:
            ts.add_threat(ThreatConfig(
                id=t["id"], name=t["name"], threat_type=t["threat_type"],
                target_device=t.get("target_device", ""),
                severity=t.get("severity", "medium"),
                parameters=t.get("parameters", {}),
            ))
        logger.info(f"ThreatStore restored: {len(threat_list)} threats")

    # Load device config snapshot (dual-mode support)
    dc_path = os.path.join(
        session_mgr.session_dir(session_name), "device_config.json"
    )
    if os.path.isfile(dc_path):
        with open(dc_path, "r") as f:
            dc_data = json.load(f)
        from engine.device_config import SessionDeviceConfig
        state.device_config = SessionDeviceConfig.from_dict(dc_data)
        logger.info(f"Device config restored: mode={state.device_config.mode.value}")

    # Layer 1: Recreate device layer from snapshot (mode-aware)
    home_devices = state.home_config.get("devices", [])
    home_rooms = state.home_config.get("rooms", [])

    # Real/hybrid mode: device_config has the real device list — use it
    # even when home_devices is empty (real-mode sessions have no simulated devices)
    has_real_config = (
        state.device_config is not None
        and hasattr(state.device_config, 'mode')
        and state.device_config.mode.value in ("real", "hybrid")
    )

    if not home_devices and not has_real_config:
        raise RuntimeError(
            "Cannot resume session: no HES home snapshot found and no real "
            "device config. Sessions require S5-HES-Agent home data."
        )

    device_layer, mapping_report = create_device_layer_with_config(
        home_devices, home_rooms, state.device_config
    )
    mode_label = mapping_report.get("mode", "simulation")
    logger.info(
        f"Device layer restored ({mode_label}): "
        f"{mapping_report['mapped_devices']} devices"
    )

    # Layer 1: MCP
    state.mcp = _init_mcp(device_layer)

    # Layer 2: Blockchain (load from session)
    bc_path = session_mgr.blockchain_path(session_name)
    state.chain = Blockchain.load(bc_path)
    logger.info(
        f"Blockchain loaded: {len(state.chain.chain)} blocks"
    )

    # Agent keys (load from session)
    agent_keys = session_mgr.load_agent_keys(session_name)
    state.agent_keys = agent_keys
    logger.info(f"Loaded {len(agent_keys)} agent keys")

    # Governance (load from session)
    state.preferences = ResidentPreferences(
        session_mgr.preferences_path(session_name)
    )
    state.model_router = ModelRouter(api_keys=API_KEYS)
    ma_path = session_mgr.model_assignments_path(session_name)
    state.model_router.load_assignments(ma_path)
    state.gov_contract = GovernanceContract(
        state.preferences, state.model_router
    )
    state.preferences.apply_to_agent_priorities(AGENT_DEFINITIONS)

    # Agents (reconstruct from loaded keys)
    (state.agents, state.nlu_agent,
     state.anomaly_agent, state.arb_agent) = _resume_agents(
        state.chain, agent_keys, state.model_router
    )

    # Conversation manager
    state.convo = ConversationManager(max_turns=CONVERSATION_HISTORY_SIZE)

    # Off-chain store (cross-thread enabled for web context)
    offchain_path = session_mgr.offchain_path(session_name)
    state.store = OffChainStore(offchain_path)
    _enable_crossthread_sqlite(state.store)

    # Health monitor
    state.health_monitor = MCPHealthMonitor(state.mcp)

    # HES telemetry sync (bridges S5-HES data into DeviceLayer)
    from web.core.hes_telemetry_sync import HESTelemetrySync
    state.hes_sync = HESTelemetrySync()

    # Mark active
    state.is_active = True

    logger.info(f"Session '{session_name}' resumed successfully")
    return state


def save_current_session() -> dict[str, Any]:
    """Save the current active session to disk.

    Returns summary of what was saved.
    """
    state = get_app_state()
    if not state.is_active:
        raise RuntimeError("No active session to save")

    session_mgr = state.session_mgr
    session_name = state.session_name
    if not session_mgr or not session_name:
        raise RuntimeError("Session manager or name not set")

    # Save blockchain
    state.chain.save(session_mgr.blockchain_path(session_name))

    # Save agent keys
    session_mgr.save_agent_keys(session_name, state.agent_keys)

    # Save preferences
    state.preferences.save(session_mgr.preferences_path(session_name))

    # Save model assignments
    state.model_router.save_assignments(
        session_mgr.model_assignments_path(session_name)
    )

    # Save threat configuration
    from web.core.threat_store import get_threat_store
    from dataclasses import asdict
    threat_store = get_threat_store()
    threats = threat_store.get_threats()
    if threats:
        import json as _json
        threat_path = os.path.join(
            session_mgr.session_dir(session_name), "threat_config.json"
        )
        with open(threat_path, "w") as f:
            _json.dump([asdict(t) for t in threats], f, indent=2)

    # Update metadata
    blocks = len(state.chain.chain)
    session_mgr.update_meta(session_name, blocks=blocks)

    logger.info(f"Session '{session_name}' saved ({blocks} blocks)")
    return {
        "session_name": session_name,
        "blocks_saved": blocks,
        "agents_saved": len(state.agent_keys),
    }


def teardown_session() -> None:
    """Deactivate the current session (does not delete from disk)."""
    state = get_app_state()
    if state.is_active:
        logger.info(f"Tearing down session: {state.session_name}")
    # Disconnect real-device adapters (MQTT, HTTP) before resetting state
    try:
        dl = get_device_layer()
        if dl is not None:
            dl.shutdown_all()
    except Exception:
        pass
    # Close SQLite connection before resetting state (prevents Windows file locks)
    if state.store is not None:
        try:
            state.store.close()
        except Exception:
            pass
    # Clear in-memory stores (they are separate singletons, not part of AppState)
    from web.core.home_store import get_home_store
    from web.core.threat_store import get_threat_store
    get_home_store().clear()
    get_threat_store().clear()

    # Preserve S5-HES references (they outlive individual sessions)
    s5_client = state.s5_hes_client
    s5_available = state.s5_hes_available
    new_state = reset_app_state()
    new_state.s5_hes_client = s5_client
    new_state.s5_hes_available = s5_available
