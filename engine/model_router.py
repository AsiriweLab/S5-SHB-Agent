"""
Model Router -- Multi-Provider AI Abstraction (NEW POC10).

Routes agent LLM calls to the correct provider based on resident governance.
Supports: Google Gemini, Anthropic Claude, OpenAI GPT, local Ollama.
Tracks cost per agent per provider.

Model registry, constraints, and presets are loaded from models.json.
If models.json is missing, hardcoded defaults are used.
"""

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
# Hardcoded defaults (used when models.json is missing)
# ---------------------------------------------------------------------------

_HARDCODED_REGISTRY = {
    "gemini-2.5-pro":    {"tier": "pro",   "cost_per_1k": 0.00125,
                          "provider": "google",    "privacy": "cloud"},
    "gemini-2.0-flash":  {"tier": "flash", "cost_per_1k": 0.0001,
                          "provider": "google",    "privacy": "cloud"},
    "claude-sonnet-4-6": {"tier": "pro",   "cost_per_1k": 0.003,
                          "provider": "anthropic", "privacy": "cloud"},
    "claude-haiku-4-5":  {"tier": "flash", "cost_per_1k": 0.0008,
                          "provider": "anthropic", "privacy": "cloud"},
    "gpt-4o":            {"tier": "pro",   "cost_per_1k": 0.005,
                          "provider": "openai",    "privacy": "cloud"},
    "gpt-4o-mini":       {"tier": "flash", "cost_per_1k": 0.00015,
                          "provider": "openai",    "privacy": "cloud"},
    "ollama/llama3":     {"tier": "flash", "cost_per_1k": 0.0,
                          "provider": "ollama",    "privacy": "local"},
    "ollama/mistral":    {"tier": "flash", "cost_per_1k": 0.0,
                          "provider": "ollama",    "privacy": "local"},
}

_HARDCODED_CONSTRAINTS = {
    "safety-agent-001":      {"min_tier": "pro",   "allow_local": True},
    "health-agent-002":      {"min_tier": "pro",   "allow_local": True},
    "security-agent-003":    {"min_tier": "flash", "allow_local": True},
    "privacy-agent-004":     {"min_tier": "flash", "allow_local": True},
    "energy-agent-005":      {"min_tier": "flash", "allow_local": True},
    "climate-agent-006":     {"min_tier": "flash", "allow_local": True},
    "maintenance-agent-007": {"min_tier": "flash", "allow_local": True},
    "nlu-agent-008":         {"min_tier": "flash", "allow_local": True},
    "anomaly-agent-009":     {"min_tier": "n/a",   "allow_local": True},
    "arbitration-agent-010": {"min_tier": "flash", "allow_local": True},
}

_HARDCODED_PRESETS = {
    "balanced": {
        "default_model": "gemini-2.0-flash",
        "safety_model": "gemini-2.5-pro",
        "description": "Pro for safety/health, flash for everything else.",
    },
    "max_privacy": {
        "default_model": "ollama/llama3",
        "safety_model": "ollama/llama3",
        "description": "All processing local. No cloud APIs.",
    },
    "budget": {
        "default_model": "gpt-4o-mini",
        "safety_model": "gemini-2.5-pro",
        "description": "Cheapest cloud models. Safety uses pro tier.",
    },
    "best_quality": {
        "default_model": "gemini-2.5-pro",
        "safety_model": "gemini-2.5-pro",
        "description": "Maximum accuracy everywhere.",
    },
}


def _load_models_config():
    """Load model registry, constraints, and presets from models.json.

    Returns (registry, constraints, presets) with hardcoded fallbacks.
    """
    try:
        from config import MODELS_CONFIG_FILE
        config_path = MODELS_CONFIG_FILE
    except Exception:
        config_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "models.json")

    registry = dict(_HARDCODED_REGISTRY)
    constraints = dict(_HARDCODED_CONSTRAINTS)
    presets = dict(_HARDCODED_PRESETS)

    if not os.path.isfile(config_path):
        return registry, constraints, presets

    try:
        with open(config_path, "r") as f:
            data = json.load(f)

        # Merge registry (user can add new models)
        if "registry" in data and isinstance(data["registry"], dict):
            for name, info in data["registry"].items():
                # Validate required fields
                if all(k in info for k in ("tier", "cost_per_1k", "provider")):
                    registry[name] = info

        # Merge constraints
        if "agent_constraints" in data and isinstance(data["agent_constraints"], dict):
            for agent_id, constraint in data["agent_constraints"].items():
                if "min_tier" in constraint:
                    constraints[agent_id] = constraint

        # Merge presets (convert models.json format to internal format)
        if "presets" in data and isinstance(data["presets"], dict):
            for preset_name, preset in data["presets"].items():
                if "default" in preset and "safety" in preset:
                    presets[preset_name] = {
                        "default_model": preset["default"],
                        "safety_model": preset["safety"],
                        "description": preset.get("description", ""),
                    }
                elif "default_model" in preset:
                    presets[preset_name] = preset

    except (json.JSONDecodeError, OSError):
        pass  # Use hardcoded fallbacks

    return registry, constraints, presets


