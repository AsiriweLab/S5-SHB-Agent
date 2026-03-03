"""
ML/DL Anomaly Detection Agent (NEW POC8).

Uses trained ML/DL models (NOT LLM) to detect anomalies in device telemetry
and propose corrective actions.
"""

import hashlib
from typing import List, Optional, Tuple

from blockchain import Transaction, sign_data
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from agent import AgentDecision
from anomaly_models import AnomalyModelSuite, AnomalyResult


# ---------------------------------------------------------------------------
# Corrective Action Mapping
# ---------------------------------------------------------------------------

CORRECTIVE_ACTIONS = {
    "thermostat": {
        "action": "turn_off",
        "params": {},
        "confidence": 0.90,
        "reasoning": "Anomalous temperature reading detected by ML models -- "
                     "turning off thermostat as precaution",
    },
    "smart_plug": {
        "action": "set_mode",
        "params": {"mode": "eco"},
        "confidence": 0.85,
        "reasoning": "Power consumption anomaly detected by ML models -- "
                     "switching to eco mode",
    },
    "smart_appliance": {
        "action": "trigger_maintenance_mode",
        "params": {},
        "confidence": 0.90,
        "reasoning": "Appliance degradation anomaly detected by ML models -- "
                     "triggering maintenance mode",
    },
    "hvac": {
        "action": "set_mode",
        "params": {"mode": "auto"},
        "confidence": 0.80,
        "reasoning": "HVAC operating anomaly detected by ML models -- "
                     "switching to auto mode",
    },
    "smoke_sensor": {
        "action": "silence_alarm",
        "params": {},
        "confidence": 0.70,
        "reasoning": "Anomalous smoke sensor reading detected -- "
                     "possible sensor malfunction",
    },
    "gas_sensor": {
        "action": "silence_alarm",
        "params": {},
        "confidence": 0.75,
        "reasoning": "Anomalous gas sensor reading detected by ML models -- "
                     "possible sensor drift or calibration issue",
    },
}

# Generic fallback for device types not in CORRECTIVE_ACTIONS
GENERIC_CORRECTIVE_ACTION = {
    "action": "turn_off",
    "params": {},
    "confidence": 0.70,
    "reasoning": "Anomalous readings detected by ML models -- "
                 "turning off device as precaution",
}


# ---------------------------------------------------------------------------
# Anomaly Detection Agent
# ---------------------------------------------------------------------------

class AnomalyDetectionAgent:
    """ML/DL-based anomaly detection agent."""

    def __init__(self, agent_id: str, private_key: Ed25519PrivateKey,
                 dl_enabled: bool = False,
                 iforest_threshold: float = -0.5,
                 zscore_threshold: float = 2.5):
        self.agent_id = agent_id
        self._private_key = private_key
        self.model_suite = AnomalyModelSuite(
            dl_enabled=dl_enabled,
            iforest_threshold=iforest_threshold,
            zscore_threshold=zscore_threshold,
        )
        self._trained = False

    def accumulate_telemetry(self, telemetry_list):
        """Accumulate one round of telemetry for training."""
        self.model_suite.accumulate(telemetry_list)

    def train(self) -> dict:
        """Train all models on accumulated normal telemetry."""
        result = self.model_suite.train()
        self._trained = self.model_suite.trained
        return result

    def detect_and_decide(self, telemetry_list
                          ) -> Tuple[List[AnomalyResult], List[AgentDecision]]:
        """Detect anomalies and propose corrective actions.

        Returns (anomaly_results, corrective_decisions).
        """
        results = self.model_suite.detect(telemetry_list)
        decisions = []

        for anomaly in results:
            if anomaly.is_anomaly:
                dec = self._anomaly_to_decision(anomaly)
                if dec:
                    decisions.append(dec)

        return results, decisions

    def _anomaly_to_decision(self, anomaly: AnomalyResult
                             ) -> Optional[AgentDecision]:
        """Convert an anomaly detection into a corrective AgentDecision."""
        mapping = CORRECTIVE_ACTIONS.get(
            anomaly.device_type, GENERIC_CORRECTIVE_ACTION)

        reasoning = (f"{mapping['reasoning']} "
                     f"(score={anomaly.anomaly_score:.3f}, "
                     f"detectors={anomaly.detectors_triggered})")
        reasoning_hash = hashlib.sha256(reasoning.encode()).hexdigest()

        try:
            tx = Transaction(
                agent_id=self.agent_id,
                action=mapping["action"],
                target_device=anomaly.device_id,
                params=mapping["params"],
                confidence=mapping["confidence"],
                reasoning_hash=reasoning_hash,
            )
            tx.signature = sign_data(self._private_key, tx.payload_bytes())

            return AgentDecision(
                transaction=tx,
                reasoning_text=reasoning,
                reasoning_hash=reasoning_hash,
            )
        except (KeyError, TypeError, ValueError):
            return None

    @property
    def trained(self) -> bool:
        return self._trained

    def update_thresholds(self, zscore_threshold: float,
                          iforest_threshold: float) -> None:
        """Update anomaly detection thresholds at runtime."""
        self.model_suite.set_thresholds(zscore_threshold, iforest_threshold)

    def training_summary(self) -> dict:
        return self.model_suite.training_summary()
