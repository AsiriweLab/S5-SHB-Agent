"""
Protocol adapters for real device communication.

Provides the adapter framework for dual-mode device support:
- RealDeviceAdapter: Abstract base extending SmartDevice
- AdapterRegistry: Maps protocol names to adapter classes
- MockDeviceAdapter: For testing without hardware
- MQTTDeviceAdapter: For MQTT-based IoT devices
- HTTPDeviceAdapter: For HTTP REST-based IoT devices
"""

from engine.adapters.base import RealDeviceAdapter, AdapterRegistry

# Import adapters to trigger auto-registration
from engine.adapters.mock_adapter import MockDeviceAdapter

# MQTT adapter (optional dependency: paho-mqtt)
try:
    from engine.adapters.mqtt_adapter import MQTTDeviceAdapter
except ImportError:
    MQTTDeviceAdapter = None  # type: ignore[assignment, misc]

# HTTP adapter (uses requests, always available)
from engine.adapters.http_adapter import HTTPDeviceAdapter

__all__ = [
    "RealDeviceAdapter",
    "AdapterRegistry",
    "MockDeviceAdapter",
    "MQTTDeviceAdapter",
    "HTTPDeviceAdapter",
]
