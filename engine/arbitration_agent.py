"""
Arbitration Agent -- Intelligent Conflict Resolution (NEW POC8).

Replaces blind priority-based conflict resolution with context-aware
arbitration using LLM reasoning + ML scoring from historical outcomes.

IMPORTANT: Safety agent (priority 1.0) is NEVER overridden by arbitration.
"""

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from typing import List, Optional, Dict

from blockchain import Transaction, sign_data
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from agent import AgentDecision


# ---------------------------------------------------------------------------
# Arbitration Result
# ---------------------------------------------------------------------------

@dataclass
class ArbitrationResult:
    winner: AgentDecision
    losers: List[AgentDecision] = field(default_factory=list)
    reasoning: str = ""
    method: str = "priority_fallback"  # safety_override | llm_arbitration | ml_scoring | priority_fallback
    confidence: float = 0.0
    scores: Dict[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Arbitration System Prompt
# ---------------------------------------------------------------------------

ARBITRATION_PROMPT = """\
You are an ARBITRATION agent for a smart home. Multiple agents have proposed \
conflicting actions on the same device. You must decide which action should win.

RULES:
- Respond ONLY with a valid JSON object:
  {{"winner_agent_id": "...", "reasoning": "...", "confidence": 0.0-1.0, \
"scores": {{"agent_id": score, ...}}}}
- Consider: current device state, time of day, safety implications, \
energy efficiency, occupant comfort.
- NEVER override safety-agent-001 (this is handled before you are called).
- Prefer actions with higher confidence when context is ambiguous.
- If one agent has a clear contextual advantage, pick it even if lower priority.
"""


# ---------------------------------------------------------------------------
# ML Outcome Scorer
# ---------------------------------------------------------------------------

class OutcomeScorer:
    """Scores proposals using historical outcome data from the off-chain store."""

    def __init__(self):
        self._agent_scores: Dict[str, float] = {}
        self._trained = False

    def train(self, store) -> dict:
        """Train on historical conflict outcomes from agent_decision_outcomes."""
        from config import AGENT_DEFINITIONS
        for agent_id in AGENT_DEFINITIONS:
            stats = store.get_outcome_stats(agent_id)
            if stats["total"] > 0:
                # Score = acceptance_rate * (1 - conflict_rate) + 0.1 * confidence_bonus
                acceptance = stats["acceptance_rate"]
                conflict_penalty = stats["conflict_rate"]
                self._agent_scores[agent_id] = round(
                    acceptance * (1.0 - conflict_penalty * 0.5), 3)
            else:
                self._agent_scores[agent_id] = 0.5  # neutral default
        self._trained = bool(self._agent_scores)
        return {
            "trained": self._trained,
            "agent_scores": dict(self._agent_scores),
        }

    def score(self, agent_id: str) -> float:
        """Return historical success score for an agent (0-1)."""
        if not self._trained:
            return 0.5
        return self._agent_scores.get(agent_id, 0.5)

    @property
    def trained(self) -> bool:
        return self._trained


# ---------------------------------------------------------------------------
# Arbitration Agent
# ---------------------------------------------------------------------------

class ArbitrationAgent:
    """Intelligent conflict arbitration using LLM + ML scoring."""

    def __init__(self, agent_id: str, private_key: Ed25519PrivateKey,
                 api_key: str = "", model_name: str = "gemini-2.0-flash",
                 safety_immune: bool = True,
                 model_router=None):
        self.agent_id = agent_id
        self._private_key = private_key
        self._api_key = api_key
        self._model_name = model_name
        self._model = None
        self._model_router = model_router   # NEW POC10
        self._safety_immune = safety_immune
        self.outcome_scorer = OutcomeScorer()
        self.arbitration_log: List[ArbitrationResult] = []

    def train_scorer(self, store):
        """Train the ML outcome scorer on historical data."""
        return self.outcome_scorer.train(store)

    def arbitrate(self, conflicting_decisions: List[AgentDecision],
                  telemetry_list=None,
                  agent_priorities: Dict[str, float] = None
                  ) -> ArbitrationResult:
        """Resolve a conflict between 2+ agent decisions on the same device.

        Arbitration logic (priority order):
          1. Safety override -- if safety-agent involved, safety ALWAYS wins
          2. LLM arbitration -- Gemini analyzes context
          3. ML scoring -- historical outcome success rate
          4. Priority fallback -- existing priority system
        """
        if not conflicting_decisions:
            return None

        priorities = agent_priorities or {}

        # 1. Safety override
        safety_dec = self._check_safety_override(conflicting_decisions)
        if safety_dec:
            losers = [d for d in conflicting_decisions if d is not safety_dec]
            result = ArbitrationResult(
                winner=safety_dec,
                losers=losers,
                reasoning="Safety agent involved -- automatic safety override",
                method="safety_override",
                confidence=1.0,
                scores={d.transaction.agent_id: (
                    1.0 if d is safety_dec else 0.0
                ) for d in conflicting_decisions},
            )
            self.arbitration_log.append(result)
            return result

        # 2. Try LLM arbitration
        llm_result = self._llm_arbitrate(conflicting_decisions, telemetry_list,
                                          priorities)
        if llm_result:
            self.arbitration_log.append(llm_result)
            return llm_result

        # 3. ML scoring fallback
        ml_result = self._ml_arbitrate(conflicting_decisions, priorities)
        if ml_result:
            self.arbitration_log.append(ml_result)
            return ml_result

        # 4. Priority fallback
        result = self._priority_fallback(conflicting_decisions, priorities)
        self.arbitration_log.append(result)
        return result

    def _check_safety_override(self, decisions: List[AgentDecision]
                                ) -> Optional[AgentDecision]:
        """If safety agent is involved and immune, return its decision."""
        if not self._safety_immune:
            return None
        for d in decisions:
            if d.transaction.agent_id == "safety-agent-001":
                return d
        return None

    def _llm_arbitrate(self, decisions: List[AgentDecision],
                       telemetry_list, priorities: Dict[str, float]
                       ) -> Optional[ArbitrationResult]:
        """Use LLM to reason about the conflict context."""
        prompt = self._build_arbitration_prompt(decisions, telemetry_list,
                                                 priorities)

        # NEW POC10: Route through model router if available
        if self._model_router:
            try:
                text = self._model_router.call(self.agent_id, prompt)
                if not text:
                    return None
            except Exception:
                return None
        else:
            if self._model is None:
                self._model = self._init_gemini()
            if self._model is None:
                return None
            try:
                response = self._model.generate_content(prompt)
                text = response.text.strip()
            except Exception:
                return None

        try:
            text = re.sub(r'^```(?:json)?\s*', '', text)
            text = re.sub(r'\s*```$', '', text)
            parsed = json.loads(text)

            winner_id = parsed.get("winner_agent_id", "")
            winner = None
            losers = []
            for d in decisions:
                if d.transaction.agent_id == winner_id:
                    winner = d
                else:
                    losers.append(d)

            if not winner:
                return None

            return ArbitrationResult(
                winner=winner,
                losers=losers,
                reasoning=parsed.get("reasoning", "LLM arbitration"),
                method="llm_arbitration",
                confidence=float(parsed.get("confidence", 0.7)),
                scores=parsed.get("scores", {}),
            )
        except Exception:
            return None

    def _build_arbitration_prompt(self, decisions: List[AgentDecision],
                                   telemetry_list,
                                   priorities: Dict[str, float]) -> str:
        prompt = ARBITRATION_PROMPT
        prompt += "\n\nCONFLICTING PROPOSALS:\n"

        for d in decisions:
            tx = d.transaction
            ml_score = self.outcome_scorer.score(tx.agent_id)
            priority = priorities.get(tx.agent_id, 0.5)
            prompt += (f"  Agent: {tx.agent_id} (priority={priority:.2f}, "
                       f"historical_score={ml_score:.3f})\n"
                       f"    Action: {tx.action} on {tx.target_device}\n"
                       f"    Params: {tx.params}\n"
                       f"    Confidence: {tx.confidence:.2f}\n"
                       f"    Reasoning: {d.reasoning_text}\n\n")

        if telemetry_list:
            prompt += "CURRENT TELEMETRY:\n"
            for t in telemetry_list[:5]:  # limit to avoid token overflow
                parts = [f"{k}={v}" for k, v in t.readings.items()]
                prompt += (f"  [{t.device_type}:{t.device_id}] "
                           f"{', '.join(parts)}\n")

        prompt += f"\nCurrent time: {time.strftime('%H:%M:%S')}\n"
        prompt += "\nRespond with JSON only:"
        return prompt

    def _ml_arbitrate(self, decisions: List[AgentDecision],
                      priorities: Dict[str, float]
                      ) -> Optional[ArbitrationResult]:
        """Use ML scores to pick winner."""
        if not self.outcome_scorer.trained:
            return None

        scored = []
        for d in decisions:
            ml = self.outcome_scorer.score(d.transaction.agent_id)
            priority = priorities.get(d.transaction.agent_id, 0.5)
            # Combined score: 60% ML history + 40% priority
            combined = 0.6 * ml + 0.4 * priority
            scored.append((combined, d))

        scored.sort(key=lambda x: x[0], reverse=True)
        winner_score, winner = scored[0]
        losers = [d for _, d in scored[1:]]
        scores = {d.transaction.agent_id: round(s, 3) for s, d in scored}

        return ArbitrationResult(
            winner=winner,
            losers=losers,
            reasoning=f"ML scoring: winner {winner.transaction.agent_id} "
                      f"(combined={winner_score:.3f})",
            method="ml_scoring",
            confidence=min(winner_score, 1.0),
            scores=scores,
        )

    def _priority_fallback(self, decisions: List[AgentDecision],
                           priorities: Dict[str, float]
                           ) -> ArbitrationResult:
        """Fall back to simple priority-based resolution."""
        sorted_decs = sorted(
            decisions,
            key=lambda d: priorities.get(d.transaction.agent_id, 0.0),
            reverse=True,
        )
        winner = sorted_decs[0]
        losers = sorted_decs[1:]
        scores = {
            d.transaction.agent_id: priorities.get(d.transaction.agent_id, 0.0)
            for d in decisions
        }

        return ArbitrationResult(
            winner=winner,
            losers=losers,
            reasoning=f"Priority fallback: {winner.transaction.agent_id} "
                      f"has highest priority",
            method="priority_fallback",
            confidence=0.6,
            scores=scores,
        )

    def _init_gemini(self):
        try:
            import google.generativeai as genai
            genai.configure(api_key=self._api_key)
            return genai.GenerativeModel(self._model_name)
        except Exception:
            return None
