"""
Home Adapter -- Creates DeviceLayer from S5-HES-Agent home configuration.

All devices are created as HESDevice instances. S5-HES-Agent provides
118 device types with type-specific telemetry; device types flow through
unchanged with no mapping or coercion.
"""

from __future__ import annotations

from typing import Any

from engine.devices import DeviceLayer, HESDevice


# ---------------------------------------------------------------------------
# Main entry point: create DeviceLayer from S5-HES home data
# ---------------------------------------------------------------------------

def create_device_layer_from_home(
    home_devices: list[dict[str, Any]],
    home_rooms: list[dict[str, Any]] | None = None,
) -> tuple[DeviceLayer, dict[str, Any]]:
    """Create a DeviceLayer from S5-HES-Agent home device data.

    All devices are created as HESDevice instances -- the universal device
    class. HES device types flow through unchanged (no mapping to the old
    10 engine classes).

    Args:
        home_devices: List of device dicts from GET /api/simulation/home/devices.
            Each dict has: id, name, device_type, room_id, status, properties.
        home_rooms: Optional list of room dicts from GET /api/simulation/home/rooms.
            Used for room name lookup (room_id -> room name).

    Returns:
        Tuple of (DeviceLayer, mapping_report) where mapping_report has stats.
    """
    # Build room_id -> room name/type lookup
    room_lookup: dict[str, str] = {}
    if home_rooms:
        for room in home_rooms:
            rid = room.get("id", room.get("room_id", ""))
            rname = room.get("name", room.get("room_type", rid))
            room_lookup[rid] = rname

    layer = DeviceLayer()
    created_count = 0
    skipped_count = 0
    type_counts: dict[str, int] = {}

    for dev_data in home_devices:
        hes_type = dev_data.get("device_type", "")
        device_id = dev_data.get("id", "")
        room_id = dev_data.get("room_id", "unknown")
        room_name = room_lookup.get(room_id, room_id)

        if not hes_type or not device_id:
            skipped_count += 1
            continue

        device = HESDevice(
            device_id=device_id,
            device_type=hes_type,
            room=room_name,
            hes_device_type=hes_type,
        )

        # Apply initial properties to device state
        props = dev_data.get("properties", {})
        if props:
            device.state.update(props)

        layer.add(device)
        created_count += 1
        type_counts[hes_type] = type_counts.get(hes_type, 0) + 1

    report = {
        "total_hes_devices": len(home_devices),
        "mapped_devices": created_count,
        "engine_devices": 0,
        "hes_only_devices": created_count,
        "skipped_devices": skipped_count,
        "skipped_types": [],
        "device_type_counts": type_counts,
    }

    return layer, report


# ---------------------------------------------------------------------------
# Dual-mode: create DeviceLayer with real device adapters
# ---------------------------------------------------------------------------

def create_device_layer_with_config(
    home_devices: list[dict[str, Any]],
    home_rooms: list[dict[str, Any]] | None = None,
    device_config: Any = None,
) -> tuple[DeviceLayer, dict[str, Any]]:
    """Create a DeviceLayer respecting device mode configuration.

    In SIMULATION mode: identical to create_device_layer_from_home()
    (S5-HES behavioral simulation).
    In REAL mode: all devices created via protocol adapters.
    In HYBRID mode: devices listed in device_config.real_devices use
    adapters; all others use S5-HES simulation.

    Args:
        home_devices: Device list from S5-HES home configuration.
        home_rooms: Room list from S5-HES home configuration.
        device_config: SessionDeviceConfig (from engine.device_config).

    Returns:
        Tuple of (DeviceLayer, mapping_report).
    """
    from engine.device_config import DeviceMode, SessionDeviceConfig

    # Default to simulation if no config
    if device_config is None:
        return create_device_layer_from_home(home_devices, home_rooms)

    if not isinstance(device_config, SessionDeviceConfig):
        return create_device_layer_from_home(home_devices, home_rooms)

    if device_config.mode == DeviceMode.SIMULATION:
        return create_device_layer_from_home(home_devices, home_rooms)

    # REAL or HYBRID mode — need the adapter framework
    from engine.adapters import AdapterRegistry

    if device_config.mode == DeviceMode.REAL:
        # All devices created via protocol adapters
        layer = DeviceLayer()
        real_count = 0
        type_counts: dict[str, int] = {}
        for conn_config in device_config.real_devices:
            adapter = AdapterRegistry.create(conn_config)
            layer.add(adapter)
            real_count += 1
            dt = conn_config.device_type
            type_counts[dt] = type_counts.get(dt, 0) + 1

        report = {
            "total_hes_devices": len(home_devices),
            "mapped_devices": real_count,
            "skipped_devices": 0,
            "skipped_types": [],
            "device_type_counts": type_counts,
            "real_devices": real_count,
            "simulated_devices": 0,
            "mode": "real",
        }
        return layer, report

    # HYBRID mode — S5-HES simulated devices + real adapters
    # First, create the full simulation layer from S5-HES data
    layer, report = create_device_layer_from_home(home_devices, home_rooms)

    # Replace devices that have real adapter configs
    replaced_count = 0
    for conn_config in device_config.real_devices:
        dev_id = conn_config.device_id
        # Remove simulated version if it exists
        if dev_id in layer.devices:
            del layer.devices[dev_id]
        # Add real adapter in its place
        adapter = AdapterRegistry.create(conn_config)
        layer.add(adapter)
        replaced_count += 1

    report["real_devices"] = replaced_count
    report["simulated_devices"] = report["mapped_devices"] - replaced_count
    report["mode"] = "hybrid"

    return layer, report
