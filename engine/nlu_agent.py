"""
NLU Agent -- Natural Language Understanding for Voice/Text Commands (POC10: +governance).

Accepts user text (or transcribed voice), parses intent via Gemini,
produces AgentDecision objects for blockchain submission.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from loguru import logger

from blockchain import Transaction, sign_data
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from agent import AgentDecision


# ---------------------------------------------------------------------------
# Parsed Intent
# ---------------------------------------------------------------------------

@dataclass
class ParsedIntent:
    intent_type: str   # "command", "query", "multi", "preference", "unknown"
    actions: List[dict] = field(default_factory=list)
    query_response: str = ""
    confidence: float = 0.0
    raw_json: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# NLU System Prompt (device catalog is injected dynamically)
# ---------------------------------------------------------------------------

NLU_SYSTEM_PROMPT = """\
You are a NATURAL LANGUAGE UNDERSTANDING agent for a smart home system.
Your job is to parse user voice/text commands into device actions.

{device_catalog}

RULES:
- Respond ONLY with a valid JSON object with this structure:
  {{
    "intent_type": "command" | "query" | "multi" | "preference" | "unknown",
    "actions": [{{"device_id": "...", "command": "...", "params": {{}}, "confidence": 0.0-1.0}}],
    "query_response": "..." (only for queries, otherwise ""),
    "confidence": 0.0-1.0
  }}
- For commands like "Turn on the living room light": map to device_id + command.
- For queries like "What's the temperature?": set intent_type="query", populate query_response from telemetry.
- For ambiguous commands like "make it warmer": use conversation context to resolve device.
- For pronouns ("turn it off"): resolve "it" to the last mentioned device from context.
- If you cannot parse the command, set intent_type="unknown" with confidence=0.0.

GOVERNANCE COMMANDS (Society 5.0):
- For preference/governance changes, set intent_type="preference"
  actions: [{{"preference_key": "...", "new_value": ..., "confidence": 0.9}}]
  Examples:
    "I'm cold" / "make it warmer" -> preference_key="preferred_temp", new_value=24.0
    "Save energy" -> preference_key="comfort_vs_energy", new_value=0.3
    "More privacy" -> preference_key="security_vs_privacy", new_value=0.2
    "Let me decide" -> preference_key="confirmation_mode", new_value="always"
    "Full auto" -> preference_key="automation_level", new_value="auto"
