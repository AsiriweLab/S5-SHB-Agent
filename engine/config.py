"""Configuration for POC10 (Session Persistence + Society 5.0 Governance -- 10 Agents).

All user-adjustable settings are loaded from .env (flat values) and
models.json (structured model config).  If these files are missing,
hardcoded defaults are used for backward compatibility.
"""

import os
import sys

# ---------------------------------------------------------------------------
# .env Loader (no external dependency)
# ---------------------------------------------------------------------------

_POC_DIR = os.path.dirname(os.path.abspath(__file__))

# Allow --config <dir> to override where .env and models.json are loaded from.
# Parsed early (before argparse) so module-level config picks it up.
_CONFIG_DIR = _POC_DIR
for _i, _arg in enumerate(sys.argv):
    if _arg == "--config" and _i + 1 < len(sys.argv):
        _candidate = sys.argv[_i + 1]
        if os.path.isdir(_candidate):
            _CONFIG_DIR = os.path.abspath(_candidate)
        break


def _load_env(env_path):
    """Parse .env file into dict. Handles comments (#), empty lines, KEY=VALUE."""
    env = {}
    if not os.path.isfile(env_path):
        return env
    with open(env_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip()
    return env


def _bool(val, default):
    """Convert string to bool with fallback."""
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.lower() in ("true", "1", "yes")
    return default


def _float(val, default):
    """Convert string to float with fallback."""
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _int(val, default):
    """Convert string to int with fallback."""
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


# Load .env (from --config dir if specified, else POC_DIR)
_ENV_PATH = os.path.join(_CONFIG_DIR, ".env")
_env = _load_env(_ENV_PATH)

# ---------------------------------------------------------------------------
# API Keys (from .env -- NEVER hardcode in source)
# ---------------------------------------------------------------------------
GEMINI_API_KEY = _env.get("GOOGLE_API_KEY", "")
GEMINI_MODEL = _env.get("DEFAULT_MODEL", "gemini-2.0-flash")

API_KEYS = {
    "google": GEMINI_API_KEY,
    "anthropic": _env.get("ANTHROPIC_API_KEY", ""),
    "openai": _env.get("OPENAI_API_KEY", ""),
}

# ---------------------------------------------------------------------------
# Core Settings (from .env with hardcoded defaults)
# ---------------------------------------------------------------------------
DIFFICULTY = _int(_env.get("DIFFICULTY"), 2)
CONFIDENCE_THRESHOLD = _float(_env.get("CONFIDENCE_THRESHOLD"), 0.6)
ANCHOR_BATCH_SIZE = _int(_env.get("ANCHOR_BATCH_SIZE"), 50)

# ---------------------------------------------------------------------------
# Paths (derived from code location -- not user-configurable)
# ---------------------------------------------------------------------------
DATA_DIR = os.path.join(_POC_DIR, "data")
BLOCKCHAIN_FILE = os.path.join(DATA_DIR, "blockchain.json")
OFFCHAIN_DB = os.path.join(DATA_DIR, "offchain.db")
SESSIONS_DIR = os.path.join(_POC_DIR, "sessions")
MODELS_CONFIG_FILE = os.path.join(_CONFIG_DIR, "models.json")
DEFAULT_SESSION_NAME = "default"

# ---------------------------------------------------------------------------
# Multi-Agent Definitions (10 Agents, 4 Tiers, Per-Agent Model)
# Priority: 1.0 = highest (Safety), 0.4 = lowest (Maintenance)
# NOTE: Agent structure is architectural, not user-configurable.
# ---------------------------------------------------------------------------

AGENT_DEFINITIONS = {
    # Tier 1 -- Safety-Critical (use more capable model)
    "safety-agent-001": {
        "role": "safety",
        "priority": 1.0,
        "model": "gemini-2.0-flash",
        "description": "Fire, gas, smoke detection, emergency response",
        "allowed_device_types": "*",
    },
    "health-agent-002": {
        "role": "health",
        "priority": 0.9,
        "model": "gemini-2.0-flash",
        "description": "Occupant wellness, inactivity detection, fall alerts",
        "allowed_device_types": "*",
    },
    # Tier 2 -- Security & Privacy (fast model)
    "security-agent-003": {
        "role": "security",
        "priority": 0.8,
        "model": "gemini-2.0-flash",
        "description": "Access control, intrusion detection, perimeter monitoring",
        "allowed_device_types": "*",
    },
    "privacy-agent-004": {
        "role": "privacy",
        "priority": 0.7,
        "model": "gemini-2.0-flash",
        "description": "Camera/mic management, guest mode",
        "allowed_device_types": "*",
    },
    # Tier 3 -- Comfort & Efficiency (fast model)
    "energy-agent-005": {
        "role": "energy",
        "priority": 0.6,
        "model": "gemini-2.0-flash",
        "description": "Power consumption optimization, peak demand management",
        "allowed_device_types": "*",
    },
    "climate-agent-006": {
        "role": "climate",
        "priority": 0.5,
        "model": "gemini-2.0-flash",
        "description": "HVAC/comfort/humidity management",
        "allowed_device_types": "*",
    },
    "maintenance-agent-007": {
        "role": "maintenance",
        "priority": 0.4,
        "model": "gemini-2.0-flash",
        "description": "Appliance health monitoring, predictive maintenance",
        "allowed_device_types": "*",
    },
    # POC8 Agents -----------------------------------------------------------
    "nlu-agent-008": {
        "role": "nlu",
        "priority": 0.85,
        "model": "gemini-2.0-flash",
        "description": "Natural language (voice/text) user command processing",
        "allowed_device_types": "*",
    },
    "anomaly-agent-009": {
        "role": "anomaly",
        "priority": 0.88,
        "model": "n/a",  # ML/DL models, not LLM
        "description": "ML/DL-based anomaly detection on device telemetry",
        "allowed_device_types": "*",
    },
    "arbitration-agent-010": {
        "role": "arbitration",
        "priority": 0.95,
        "model": "gemini-2.0-flash",
        "description": "Intelligent conflict arbitration (LLM + ML hybrid)",
        "allowed_device_types": "*",
    },
}


def agent_has_device_access(agent_def: dict, device_type: str) -> bool:
    """Check if an agent definition allows access to a device type.

    Handles '*' wildcard (all types) and explicit lists.
    """
    allowed = agent_def.get("allowed_device_types", [])
    if allowed == "*":
        return True
    return device_type in allowed

# ---------------------------------------------------------------------------
# MCP Configuration
# ---------------------------------------------------------------------------
MCP_SERVER_NAME = "SmartHomeDeviceLayer"
MCP_TRANSPORT = "inprocess"
MCP_LOG_CALLS = _bool(_env.get("MCP_LOG_CALLS"), True)

# ---------------------------------------------------------------------------
# Feedback Loop
# ---------------------------------------------------------------------------
FEEDBACK_ENABLED = _bool(_env.get("FEEDBACK_ENABLED"), True)
FEEDBACK_HISTORY_SIZE = _int(_env.get("FEEDBACK_HISTORY_SIZE"), 5)

# ---------------------------------------------------------------------------
# Voice / NLU
# ---------------------------------------------------------------------------
TTS_ENABLED = _bool(_env.get("TTS_ENABLED"), False)
STT_ENGINE = _env.get("STT_ENGINE", "google")
LISTEN_TIMEOUT = _float(_env.get("LISTEN_TIMEOUT"), 5.0)
CONVERSATION_HISTORY_SIZE = _int(_env.get("CONVERSATION_HISTORY_SIZE"), 10)

# ---------------------------------------------------------------------------
# ML/DL Anomaly Detection
# ---------------------------------------------------------------------------
DL_ANOMALY_ENABLED = _bool(_env.get("DL_ANOMALY_ENABLED"), False)
ANOMALY_THRESHOLD = _float(_env.get("ANOMALY_THRESHOLD"), -0.5)
ANOMALY_ZSCORE_THRESHOLD = _float(_env.get("ANOMALY_ZSCORE_THRESHOLD"), 2.5)
ANOMALY_TRAINING_ROUNDS = _int(_env.get("ANOMALY_TRAINING_ROUNDS"), 20)

# ---------------------------------------------------------------------------
# Arbitration
# ---------------------------------------------------------------------------
ARBITRATION_ENABLED = _bool(_env.get("ARBITRATION_ENABLED"), True)
SAFETY_OVERRIDE_IMMUNE = True  # LOCKED -- cannot be changed via .env

# ---------------------------------------------------------------------------
# Adaptive PoW Difficulty
# ---------------------------------------------------------------------------
ADAPTIVE_DIFFICULTY_ENABLED = _bool(_env.get("ADAPTIVE_DIFFICULTY_ENABLED"), True)
DIFFICULTY_MIN = 1              # LOCKED -- safety bound
DIFFICULTY_MAX = 4              # LOCKED -- safety bound
DIFFICULTY_TX_VOLUME_LOW = _int(_env.get("DIFFICULTY_TX_VOLUME_LOW"), 3)
DIFFICULTY_TX_VOLUME_HIGH = _int(_env.get("DIFFICULTY_TX_VOLUME_HIGH"), 10)
DIFFICULTY_ADJUSTMENT_WINDOW = _int(_env.get("DIFFICULTY_ADJUSTMENT_WINDOW"), 3)

# ---------------------------------------------------------------------------
# Society 5.0 Governance
# ---------------------------------------------------------------------------
GOVERNANCE_ENABLED = True       # LOCKED -- core feature, always on
DEFAULT_GOVERNANCE_PRESET = _env.get("GOVERNANCE_PRESET", "balanced")
