"""
Resident Preferences -- Society 5.0 Human-Centered Governance (NEW POC10).

4-tier governance model:
  Tier 1 (SAFE):      Environment, notifications, voice settings
  Tier 2 (IMPACTFUL): Agent priority sliders, anomaly sensitivity, automation level
  Tier 3 (ADVANCED):  Per-agent device access overrides, budget caps
  Tier 4 (LOCKED):    Safety invariants, crypto, blockchain validation -- IMMUTABLE

Defaults are loaded from .env (via config module). If .env is missing,
hardcoded values below are used.
"""

import copy
import json
import os
from typing import Any


# ---------------------------------------------------------------------------
# Hardcoded defaults (used when .env is missing or incomplete)
# ---------------------------------------------------------------------------

_HARDCODED_DEFAULTS = {
    # Tier 1: SAFE
    "preferred_temp": 22.0,
    "preferred_brightness": 80,
    "quiet_hours_start": "23:00",
    "quiet_hours_end": "07:00",
    "voice_responses": False,
    "tts_voice": "neutral",
    "alert_severity_filter": "warnings",
    "daily_report": False,
    # Tier 2: IMPACTFUL
    "comfort_vs_energy": 0.5,
    "security_vs_privacy": 0.5,
    "anomaly_sensitivity": "medium",
    "automation_level": "auto",
    "confirmation_mode": "never",
    "arbitration_mode": "ai",
    "anomaly_train_cycles": 3,
    # Tier 3: ADVANCED
    "agent_device_overrides": {},
    "api_budget_monthly": 20.0,
    "allowed_providers": ["google", "anthropic", "openai", "ollama"],
}


def _load_defaults_from_env():
    """Merge .env preference values into hardcoded defaults.

    Reads from config._env (the parsed .env dict). If a key is present
    in .env, it overrides the hardcoded default with type conversion.
    """
    from config import _env, _float, _int, _bool

    # Mapping: .env key -> (pref key, converter)
    _ENV_MAP = {
        "PREFERRED_TEMP": ("preferred_temp", lambda v: _float(v, 22.0)),
        "PREFERRED_BRIGHTNESS": ("preferred_brightness", lambda v: _int(v, 80)),
        "QUIET_HOURS_START": ("quiet_hours_start", lambda v: v),
        "QUIET_HOURS_END": ("quiet_hours_end", lambda v: v),
        "VOICE_RESPONSES": ("voice_responses", lambda v: _bool(v, False)),
        "TTS_VOICE": ("tts_voice", lambda v: v),
        "ALERT_SEVERITY_FILTER": ("alert_severity_filter", lambda v: v),
        "DAILY_REPORT": ("daily_report", lambda v: _bool(v, False)),
        "COMFORT_VS_ENERGY": ("comfort_vs_energy", lambda v: _float(v, 0.5)),
        "SECURITY_VS_PRIVACY": ("security_vs_privacy", lambda v: _float(v, 0.5)),
        "ANOMALY_SENSITIVITY": ("anomaly_sensitivity", lambda v: v),
        "AUTOMATION_LEVEL": ("automation_level", lambda v: v),
        "CONFIRMATION_MODE": ("confirmation_mode", lambda v: v),
        "ARBITRATION_MODE": ("arbitration_mode", lambda v: v),
        "ANOMALY_TRAIN_CYCLES": ("anomaly_train_cycles", lambda v: _int(v, 3)),
        "API_BUDGET_MONTHLY": ("api_budget_monthly", lambda v: _float(v, 20.0)),
    }

    defaults = copy.deepcopy(_HARDCODED_DEFAULTS)

    for env_key, (pref_key, converter) in _ENV_MAP.items():
        if env_key in _env:
            defaults[pref_key] = converter(_env[env_key])

    # ALLOWED_PROVIDERS: comma-separated -> list
    if "ALLOWED_PROVIDERS" in _env:
        raw = _env["ALLOWED_PROVIDERS"]
        defaults["allowed_providers"] = [
            p.strip() for p in raw.split(",") if p.strip()
        ]

    return defaults


# Build DEFAULT_PREFERENCES from .env + hardcoded fallbacks
try:
    DEFAULT_PREFERENCES = _load_defaults_from_env()