"""


def _infer_commands(device_type: str, readings: dict) -> list:
    """Infer available commands from device type name and current readings."""
    cmds = ["turn_on", "turn_off"]

    if "target_temp" in readings or "current_temp" in readings:
        cmds.append('set_temperature {temperature: N}')
    if "brightness" in readings:
        cmds.append('set_brightness {brightness: 0-100}')
    if "is_locked" in readings:
        cmds.extend(["lock", "unlock"])
    if "is_recording" in readings:
        cmds.extend(["start_recording", "stop_recording"])
    if "mode" in readings:
        cmds.append('set_mode {mode: ...}')
    if "fan_speed" in readings or "fan_mode" in readings:
        cmds.append('set_fan_speed {fan_speed: low|medium|high}')
    if "smoke_level" in readings or "gas_level_ppm" in readings:
        cmds.append("silence_alarm")
    if "alarm_active" in readings:
        cmds.append("silence_alarm")
    if any("motion" in k for k in readings):
        cmds.append("reset")

    return cmds


# ---------------------------------------------------------------------------
# NLU Agent
# ---------------------------------------------------------------------------

class NLUAgent:
    """Gemini-backed NLU agent for voice/text command processing."""

    def __init__(self, agent_id: str, private_key: Ed25519PrivateKey,
                 api_key: str = "", model_name: str = "gemini-2.0-flash",
                 model_router=None):
        self.agent_id = agent_id
        self._private_key = private_key
        self._api_key = api_key
        self._model_name = model_name
        self._model = None
        self._model_router = model_router   # NEW POC10

    def process_command(self, user_text: str,
                        telemetry_list=None,
                        conversation_context: str = ""
                        ) -> Tuple[ParsedIntent, List[AgentDecision]]:
        """Parse user text into intent + decisions.

        Returns (ParsedIntent, List[AgentDecision]).
        """
        prompt = self._build_prompt(user_text, telemetry_list,
                                    conversation_context)
        raw = self._call_llm(prompt)

        if not raw:
            intent = ParsedIntent(intent_type="unknown", confidence=0.0)
            return intent, []

        intent = ParsedIntent(
            intent_type=raw.get("intent_type", "unknown"),
            actions=raw.get("actions", []),
            query_response=raw.get("query_response", ""),
            confidence=float(raw.get("confidence", 0.0)),
            raw_json=raw,
        )

        decisions = []
        for act in intent.actions:
            dec = self._create_decision(act)
            if dec:
                decisions.append(dec)

        return intent, decisions

    @staticmethod
    def _build_device_catalog(telemetry_list) -> str:
        """Build device catalog from live telemetry for the LLM prompt."""
        if not telemetry_list:
            return "AVAILABLE DEVICES: None currently registered."

        by_type: dict = {}
        for t in telemetry_list:
            by_type.setdefault(t.device_type, []).append(t)

        lines = ["AVAILABLE DEVICES AND COMMANDS:"]
        for dtype, devs in sorted(by_type.items()):
            ids = ", ".join(d.device_id for d in devs)
            cmds = _infer_commands(dtype, devs[0].readings if devs else {})
            lines.append(f"  {dtype} ({ids}): {', '.join(cmds)}")

        return "\n".join(lines)

    def _build_prompt(self, user_text: str, telemetry_list=None,
                      conversation_context: str = "") -> str:
        device_catalog = self._build_device_catalog(telemetry_list)
        prompt = NLU_SYSTEM_PROMPT.format(device_catalog=device_catalog)

        if conversation_context:
            prompt += f"\n\n{conversation_context}"

        if telemetry_list:
            prompt += "\n\nCURRENT DEVICE TELEMETRY:\n"
            for t in telemetry_list:
                parts = [f"{k}={v}" for k, v in t.readings.items()]
                prompt += f"  [{t.device_type}:{t.device_id}] {', '.join(parts)}\n"

        prompt += f'\n\nUSER COMMAND: "{user_text}"\n\nRespond with JSON only:'
        return prompt

    def _call_llm(self, prompt: str) -> Optional[dict]:
        # NEW POC10: Route through model router if available
        if self._model_router:
            try:
                text = self._model_router.call(self.agent_id, prompt)
                if not text:
                    logger.warning("NLU LLM call returned empty via model router")
                    return None
                text = re.sub(r'^```(?:json)?\s*', '', text)
                text = re.sub(r'\s*```$', '', text)
                return json.loads(text)
            except Exception as e:
                logger.warning(f"NLU LLM call failed via model router: {e}")
                return None

        # Existing direct-Gemini path (backward compat)
        if self._model is None:
            self._model = self._init_gemini()
        if self._model is None:
            logger.warning("NLU: Gemini model failed to initialize")
            return None
        try:
            response = self._model.generate_content(prompt)
            text = response.text.strip()
            text = re.sub(r'^```(?:json)?\s*', '', text)
            text = re.sub(r'\s*```$', '', text)
            return json.loads(text)
        except Exception as e:
            logger.warning(f"NLU direct Gemini call failed: {e}")
            return None

    def _init_gemini(self):
        try:
            import google.generativeai as genai
            genai.configure(api_key=self._api_key)
            return genai.GenerativeModel(self._model_name)
        except Exception:
            return None

    def _create_decision(self, action_dict: dict) -> Optional[AgentDecision]:
        try:
            reasoning_text = (f"NLU parsed user command -> "
                              f"{action_dict['command']} on {action_dict['device_id']}")
            reasoning_hash = hashlib.sha256(
                reasoning_text.encode()).hexdigest()

            tx = Transaction(
                agent_id=self.agent_id,
                action=action_dict["command"],
                target_device=action_dict["device_id"],
                params=action_dict.get("params", {}),
                confidence=float(action_dict.get("confidence", 0.7)),
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
