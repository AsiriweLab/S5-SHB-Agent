"""
Governance Contract -- Smart contract enforcement for Society 5.0 (NEW POC10).

Validates governance changes against tier rules, bounds, and constraints.
All governance changes are logged on-chain as governance_update transactions.
"""

import hashlib
import json
import time
from typing import Any, List

from blockchain import Transaction
from resident_preferences import (
    LOCKED_PARAMETERS, TIER_MAP, VALIDATION_RULES,
    ResidentPreferences,
)
from model_router import (
    MODEL_REGISTRY, MODEL_CONSTRAINTS, TIER_ORDER,
    GOVERNANCE_PRESETS, ModelRouter,
)


class GovernanceContract:
    """Enforces governance rules -- cannot be overridden by any agent."""

    def __init__(self, preferences: ResidentPreferences,
                 router: ModelRouter):
        self._preferences = preferences
        self._router = router
        self._change_log: List[dict] = []

    # ------------------------------------------------------------------
    # Preference validation & application
    # ------------------------------------------------------------------

    def validate_preference_change(self, key: str, new_value: Any) -> dict:
        """Check if a preference change is permitted.

        Returns {valid, reason, tier, [old_value, new_value]}.
        """
        if key in LOCKED_PARAMETERS:
            return {"valid": False,
                    "reason": f"LOCKED: '{key}' is immutable (tier 4)",
                    "tier": 4}
        if key not in TIER_MAP:
            return {"valid": False,
                    "reason": f"Unknown preference: '{key}'"}

        rule = VALIDATION_RULES.get(key)
        if rule:
            # Type coercion: int -> float
            expected = rule.get("type")
            if expected == float and isinstance(new_value, int):
                new_value = float(new_value)
            elif expected and not isinstance(new_value, expected):
                return {"valid": False,
                        "reason": f"Type error: expected {expected.__name__}",
                        "tier": TIER_MAP[key]}
            if "min" in rule and new_value < rule["min"]:
                return {"valid": False,
                        "reason": f"Below minimum ({rule['min']})",
                        "tier": TIER_MAP[key]}
            if "max" in rule and new_value > rule["max"]:
                return {"valid": False,
                        "reason": f"Above maximum ({rule['max']})",
                        "tier": TIER_MAP[key]}
            if "choices" in rule and new_value not in rule["choices"]:
                return {"valid": False,
                        "reason": f"Invalid choice: {new_value}",
                        "tier": TIER_MAP[key]}

        return {"valid": True, "tier": TIER_MAP[key],
                "old_value": self._preferences.get(key),
                "new_value": new_value, "reason": "OK"}

    def apply_preference_change(self, key: str, new_value: Any) -> dict:
        """Validate + apply a preference change. Returns result dict."""
        validation = self.validate_preference_change(key, new_value)
        if not validation.get("valid"):
            return {"success": False, **validation}

        result = self._preferences.set(key, new_value)
        if result["success"]:
            entry = {
                "type": "preference_change",
                "key": key,
                "old_value": result["old_value"],
                "new_value": result["new_value"],
                "tier": result["tier"],
                "timestamp": time.time(),
            }
            self._change_log.append(entry)
        return result

    # ------------------------------------------------------------------
    # Model validation & application
    # ------------------------------------------------------------------

    def validate_model_change(self, agent_id: str, new_model: str) -> dict:
        """Check if a model assignment meets tier constraints."""
        if new_model not in MODEL_REGISTRY:
            return {"valid": False,
                    "reason": f"Unknown model: {new_model}"}
        constraint = MODEL_CONSTRAINTS.get(agent_id)
        if not constraint:
            return {"valid": False,
                    "reason": f"Unknown agent: {agent_id}"}

        model_info = MODEL_REGISTRY[new_model]
        min_tier = constraint["min_tier"]

        if min_tier != "n/a":
            if TIER_ORDER.get(model_info["tier"], 0) < TIER_ORDER.get(min_tier, 0):
                return {
                    "valid": False,
                    "reason": (f"Model tier '{model_info['tier']}' "
                               f"below required '{min_tier}'"),
                    "agent_id": agent_id, "model": new_model,
                }

        return {"valid": True, "reason": "OK",
                "agent_id": agent_id, "model": new_model,
                "tier": model_info["tier"],
                "provider": model_info["provider"]}

    def apply_model_change(self, agent_id: str, new_model: str) -> dict:
        """Validate + apply a model assignment change."""
        validation = self.validate_model_change(agent_id, new_model)
        if not validation.get("valid"):
            return {"success": False, **validation}

        result = self._router.assign_model(agent_id, new_model)
        if result.get("success"):
            entry = {
                "type": "model_change",
                "agent_id": agent_id,
                "old_model": result.get("old_model"),
                "new_model": result.get("new_model"),
                "timestamp": time.time(),
            }
            self._change_log.append(entry)
        return result

    def apply_preset(self, preset_name: str,
                     agent_definitions: dict = None) -> dict:
        """Apply a governance preset to all agents."""
        result = self._router.apply_preset(preset_name, agent_definitions)
        if result.get("success"):
            self._change_log.append({
                "type": "preset_applied",
                "preset": preset_name,
                "timestamp": time.time(),
            })
        return result

    # ------------------------------------------------------------------
    # On-chain logging
    # ------------------------------------------------------------------

    def create_governance_transaction(self, change: dict) -> Transaction:
        """Create a blockchain transaction recording a governance change."""
        params = {
            "change_type": change.get("type", "unknown"),
            "details": change,
        }
        params_json = json.dumps(params, sort_keys=True, default=str)
        reasoning_hash = hashlib.sha256(params_json.encode()).hexdigest()
        return Transaction(
            agent_id="GOVERNANCE_CONTRACT",
            action="governance_update",
            target_device="system",
            params=params,
            confidence=1.0,
            reasoning_hash=reasoning_hash,
            tx_type="governance_update",
        )

    # ------------------------------------------------------------------
    # Log access
    # ------------------------------------------------------------------

    @property
    def change_log(self) -> List[dict]:
        """Return list of all governance changes in this session."""
        return list(self._change_log)

    @property
    def preference_changes(self) -> int:
        return sum(1 for c in self._change_log
                   if c["type"] == "preference_change")

    @property
    def model_changes(self) -> int:
        return sum(1 for c in self._change_log
                   if c["type"] == "model_change")