except Exception:
    # If config module fails (e.g., circular import in tests), use hardcoded
    DEFAULT_PREFERENCES = copy.deepcopy(_HARDCODED_DEFAULTS)

# ---------------------------------------------------------------------------
# LOCKED Parameters (Tier 4 -- immutable, cannot be changed by anyone)
# ---------------------------------------------------------------------------

LOCKED_PARAMETERS = {
    "safety_priority": 1.0,
    "firmware_gas_threshold_ppm": 50,
    "firmware_smoke_threshold": 0.3,
    "safety_override_immune": True,
    "ed25519_key_management": True,
    "blockchain_validation": True,
    "min_active_detectors": 1,
    "safety_model_min_tier": "pro",
    "governance_changes_logged": True,
}

# ---------------------------------------------------------------------------
# Tier classification
# ---------------------------------------------------------------------------

TIER_MAP = {
    # Tier 1: SAFE
    "preferred_temp": 1,
    "preferred_brightness": 1,
    "quiet_hours_start": 1,
    "quiet_hours_end": 1,
    "voice_responses": 1,
    "tts_voice": 1,
    "alert_severity_filter": 1,
    "daily_report": 1,
    # Tier 2: IMPACTFUL
    "comfort_vs_energy": 2,
    "security_vs_privacy": 2,
    "anomaly_sensitivity": 2,
    "automation_level": 2,
    "confirmation_mode": 2,
    "arbitration_mode": 2,
    "anomaly_train_cycles": 2,
    # Tier 3: ADVANCED
    "agent_device_overrides": 3,
    "api_budget_monthly": 3,
    "allowed_providers": 3,
}

# ---------------------------------------------------------------------------
# Validation rules
# ---------------------------------------------------------------------------

VALIDATION_RULES = {
    "preferred_temp": {"type": float, "min": 16.0, "max": 30.0},
    "preferred_brightness": {"type": int, "min": 0, "max": 100},
    "comfort_vs_energy": {"type": float, "min": 0.0, "max": 1.0},
    "security_vs_privacy": {"type": float, "min": 0.0, "max": 1.0},
    "anomaly_sensitivity": {"type": str, "choices": ["low", "medium", "high"]},
    "automation_level": {"type": str, "choices": ["manual", "suggest", "auto"]},
    "confirmation_mode": {"type": str,
                          "choices": ["always", "destructive_only", "never"]},
    "arbitration_mode": {"type": str, "choices": ["ai", "priority", "ask_me"]},
    "anomaly_train_cycles": {"type": int, "min": 2, "max": 10},
    "api_budget_monthly": {"type": float, "min": 0.0, "max": 1000.0},
    "tts_voice": {"type": str, "choices": ["male", "female", "neutral"]},
    "alert_severity_filter": {"type": str,
                              "choices": ["critical_only", "warnings", "all"]},
}

# ---------------------------------------------------------------------------
# Anomaly sensitivity mapping
# ---------------------------------------------------------------------------

ANOMALY_SENSITIVITY_MAP = {
    "low":    {"zscore_threshold": 3.5, "iforest_threshold": -0.7},
    "medium": {"zscore_threshold": 2.5, "iforest_threshold": -0.5},
    "high":   {"zscore_threshold": 1.5, "iforest_threshold": -0.3},
}


# ---------------------------------------------------------------------------
# Resident Preferences class
# ---------------------------------------------------------------------------