# Load from models.json at module level
MODEL_REGISTRY, MODEL_CONSTRAINTS, GOVERNANCE_PRESETS = _load_models_config()

TIER_ORDER = {"n/a": 0, "flash": 1, "pro": 2}


# ---------------------------------------------------------------------------
# Cost Tracker
# ---------------------------------------------------------------------------

@dataclass
class ModelCallRecord:
    agent_id: str
    model_name: str
    provider: str
    tokens_approx: int
    cost_approx: float
    latency_ms: float
    timestamp: float
    success: bool


class CostTracker:
    """Tracks API cost per agent per provider."""

    def __init__(self):
        self._records: List[ModelCallRecord] = []

    def record(self, rec: ModelCallRecord):
        self._records.append(rec)

    @property
    def total_calls(self) -> int:
        return len(self._records)

    def summary(self) -> dict:
        """Return cost summary by agent and provider."""
        total = sum(r.cost_approx for r in self._records)
        by_agent: Dict[str, float] = {}
        by_provider: Dict[str, float] = {}
        for r in self._records:
            by_agent[r.agent_id] = by_agent.get(r.agent_id, 0) + r.cost_approx
            by_provider[r.provider] = (by_provider.get(r.provider, 0)
                                       + r.cost_approx)
        return {
            "total_cost": round(total, 6),
            "total_calls": len(self._records),
            "by_agent": {k: round(v, 6) for k, v in by_agent.items()},
            "by_provider": {k: round(v, 6) for k, v in by_provider.items()},
        }


# ---------------------------------------------------------------------------
# Model Router
# ---------------------------------------------------------------------------

