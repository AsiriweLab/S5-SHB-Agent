"""
Session Manager -- Save/Restore/List/Select blockchain sessions (NEW POC10).

Session directory layout:
  sessions/<name>/blockchain.json
  sessions/<name>/offchain.db
  sessions/<name>/agent_keys.json      (Ed25519 PEM -- DEMO ONLY, not production-safe)
  sessions/<name>/session_meta.json
  sessions/<name>/preferences.json
  sessions/<name>/model_assignments.json
"""

import json
import os
import shutil
import time
from datetime import datetime
from typing import Dict, List, Optional

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding, PrivateFormat, PublicFormat, NoEncryption,
    load_pem_private_key,
)


class SessionManager:
    """Manages named sessions in sessions/<name>/ directories."""

    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        self.sessions_dir = os.path.join(base_dir, "sessions")
        os.makedirs(self.sessions_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def list_sessions(self) -> List[dict]:
        """Return list of session metadata dicts sorted by last_run desc."""
        sessions = []
        if not os.path.isdir(self.sessions_dir):
            return sessions
        for name in os.listdir(self.sessions_dir):
            meta_path = os.path.join(self.sessions_dir, name, "session_meta.json")
            if os.path.isfile(meta_path):
                try:
                    with open(meta_path, "r") as f:
                        meta = json.load(f)
                    meta["name"] = name
                    # Infer device_mode for older sessions missing the field
                    if "device_mode" not in meta:
                        dc_path = os.path.join(
                            self.sessions_dir, name, "device_config.json"
                        )
                        if os.path.isfile(dc_path):
                            try:
                                with open(dc_path, "r") as dc_f:
                                    dc = json.load(dc_f)
                                meta["device_mode"] = dc.get("mode", "simulation")
                            except (json.JSONDecodeError, OSError):
                                meta["device_mode"] = "simulation"
                        else:
                            meta["device_mode"] = "simulation"
                    sessions.append(meta)
                except (json.JSONDecodeError, OSError):
                    pass
        sessions.sort(key=lambda s: s.get("last_run", 0), reverse=True)
        return sessions

    def session_exists(self, name: str) -> bool:
        """Check if a named session directory exists with valid metadata."""
        return os.path.isfile(os.path.join(self.sessions_dir, name,
                                           "session_meta.json"))

    def most_recent_session(self) -> Optional[str]:
        """Return the name of the most recently used session, or None."""
        sessions = self.list_sessions()
        return sessions[0]["name"] if sessions else None

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    def session_dir(self, name: str) -> str:
        return os.path.join(self.sessions_dir, name)

    def blockchain_path(self, name: str) -> str:
        return os.path.join(self.session_dir(name), "blockchain.json")

    def offchain_path(self, name: str) -> str:
        return os.path.join(self.session_dir(name), "offchain.db")

    def keys_path(self, name: str) -> str:
        return os.path.join(self.session_dir(name), "agent_keys.json")

    def meta_path(self, name: str) -> str:
        return os.path.join(self.session_dir(name), "session_meta.json")

    def preferences_path(self, name: str) -> str:
        return os.path.join(self.session_dir(name), "preferences.json")

    def model_assignments_path(self, name: str) -> str:
        return os.path.join(self.session_dir(name), "model_assignments.json")

    # ------------------------------------------------------------------
    # Create / Delete
    # ------------------------------------------------------------------

    def create_session(self, name: str, device_mode: str = "simulation") -> str:
        """Create a new empty session directory. Returns session dir path."""
        sdir = self.session_dir(name)
        os.makedirs(sdir, exist_ok=True)
        meta = {
            "created": time.time(),
            "created_iso": datetime.now().isoformat(),
            "last_run": time.time(),
            "last_run_iso": datetime.now().isoformat(),
            "poc_version": "POC10",
            "blocks": 0,
            "scenarios_run": 0,
            "device_mode": device_mode,
        }
        with open(self.meta_path(name), "w") as f:
            json.dump(meta, f, indent=2)
        return sdir

    def delete_session(self, name: str) -> bool:
        """Delete a session directory entirely. Returns True if deleted."""
        sdir = self.session_dir(name)
        if os.path.isdir(sdir):
            shutil.rmtree(sdir)
            return True
        return False

    # ------------------------------------------------------------------
    # Agent key persistence (Ed25519 PEM -- DEMO ONLY)
    # ------------------------------------------------------------------

    def save_agent_keys(self, name: str,
                        agent_keys: Dict[str, Ed25519PrivateKey]):
        """Serialize Ed25519 private keys to PEM.

        WARNING: Keys stored unencrypted. Demo/research use only.
        Production systems must use encrypted keystores or HSMs.
        """
        keys_data = {}
        for agent_id, privkey in agent_keys.items():
            pem_bytes = privkey.private_bytes(
                Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
            )
            pub_bytes = privkey.public_key().public_bytes(
                Encoding.Raw, PublicFormat.Raw
            )
            keys_data[agent_id] = {
                "private_key_pem": pem_bytes.decode("utf-8"),
                "public_key_hex": pub_bytes.hex(),
            }
        with open(self.keys_path(name), "w") as f:
            json.dump(keys_data, f, indent=2)

    def load_agent_keys(self, name: str) -> Dict[str, Ed25519PrivateKey]:
        """Deserialize Ed25519 private keys from PEM format."""
        path = self.keys_path(name)
        if not os.path.isfile(path):
            return {}
        with open(path, "r") as f:
            keys_data = json.load(f)
        agent_keys = {}
        for agent_id, kd in keys_data.items():
            privkey = load_pem_private_key(
                kd["private_key_pem"].encode("utf-8"), password=None
            )
            agent_keys[agent_id] = privkey
        return agent_keys

    # ------------------------------------------------------------------
    # Metadata update
    # ------------------------------------------------------------------

    def update_meta(self, name: str, blocks: int = 0,
                    scenarios_run: int = 0):
        """Update session metadata after a run."""
        path = self.meta_path(name)
        meta = {}
        if os.path.isfile(path):
            with open(path, "r") as f:
                meta = json.load(f)
        meta["last_run"] = time.time()
        meta["last_run_iso"] = datetime.now().isoformat()
        meta["blocks"] = blocks
        meta["scenarios_run"] = scenarios_run
        with open(path, "w") as f:
            json.dump(meta, f, indent=2)