class ResidentPreferences:
    """Manages resident governance preferences with tier-based validation."""

    def __init__(self, filepath: str = ""):
        self._prefs = copy.deepcopy(DEFAULT_PREFERENCES)
        self._filepath = filepath
        if filepath and os.path.isfile(filepath):
            self.load(filepath)

    @property
    def preferences(self) -> dict:
        """Return a copy of current preferences."""
        return copy.deepcopy(self._prefs)

    def get(self, key: str, default=None):
        """Get a preference value. LOCKED parameters always return fixed value."""
        if key in LOCKED_PARAMETERS:
            return LOCKED_PARAMETERS[key]
        return self._prefs.get(key, default)

    def set(self, key: str, value: Any) -> dict:
        """Set a preference value with validation.

        Returns dict: {success, old_value, new_value, tier, reason}
        """
        # Check LOCKED
        if key in LOCKED_PARAMETERS:
            return {
                "success": False,
                "reason": f"LOCKED: '{key}' is immutable (tier 4)",
                "tier": 4,
                "old_value": LOCKED_PARAMETERS[key],
            }

        # Check known key
        if key not in TIER_MAP:
            return {
                "success": False,
                "reason": f"Unknown preference key: '{key}'",
            }

        # Validate
        rule = VALIDATION_RULES.get(key)
        if rule:
            valid, reason = self._validate(key, value, rule)
            if not valid:
                return {
                    "success": False,
                    "reason": reason,
                    "tier": TIER_MAP[key],
                    "old_value": self._prefs.get(key),
                }

        old_value = self._prefs.get(key)
        self._prefs[key] = value
        return {
            "success": True,
            "old_value": old_value,
            "new_value": value,
            "tier": TIER_MAP[key],
            "reason": "OK",
        }

    def _validate(self, key: str, value: Any, rule: dict) -> tuple:
        """Validate a value against its rule. Returns (valid, reason)."""
        expected_type = rule.get("type")
        if expected_type:
            # Allow int for float fields
            if expected_type == float and isinstance(value, int):
                pass
            elif not isinstance(value, expected_type):
                return (False,
                        f"Type error: expected {expected_type.__name__}, "
                        f"got {type(value).__name__}")

        if "min" in rule and value < rule["min"]:
            return False, f"Below minimum: {value} < {rule['min']}"
        if "max" in rule and value > rule["max"]:
            return False, f"Above maximum: {value} > {rule['max']}"
        if "choices" in rule and value not in rule["choices"]:
            return False, f"Invalid choice: '{value}' not in {rule['choices']}"
        return True, "OK"

    # ------------------------------------------------------------------
    # Agent priority adjustment
    # ------------------------------------------------------------------

    def apply_to_agent_priorities(self, agent_definitions: dict):
        """Adjust agent priorities based on current preferences.

        Formulas calibrated so defaults (0.5, 0.5) = exact POC9 values.
        Safety (1.0), Arbitration (0.95), Health (0.9), Anomaly (0.88),
        NLU (0.85) are NOT affected -- only comfort/energy/security/privacy.
        """
        balance = self._prefs["comfort_vs_energy"]
        # Energy: 0.6 at balance=0.5, range [0.5, 0.7]
        agent_definitions["energy-agent-005"]["priority"] = round(
            0.5 + (1.0 - balance) * 0.2, 3)
        # Climate: 0.5 at balance=0.5, range [0.4, 0.6]
        agent_definitions["climate-agent-006"]["priority"] = round(
            0.4 + balance * 0.2, 3)

        sec_priv = self._prefs["security_vs_privacy"]
        # Security: 0.8 at sec_priv=0.5, range [0.6, 1.0]
        agent_definitions["security-agent-003"]["priority"] = round(
            0.6 + sec_priv * 0.4, 3)
        # Privacy: 0.7 at sec_priv=0.5, range [0.5, 0.9]
        agent_definitions["privacy-agent-004"]["priority"] = round(
            0.9 - sec_priv * 0.4, 3)

    def get_anomaly_thresholds(self) -> dict:
        """Get anomaly detection thresholds based on current sensitivity."""
        sensitivity = self._prefs.get("anomaly_sensitivity", "medium")
        return ANOMALY_SENSITIVITY_MAP.get(
            sensitivity, ANOMALY_SENSITIVITY_MAP["medium"])

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, filepath: str = ""):
        """Save preferences to JSON file."""
        path = filepath or self._filepath
        if path:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w") as f:
                json.dump(self._prefs, f, indent=2)

    def load(self, filepath: str = ""):
        """Load preferences from JSON file (only known keys)."""
        path = filepath or self._filepath
        if path and os.path.isfile(path):
            with open(path, "r") as f:
                loaded = json.load(f)
            for k, v in loaded.items():
                if k in TIER_MAP:
                    self._prefs[k] = v

    def to_dict(self) -> dict:
        """Return a copy of the preferences dict."""
        return copy.deepcopy(self._prefs)
