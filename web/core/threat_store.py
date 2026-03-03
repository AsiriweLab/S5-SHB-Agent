"""
Threat configuration store.

Manages threat scenarios configured by the user for simulation injection.
Threats are kept in memory and sent to S5-HES-Agent when simulation starts.
Threat types aligned to S5-HES-Agent ThreatCatalog (22 types, 7 categories).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from loguru import logger


# ---------------------------------------------------------------------------
# Threat types -- aligned to S5-HES-Agent ThreatCatalog (22 types, 7 categories)
# ---------------------------------------------------------------------------

THREAT_TYPES: list[dict[str, Any]] = [
    # DATA_THEFT
    {"id": "data_exfiltration", "name": "Data Exfiltration", "category": "data_theft",
     "severity_default": "high",
     "description": "Unauthorized extraction of sensitive data from smart home devices"},
    {"id": "credential_theft", "name": "Credential Theft", "category": "data_theft",
     "severity_default": "high",
     "description": "Stealing authentication credentials from devices or users"},
    {"id": "sensor_data_interception", "name": "Sensor Data Interception", "category": "data_theft",
     "severity_default": "medium",
     "description": "Intercepting sensor readings in transit"},
    # DEVICE_COMPROMISE
    {"id": "device_tampering", "name": "Device Tampering", "category": "device_compromise",
     "severity_default": "high",
     "description": "Physical or logical manipulation of device behavior"},
    {"id": "firmware_modification", "name": "Firmware Modification", "category": "device_compromise",
     "severity_default": "critical",
     "description": "Unauthorized alteration of device firmware"},
    {"id": "botnet_recruitment", "name": "Botnet Recruitment", "category": "device_compromise",
     "severity_default": "high",
     "description": "Recruiting smart home devices into a botnet"},
    {"id": "ransomware", "name": "Ransomware", "category": "device_compromise",
     "severity_default": "critical",
     "description": "Ransomware attack locking device functionality"},
    # SERVICE_DISRUPTION
    {"id": "denial_of_service", "name": "Denial of Service", "category": "service_disruption",
     "severity_default": "medium",
     "description": "DoS attack on smart home infrastructure"},
    {"id": "jamming", "name": "Jamming", "category": "service_disruption",
     "severity_default": "medium",
     "description": "Wireless signal jamming disrupting device communication"},
    {"id": "resource_exhaustion", "name": "Resource Exhaustion", "category": "service_disruption",
     "severity_default": "medium",
     "description": "Exhausting device computational resources"},
    # PHYSICAL_IMPACT
    {"id": "unauthorized_access", "name": "Unauthorized Access", "category": "physical_impact",
     "severity_default": "critical",
     "description": "Unauthorized physical access via smart lock/door manipulation"},
    {"id": "safety_system_bypass", "name": "Safety System Bypass", "category": "physical_impact",
     "severity_default": "critical",
     "description": "Bypassing safety systems (smoke detectors, alarms)"},
    {"id": "hvac_manipulation", "name": "HVAC Manipulation", "category": "physical_impact",
     "severity_default": "medium",
     "description": "Manipulating HVAC systems for physical discomfort or damage"},
    # ENERGY_FRAUD
    {"id": "energy_theft", "name": "Energy Theft", "category": "energy_fraud",
     "severity_default": "medium",
     "description": "Manipulating energy consumption readings"},
    {"id": "meter_tampering", "name": "Meter Tampering", "category": "energy_fraud",
     "severity_default": "high",
     "description": "Tampering with smart meter readings"},
    {"id": "usage_falsification", "name": "Usage Falsification", "category": "energy_fraud",
     "severity_default": "medium",
     "description": "Falsifying energy usage data"},
    # PRIVACY_VIOLATION
    {"id": "surveillance", "name": "Surveillance", "category": "privacy_violation",
     "severity_default": "high",
     "description": "Unauthorized video/audio surveillance via cameras and microphones"},
    {"id": "location_tracking", "name": "Location Tracking", "category": "privacy_violation",
     "severity_default": "medium",
     "description": "Tracking inhabitant location patterns"},
    {"id": "behavior_profiling", "name": "Behavior Profiling", "category": "privacy_violation",
     "severity_default": "medium",
     "description": "Profiling inhabitant behavior from device usage"},
    # NETWORK_ATTACK
    {"id": "man_in_the_middle", "name": "Man in the Middle", "category": "network_attack",
     "severity_default": "high",
     "description": "Intercepting and modifying network communication"},
    {"id": "dns_spoofing", "name": "DNS Spoofing", "category": "network_attack",
     "severity_default": "high",
     "description": "Redirecting DNS queries to malicious endpoints"},
    {"id": "arp_poisoning", "name": "ARP Poisoning", "category": "network_attack",
     "severity_default": "high",
     "description": "Poisoning ARP tables for traffic interception"},
]

THREAT_TYPE_IDS = {t["id"] for t in THREAT_TYPES}


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------

@dataclass
class ThreatConfig:
    id: str
    name: str
    threat_type: str
    target_device: str = ""
    severity: str = "medium"
    parameters: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# ThreatStore
# ---------------------------------------------------------------------------

class ThreatStore:
    """In-memory store for configured threat scenarios."""

    def __init__(self) -> None:
        self._threats: list[ThreatConfig] = []

    def get_threats(self) -> list[ThreatConfig]:
        return list(self._threats)

    def get_threat(self, threat_id: str) -> ThreatConfig | None:
        for t in self._threats:
            if t.id == threat_id:
                return t
        return None

    def add_threat(self, config: ThreatConfig) -> None:
        self._threats.append(config)
        logger.debug(f"Threat added: {config.name} ({config.threat_type})")

    def update_threat(
        self, threat_id: str, updates: dict[str, Any]
    ) -> ThreatConfig | None:
        for t in self._threats:
            if t.id == threat_id:
                for key, val in updates.items():
                    if hasattr(t, key) and key != "id":
                        setattr(t, key, val)
                return t
        return None

    def remove_threat(self, threat_id: str) -> bool:
        before = len(self._threats)
        self._threats = [t for t in self._threats if t.id != threat_id]
        return len(self._threats) < before

    def clear(self) -> None:
        self._threats.clear()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_threat_store: Optional[ThreatStore] = None


def get_threat_store() -> ThreatStore:
    """Get or create the global ThreatStore singleton."""
    global _threat_store
    if _threat_store is None:
        _threat_store = ThreatStore()
    return _threat_store