class ModelRouter:
    """Routes LLM calls to the correct provider per agent assignment."""

    def __init__(self, api_keys: Optional[Dict[str, str]] = None):
        """api_keys: {provider_name: api_key}"""
        self._api_keys = api_keys or {}
        self._assignments: Dict[str, str] = {}   # agent_id -> model_name
        self._models_cache: Dict[str, object] = {}
        self.cost_tracker = CostTracker()

    # ------------------------------------------------------------------
    # Assignment
    # ------------------------------------------------------------------

    def assign_model(self, agent_id: str, model_name: str) -> dict:
        """Assign a model to an agent with tier constraint checking."""
        if model_name not in MODEL_REGISTRY:
            return {"success": False,
                    "reason": f"Unknown model: {model_name}"}
        constraint = MODEL_CONSTRAINTS.get(agent_id)
        if not constraint:
            return {"success": False,
                    "reason": f"Unknown agent: {agent_id}"}

        model_info = MODEL_REGISTRY[model_name]
        min_tier = constraint["min_tier"]

        # Anomaly agent uses ML, not LLM -- skip tier check
        if min_tier != "n/a":
            if TIER_ORDER.get(model_info["tier"], 0) < TIER_ORDER.get(min_tier, 0):
                return {
                    "success": False,
                    "reason": (f"Model '{model_name}' (tier={model_info['tier']}) "
                               f"below minimum tier '{min_tier}' for {agent_id}"),
                }

        old = self._assignments.get(agent_id)
        self._assignments[agent_id] = model_name
        return {"success": True, "old_model": old, "new_model": model_name}

    def get_assignment(self, agent_id: str) -> str:
        """Return the model assigned to an agent (default: gemini-2.0-flash)."""
        return self._assignments.get(agent_id, "gemini-2.0-flash")

    def get_all_assignments(self) -> dict:
        """Return a copy of all current assignments."""
        return dict(self._assignments)

    def apply_preset(self, preset_name: str,
                     agent_definitions: Optional[dict] = None) -> dict:
        """Apply a governance preset to all LLM-using agents."""
        preset = GOVERNANCE_PRESETS.get(preset_name)
        if not preset:
            return {"success": False,
                    "reason": f"Unknown preset: {preset_name}"}

        results = {}
        agents_to_assign = agent_definitions or MODEL_CONSTRAINTS

        for agent_id in agents_to_assign:
            constraint = MODEL_CONSTRAINTS.get(agent_id, {})
            # Skip ML-only agents
            if constraint.get("min_tier") == "n/a":
                continue
            # Safety/health (priority >= 0.9) use safety_model
            if agent_definitions:
                priority = agent_definitions[agent_id].get("priority", 0)
            else:
                priority = 0
            if priority >= 0.9:
                model = preset["safety_model"]
            else:
                model = preset["default_model"]
            r = self.assign_model(agent_id, model)
            results[agent_id] = r

        return {"success": True, "preset": preset_name,
                "assignments": results}

    # ------------------------------------------------------------------
    # LLM Call Routing
    # ------------------------------------------------------------------

    def call(self, agent_id: str, prompt: str) -> Optional[str]:
        """Route an LLM call to the assigned provider. Returns response text."""
        model_name = self.get_assignment(agent_id)
        model_info = MODEL_REGISTRY.get(model_name, {})
        provider = model_info.get("provider", "google")

        t0 = time.time()
        try:
            if provider == "google":
                text = self._call_google(model_name, prompt)
            elif provider == "anthropic":
                text = self._call_anthropic(model_name, prompt)
            elif provider == "openai":
                text = self._call_openai(model_name, prompt)
            elif provider == "ollama":
                text = self._call_ollama(model_name, prompt)
            else:
                text = None

            latency = (time.time() - t0) * 1000
            tokens = len(prompt.split()) + (len(text.split()) if text else 0)
            cost = tokens / 1000 * model_info.get("cost_per_1k", 0)
            self.cost_tracker.record(ModelCallRecord(
                agent_id=agent_id, model_name=model_name, provider=provider,
                tokens_approx=tokens, cost_approx=cost, latency_ms=latency,
                timestamp=time.time(), success=text is not None,
            ))
            return text
        except Exception:
            latency = (time.time() - t0) * 1000
            self.cost_tracker.record(ModelCallRecord(
                agent_id=agent_id, model_name=model_name, provider=provider,
                tokens_approx=0, cost_approx=0, latency_ms=latency,
                timestamp=time.time(), success=False,
            ))
            return None

    def _call_google(self, model_name: str, prompt: str) -> Optional[str]:
        import google.generativeai as genai
        if model_name not in self._models_cache:
            genai.configure(api_key=self._api_keys.get("google", ""))
            self._models_cache[model_name] = genai.GenerativeModel(model_name)
        model = self._models_cache[model_name]
        response = model.generate_content(prompt)
        return response.text.strip()

    def _call_anthropic(self, model_name: str, prompt: str) -> Optional[str]:
        try:
            import anthropic
            client = anthropic.Anthropic(
                api_key=self._api_keys.get("anthropic", ""))
            response = client.messages.create(
                model=model_name, max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.content[0].text.strip()
        except ImportError:
            return None

    def _call_openai(self, model_name: str, prompt: str) -> Optional[str]:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=self._api_keys.get("openai", ""))
            response = client.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.choices[0].message.content.strip()
        except ImportError:
            return None

    def _call_ollama(self, model_name: str, prompt: str) -> Optional[str]:
        try:
            import requests
            model_short = model_name.replace("ollama/", "")
            resp = requests.post(
                "http://localhost:11434/api/generate",
                json={"model": model_short, "prompt": prompt, "stream": False},
                timeout=30,
            )
            return resp.json().get("response", "").strip()
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save_assignments(self, filepath: str):
        """Save model assignments to JSON file."""
        os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
        with open(filepath, "w") as f:
            json.dump(self._assignments, f, indent=2)

    def load_assignments(self, filepath: str):
        """Load model assignments from JSON file."""
        if os.path.isfile(filepath):
            with open(filepath, "r") as f:
                self._assignments = json.load(f)
