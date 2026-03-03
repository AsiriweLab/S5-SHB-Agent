"""
Layer 3: AI Agent -- Gemini-Backed Multi-Agent Decision Engine
POC10: Session Persistence + Society 5.0 Governance (10 agents total).
"""

import hashlib
import json
import re
import time
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

from loguru import logger

from blockchain import Transaction, sign_data
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


# ---------------------------------------------------------------------------
# Agent Roles
# ---------------------------------------------------------------------------

class AgentRole(Enum):
    SAFETY = "safety"
    HEALTH = "health"
    SECURITY = "security"
    PRIVACY = "privacy"
    ENERGY = "energy"
    CLIMATE = "climate"
    MAINTENANCE = "maintenance"
    NLU = "nlu"                  # NEW POC8: Natural language understanding
    ANOMALY = "anomaly"          # NEW POC8: ML/DL anomaly detection
    ARBITRATION = "arbitration"  # NEW POC8: Intelligent conflict arbitration


# ---------------------------------------------------------------------------
# Role-specific System Prompts
# ---------------------------------------------------------------------------

SYSTEM_PROMPTS = {
    AgentRole.SAFETY: """\
You are a SAFETY agent for a smart home. Your ONLY concern is fire, gas, \
and smoke detection plus emergency response.

RULES:
- Respond ONLY with a valid JSON array of action objects.
- Each action: {"device_id": "...", "command": "...", "params": {}, \
"confidence": 0.0-1.0, "reasoning": "..."}
- If no safety concern exists, return: []
- Only act on SAFETY-CRITICAL conditions (high temperature, smoke, gas).
- If gas is detected above safe levels, recommend turning off thermostat \
and unlocking doors.
- Commands you may use: turn_off, unlock, turn_on, set_brightness, silence_alarm
""",

    AgentRole.HEALTH: """\
You are a HEALTH/WELLNESS agent for a smart home. You monitor occupant \
wellbeing through motion sensors.

RULES:
- Respond ONLY with a valid JSON array of action objects.
- Each action: {"device_id": "...", "command": "...", "params": {}, \
"confidence": 0.0-1.0, "reasoning": "..."}
- If no health concern exists, return: []
- If motion sensors show no movement for extended periods during daytime, \
consider turning on lights to check on occupant.
- Commands you may use: turn_on, turn_off, set_brightness, lock, unlock, reset
""",

    AgentRole.SECURITY: """\
You are a SECURITY agent for a smart home. You handle access control, \
intrusion detection, and perimeter monitoring.

RULES:
- Respond ONLY with a valid JSON array of action objects.
- Each action: {"device_id": "...", "command": "...", "params": {}, \
"confidence": 0.0-1.0, "reasoning": "..."}
- If no security concern exists, return: []
- If cameras or motion sensors detect unexpected activity, recommend \
locking doors and starting camera recording.
- Default posture: doors locked, entrance cameras recording.
- Commands you may use: lock, unlock, start_recording, stop_recording, turn_on, turn_off, set_brightness, reset
""",

    AgentRole.PRIVACY: """\
You are a PRIVACY agent for a smart home. You manage cameras to protect \
occupant privacy.

RULES:
- Respond ONLY with a valid JSON array of action objects.
- Each action: {"device_id": "...", "command": "...", "params": {}, \
"confidence": 0.0-1.0, "reasoning": "..."}
- If no privacy action needed, return: []
- Indoor cameras should stop recording when people are detected \
inside to protect privacy.
- Entrance cameras can remain recording for security.
- Commands you may use: start_recording, stop_recording, turn_on, turn_off, set_brightness
""",

    AgentRole.ENERGY: """\
You are an ENERGY MANAGEMENT agent for a smart home. You optimize power \
consumption and manage peak demand.

RULES:
- Respond ONLY with a valid JSON array of action objects.
- Each action: {"device_id": "...", "command": "...", "params": {}, \
"confidence": 0.0-1.0, "reasoning": "..."}
- If no energy optimization needed, return: []
- Monitor smart plug power readings for excessive consumption (>100W).
- Set plugs to eco mode to reduce energy usage during high-demand periods.
- Dim lights to save power when full brightness is not needed.
- Coordinate thermostat and HVAC to minimize energy waste.
- Do NOT override safety-critical devices or emergency states.
- Commands you may use: turn_on, turn_off, set_mode, set_temperature, set_brightness, set_fan_speed
""",

    AgentRole.CLIMATE: """\
You are a CLIMATE/COMFORT agent for a smart home. You manage HVAC, \
temperature, and humidity for occupant comfort.

RULES:
- Respond ONLY with a valid JSON array of action objects.
- Each action: {"device_id": "...", "command": "...", "params": {}, \
"confidence": 0.0-1.0, "reasoning": "..."}
- If climate is comfortable (20-25C, 40-60% humidity), return: []
- If temperature is outside comfort range, adjust HVAC target temperature.
- Adjust HVAC mode and fan speed for optimal comfort.
- Coordinate with thermostat for temperature consistency.
- Prefer gradual adjustments over dramatic changes.
- Commands you may use: set_temperature, set_mode, set_fan_speed, turn_off, turn_on, set_brightness
""",

    AgentRole.MAINTENANCE: """\
You are a MAINTENANCE agent for a smart home. You monitor appliance \
health and perform predictive maintenance.

RULES:
- Respond ONLY with a valid JSON array of action objects.
- Each action: {"device_id": "...", "command": "...", "params": {}, \
"confidence": 0.0-1.0, "reasoning": "..."}
- If all appliances are healthy (status=ok), return: []
- If an appliance status is 'warning', report its status for monitoring.
- If an appliance status is 'critical', trigger maintenance mode immediately.
- Monitor runtime hours; high runtime (>4000h) without service warrants \
a status report.
- Commands you may use: report_status, trigger_maintenance_mode, reset_runtime, turn_off, turn_on, set_mode
""",

    # POC8 stubs -- actual prompts live in nlu_agent.py, anomaly_agent.py,
    # arbitration_agent.py respectively.
    AgentRole.NLU: "NLU agent -- see nlu_agent.py for full prompt.",
    AgentRole.ANOMALY: "Anomaly agent -- see anomaly_agent.py (ML/DL, no LLM prompt).",
    AgentRole.ARBITRATION: "Arbitration agent -- see arbitration_agent.py for full prompt.",
}


