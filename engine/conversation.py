"""
Conversation Manager for NLU Agent (NEW POC8).

Tracks multi-turn conversation context so the NLU agent can resolve
pronouns ("it", "that") and follow-up commands ("make it warmer").
"""

import time
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class ConversationTurn:
    timestamp: float
    user_text: str
    intent_type: str                    # command, query, multi, unknown
    actions: List[dict] = field(default_factory=list)
    response: str = ""
    devices_mentioned: List[str] = field(default_factory=list)


class ConversationManager:
    """Stores conversation history for pronoun resolution and context."""

    def __init__(self, max_turns: int = 10):
        self._turns: List[ConversationTurn] = []
        self._max_turns = max_turns

    @property
    def last_device_id(self) -> Optional[str]:
        """Return the most recently mentioned device (for 'it'/'that')."""
        for turn in reversed(self._turns):
            if turn.devices_mentioned:
                return turn.devices_mentioned[-1]
        return None

    def add_turn(self, user_text: str, intent_type: str,
                 actions: List[dict] = None,
                 response: str = "",
                 devices_mentioned: List[str] = None):
        turn = ConversationTurn(
            timestamp=time.time(),
            user_text=user_text,
            intent_type=intent_type,
            actions=actions or [],
            response=response,
            devices_mentioned=devices_mentioned or [],
        )
        self._turns.append(turn)
        if len(self._turns) > self._max_turns:
            self._turns = self._turns[-self._max_turns:]

    def get_context_string(self, last_n: int = 3) -> str:
        """Return last N turns formatted for LLM prompt injection."""
        if not self._turns:
            return ""
        recent = self._turns[-last_n:]
        lines = ["CONVERSATION CONTEXT (recent turns):"]
        for t in recent:
            lines.append(f"  User: \"{t.user_text}\"")
            if t.response:
                lines.append(f"  System: \"{t.response}\"")
            if t.devices_mentioned:
                lines.append(f"  Devices: {', '.join(t.devices_mentioned)}")
        if self.last_device_id:
            lines.append(f"  Last mentioned device: {self.last_device_id}")
        return "\n".join(lines)

    def get_session_summary(self) -> dict:
        """Summary for audit / logging."""
        intent_counts = {}
        all_devices = set()
        for t in self._turns:
            intent_counts[t.intent_type] = intent_counts.get(t.intent_type, 0) + 1
            all_devices.update(t.devices_mentioned)
        return {
            "total_turns": len(self._turns),
            "intent_distribution": intent_counts,
            "unique_devices": sorted(all_devices),
        }

    def clear(self):
        self._turns.clear()
