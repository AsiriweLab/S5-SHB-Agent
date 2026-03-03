"""
Application state for the active session.

Holds references to all core objects for the currently active session.
Only one session can be active at a time (see design doc Section 4.3.1).
"""

from __future__ import annotations

from typing import Any, Optional


class AppState:
    """Holds the active session objects.

    All core object references are None until a session is created
    or resumed via the sessions API (Step 2).
    """

    def __init__(self) -> None:
        self.session_name: str = ""
        self.is_active: bool = False
        self.is_fresh: bool = True
        self.home_config: dict[str, Any] = {}

        # Core objects (all None until session is activated)
        self.mcp: Any = None                  # MCPDeviceClient
        self.chain: Any = None                # Blockchain
        self.agents: dict[str, Any] = {}      # agent_id -> SmartHomeAgent
        self.agent_keys: dict[str, Any] = {}  # agent_id -> private key
        self.store: Any = None                # OffChainStore
        self.health_monitor: Any = None       # MCPHealthMonitor
        self.nlu_agent: Any = None            # NLUAgent
        self.anomaly_agent: Any = None        # AnomalyDetectionAgent
        self.arb_agent: Any = None            # ArbitrationAgent
        self.convo: Any = None                # ConversationManager
        self.preferences: Any = None          # ResidentPreferences
        self.model_router: Any = None         # ModelRouter
        self.gov_contract: Any = None         # GovernanceContract
        self.session_mgr: Any = None          # SessionManager

        # Device mode configuration (dual-mode support)
        self.device_config: Any = None       # SessionDeviceConfig

        # S5-HES-Agent integration
        self.s5_hes_client: Any = None       # S5HESClient
        self.s5_hes_available: bool = False  # True if health check passed
        self.simulation_active: bool = False  # True while simulation running
        self.hes_sync: Any = None            # HESTelemetrySync

        # Auto-run agent cycles (real-mode continuous operation)
        self.auto_run_active: bool = False
        self.auto_run_task: Any = None        # asyncio.Task for the background loop
        self.auto_run_interval: float = 60     # seconds between cycles
        self.auto_run_duration: float = 0      # total seconds to run (0 = unlimited)
        self.auto_run_start_time: float = 0    # time.time() when auto-run started
        self.auto_run_cycles: int = 0         # total cycles completed


# Module-level singleton
_app_state: Optional[AppState] = None


def get_app_state() -> AppState:
    """Get or create the global application state singleton."""
    global _app_state
    if _app_state is None:
        _app_state = AppState()
    return _app_state


def reset_app_state() -> AppState:
    """Reset the application state (used when switching sessions)."""
    global _app_state
    _app_state = AppState()
    return _app_state