# ---------------------------------------------------------------------------
# Agent Decision
# ---------------------------------------------------------------------------

@dataclass
class AgentDecision:
    transaction: Transaction
    reasoning_text: str
    reasoning_hash: str


class AgentOfflineError(Exception):
    pass


# ---------------------------------------------------------------------------
# Gemini Initialization
# ---------------------------------------------------------------------------

def _init_gemini(api_key: str, model_name: str):
    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        return genai.GenerativeModel(model_name)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Smart Home Agent (multi-role, async-capable, feedback-aware)
# ---------------------------------------------------------------------------

class SmartHomeAgent:
    """Gemini-backed AI agent with async support and feedback loop."""

    def __init__(self, agent_id: str, private_key: Ed25519PrivateKey,
                 role: AgentRole,
                 api_key: str = "", model_name: str = "gemini-2.0-flash",
                 model_router=None):
        self.agent_id = agent_id
        self.role = role
        self._private_key = private_key
        self._api_key = api_key
        self._model_name = model_name
        self._model = None
        self._model_router = model_router   # NEW POC10: multi-provider routing
        self._available = True
        self._feedback_context = ""

    def set_offline(self):
        self._available = False

    def set_online(self):
        self._available = True

    def set_feedback_context(self, context: str):
        """Set feedback from recent decision outcomes."""
        self._feedback_context = context

    # ---- prompt builder (shared by sync + async) ----

    def _build_prompt(self, sensor_text: str) -> str:
        prompt = SYSTEM_PROMPTS[self.role]
        if self._feedback_context:
            prompt += (f"\n\nRECENT DECISION HISTORY:\n"
                       f"{self._feedback_context}")
        prompt += f"\n\n{sensor_text}\n\nRespond with JSON array only:"
        return prompt

    # ---- synchronous path (unchanged from POC6) ----

    def perceive_and_decide(self, telemetry_list) -> List[AgentDecision]:
        """Analyze telemetry and produce decisions for this agent's role."""
        if not self._available:
            raise AgentOfflineError(
                f"Agent '{self.agent_id}' ({self.role.value}) is offline")

        sensor_text = self._format_telemetry(telemetry_list)
        actions = self._call_llm(sensor_text)

        if not actions:
            return []

        decisions = []
        for act in actions:
            decision = self._create_decision(act)
            if decision:
                decisions.append(decision)
        return decisions

    # ---- async path (NEW POC7) ----

    async def perceive_and_decide_async(self, telemetry_list) -> List[AgentDecision]:
        """Async version -- uses generate_content_async() for concurrent reasoning."""
        if not self._available:
            raise AgentOfflineError(
                f"Agent '{self.agent_id}' ({self.role.value}) is offline")

        sensor_text = self._format_telemetry(telemetry_list)
        actions = await self._call_llm_async(sensor_text)

        if not actions:
            return []

        return [d for act in actions if (d := self._create_decision(act))]

    # ---- telemetry formatting ----

    def _format_telemetry(self, telemetry_list) -> str:
        lines = [f"Current time: {time.strftime('%H:%M:%S')}",
                 f"Agent role: {self.role.value}", ""]

        # Build dynamic device inventory from actual telemetry
        lines.append("AVAILABLE DEVICES (use ONLY these device_id values):")
        for t in telemetry_list:
            lines.append(f"  - device_id=\"{t.device_id}\"  type={t.device_type}")
        lines.append("")
        lines.append("IMPORTANT: You MUST only target device IDs listed above. "
                      "Do NOT use any other device IDs.")
        lines.append("")

        lines.append("CURRENT TELEMETRY:")
        for t in telemetry_list:
            parts = [f"{k}={v}" for k, v in t.readings.items()]
            lines.append(f"  [{t.device_type}:{t.device_id}] "
                         f"{', '.join(parts)}")
        return "\n".join(lines)

    # ---- sync LLM call ----

    def _call_llm(self, sensor_text: str) -> Optional[List[dict]]:
        prompt = self._build_prompt(sensor_text)

        # NEW POC10: Route through model router if available
        if self._model_router:
            try:
                text = self._model_router.call(self.agent_id, prompt)
                if not text:
                    return None
                text = re.sub(r'^```(?:json)?\s*', '', text)
                text = re.sub(r'\s*```$', '', text)
                return json.loads(text)
            except json.JSONDecodeError:
                logger.debug(
                    f"Agent {self.agent_id} ({self.role.value}) JSON parse "
                    f"failed. Raw: {text[:500]}")
                return None
            except Exception as e:
                logger.debug(
                    f"Agent {self.agent_id} ({self.role.value}) LLM call "
                    f"failed: {e}")
                return None

        # Existing direct-Gemini path (backward compat)
        if self._model is None:
            self._model = _init_gemini(self._api_key, self._model_name)
        if self._model is None:
            return None
        try:
            response = self._model.generate_content(prompt)
            text = response.text.strip()
            text = re.sub(r'^```(?:json)?\s*', '', text)
            text = re.sub(r'\s*```$', '', text)
            return json.loads(text)
        except json.JSONDecodeError:
            logger.debug(
                f"Agent {self.agent_id} ({self.role.value}) JSON parse "
                f"failed. Raw: {text[:500]}")
            return None
        except Exception as e:
            logger.debug(
                f"Agent {self.agent_id} ({self.role.value}) LLM call "
                f"failed: {e}")
            return None

    # ---- async LLM call (NEW POC7) ----

    async def _call_llm_async(self, sensor_text: str) -> Optional[List[dict]]:
        # NEW POC10: If model_router, fall back to sync (router has no async yet)
        if self._model_router:
            return self._call_llm(sensor_text)

        if self._model is None:
            self._model = _init_gemini(self._api_key, self._model_name)
        if self._model is None:
            return None

        prompt = self._build_prompt(sensor_text)

        try:
            response = await self._model.generate_content_async(prompt)
            text = response.text.strip()
            text = re.sub(r'^```(?:json)?\s*', '', text)
            text = re.sub(r'\s*```$', '', text)
            return json.loads(text)
        except json.JSONDecodeError:
            logger.debug(
                f"Agent {self.agent_id} ({self.role.value}) async JSON parse "
                f"failed. Raw: {text[:500]}")
            return None
        except Exception as e:
            logger.debug(
                f"Agent {self.agent_id} ({self.role.value}) async LLM call "
                f"failed: {e}")
            return None

    # ---- decision creation ----

    def _create_decision(self, action_dict: dict) -> Optional[AgentDecision]:
        try:
            reasoning_text = action_dict.get("reasoning", "No reasoning")
            reasoning_hash = hashlib.sha256(
                reasoning_text.encode()).hexdigest()

            tx = Transaction(
                agent_id=self.agent_id,
                action=action_dict["command"],
                target_device=action_dict["device_id"],
                params=action_dict.get("params", {}),
                confidence=float(action_dict.get("confidence", 0.5)),
                reasoning_hash=reasoning_hash,
            )
            tx.signature = sign_data(self._private_key, tx.payload_bytes())

            return AgentDecision(
                transaction=tx,
                reasoning_text=reasoning_text,
                reasoning_hash=reasoning_hash,
            )
        except (KeyError, TypeError, ValueError):
            return None
