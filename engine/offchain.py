"""
Off-Chain Storage -- 13 Tables (POC10: +governance + model usage)

Tables:
  1. telemetry_continuous      -- high-frequency numeric streams
  2. telemetry_events          -- discrete state changes
  3. telemetry_alerts          -- threshold crossings and anomalies
  4. reasoning_log             -- full LLM reasoning text
  5. telemetry_anchors         -- Merkle roots linking off-chain to on-chain
  6. agent_conflicts           -- conflict resolution log
  7. agent_decision_outcomes   -- decision outcomes for feedback loop (POC7)
  8. mcp_health_log            -- MCP health check snapshots (POC7)
  9. conversation_log          -- NLU interactions (POC8)
  10. anomaly_log              -- Anomaly detections (POC8)
  11. arbitration_log          -- Arbitration decisions (POC8)
  12. governance_log           -- Governance changes (NEW POC10)
  13. model_usage_log          -- Model routing cost tracking (NEW POC10)
"""

import sqlite3
import hashlib
import json
import time
import os
from typing import List, Dict, Any, Optional


# ---------------------------------------------------------------------------
# Merkle Tree
# ---------------------------------------------------------------------------

def merkle_root(data_hashes: List[str]) -> str:
    if not data_hashes:
        return hashlib.sha256(b"empty").hexdigest()
    if len(data_hashes) == 1:
        return data_hashes[0]
    next_level = []
    for i in range(0, len(data_hashes), 2):
        left = data_hashes[i]
        right = data_hashes[i + 1] if i + 1 < len(data_hashes) else left
        combined = hashlib.sha256((left + right).encode()).hexdigest()
        next_level.append(combined)
    return merkle_root(next_level)


def hash_row(table: str, device_id: str, data_json: str,
             timestamp: float) -> str:
    raw = f"{table}|{device_id}|{data_json}|{timestamp}"
    return hashlib.sha256(raw.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Device type classification (reading-based, works with all HES device types)
# ---------------------------------------------------------------------------

# Gas thresholds (duplicated from devices.py to avoid circular import)
GAS_THRESHOLDS = {"CO": 50, "CO2": 5000, "NG": 1000}

# Reading keys that indicate alert-class devices
_ALERT_READING_KEYS = frozenset({
    "smoke_level", "gas_level_ppm", "co_level", "alarm_active",
    "radon_level", "leak_detected",
})

# Reading keys that indicate continuous-measurement devices
_CONTINUOUS_READING_KEYS = frozenset({
    "current_temp", "target_temp", "humidity", "power_watts",
    "voltage", "current_amps", "energy_kwh", "temperature",
    "pressure", "flow_rate", "water_temp",
})

# Device type name patterns for fallback classification
_ALERT_TYPE_PATTERNS = ("smoke", "gas", "co_detector", "radon", "leak", "alarm")
_CONTINUOUS_TYPE_PATTERNS = (
    "thermostat", "plug", "hvac", "meter", "energy",
    "solar", "battery", "ev_charger", "heater",
    "humidifier", "dehumidifier", "ac", "fan",
)


def classify_device(device_type: str, readings: dict = None) -> str:
    """Classify device for off-chain storage routing.

    Uses readings-based heuristics when available, falls back to
    device_type name pattern matching. Works with all HES device types.
    """
    if readings:
        if _ALERT_READING_KEYS & readings.keys():
            return "alert"
        if _CONTINUOUS_READING_KEYS & readings.keys():
            return "continuous"
    # Fallback: classify by device_type name patterns
    dt_lower = device_type.lower()
    if any(p in dt_lower for p in _ALERT_TYPE_PATTERNS):
        return "alert"
    if any(p in dt_lower for p in _CONTINUOUS_TYPE_PATTERNS):
        return "continuous"
    return "event"


# ---------------------------------------------------------------------------
# Off-Chain Store -- 11 Tables
# ---------------------------------------------------------------------------

class OffChainStore:
    """SQLite-backed off-chain storage with 13 specialized tables."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._create_tables()
        self._unanchored_count = self._count_unanchored()
        self._next_batch_id = self._get_next_batch_id()

    def _create_tables(self):
        self.conn.executescript("""
            -- TABLE 1: Continuous numeric streams
            CREATE TABLE IF NOT EXISTS telemetry_continuous (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL,
                device_type TEXT NOT NULL,
                readings_json TEXT NOT NULL,
                timestamp REAL NOT NULL,
                data_hash TEXT NOT NULL,
                batch_id INTEGER DEFAULT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_cont_device
                ON telemetry_continuous(device_id, timestamp);
            CREATE INDEX IF NOT EXISTS idx_cont_batch
                ON telemetry_continuous(batch_id);

            -- TABLE 2: Discrete state-change events
            CREATE TABLE IF NOT EXISTS telemetry_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL,
                device_type TEXT NOT NULL,
                event_type TEXT NOT NULL,
                readings_json TEXT NOT NULL,
                timestamp REAL NOT NULL,
                data_hash TEXT NOT NULL,
                batch_id INTEGER DEFAULT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_event_device
                ON telemetry_events(device_id, timestamp);
            CREATE INDEX IF NOT EXISTS idx_event_type
                ON telemetry_events(event_type);
            CREATE INDEX IF NOT EXISTS idx_event_batch
                ON telemetry_events(batch_id);

            -- TABLE 3: Threshold crossings and anomalies
            CREATE TABLE IF NOT EXISTS telemetry_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL,
                device_type TEXT NOT NULL,
                alert_type TEXT NOT NULL,
                severity TEXT NOT NULL,
                readings_json TEXT NOT NULL,
                timestamp REAL NOT NULL,
                data_hash TEXT NOT NULL,
                batch_id INTEGER DEFAULT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_alert_type
                ON telemetry_alerts(alert_type, severity);
            CREATE INDEX IF NOT EXISTS idx_alert_batch
                ON telemetry_alerts(batch_id);

            -- TABLE 4: Full LLM reasoning text
            CREATE TABLE IF NOT EXISTS reasoning_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reasoning_hash TEXT NOT NULL UNIQUE,
                full_text TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                action TEXT NOT NULL,
                target_device TEXT NOT NULL,
                confidence REAL NOT NULL,
                timestamp REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_reasoning_hash
                ON reasoning_log(reasoning_hash);
            CREATE INDEX IF NOT EXISTS idx_reasoning_agent
                ON reasoning_log(agent_id);

            -- TABLE 5: Merkle roots
            CREATE TABLE IF NOT EXISTS telemetry_anchors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id INTEGER NOT NULL UNIQUE,
                merkle_root TEXT NOT NULL,
                record_count INTEGER NOT NULL,
                continuous_count INTEGER NOT NULL DEFAULT 0,
                event_count INTEGER NOT NULL DEFAULT 0,
                alert_count INTEGER NOT NULL DEFAULT 0,
                first_timestamp REAL NOT NULL,
                last_timestamp REAL NOT NULL,
                block_index INTEGER DEFAULT NULL,
                timestamp REAL NOT NULL
            );

            -- TABLE 6: Agent conflict resolution log (NEW in POC4)
            CREATE TABLE IF NOT EXISTS agent_conflicts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conflict_id TEXT NOT NULL UNIQUE,
                device_id TEXT NOT NULL,
                agent_a_id TEXT NOT NULL,
                agent_a_action TEXT NOT NULL,
                agent_a_priority REAL NOT NULL,
                agent_b_id TEXT NOT NULL,
                agent_b_action TEXT NOT NULL,
                agent_b_priority REAL NOT NULL,
                winner_id TEXT NOT NULL,
                resolution TEXT NOT NULL,
                timestamp REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_conflict_device
                ON agent_conflicts(device_id);
            CREATE INDEX IF NOT EXISTS idx_conflict_winner
                ON agent_conflicts(winner_id);

            -- TABLE 7: Agent decision outcomes for feedback loop (NEW POC7)
            CREATE TABLE IF NOT EXISTS agent_decision_outcomes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id TEXT NOT NULL,
                action TEXT NOT NULL,
                target_device TEXT NOT NULL,
                confidence REAL NOT NULL,
                accepted INTEGER NOT NULL DEFAULT 0,
                conflict INTEGER NOT NULL DEFAULT 0,
                conflict_winner TEXT DEFAULT '',
                reasoning_summary TEXT DEFAULT '',
                timestamp REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_outcome_agent
                ON agent_decision_outcomes(agent_id, timestamp);

            -- TABLE 8: MCP health check snapshots (NEW POC7)
            CREATE TABLE IF NOT EXISTS mcp_health_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                healthy INTEGER NOT NULL DEFAULT 1,
                latency_ms REAL NOT NULL DEFAULT 0,
                consecutive_errors INTEGER NOT NULL DEFAULT 0,
                fallback_active INTEGER NOT NULL DEFAULT 0,
                timestamp REAL NOT NULL
            );

            -- TABLE 9: NLU conversation log (NEW POC8)
            CREATE TABLE IF NOT EXISTS conversation_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_text TEXT NOT NULL,
                intent_type TEXT NOT NULL DEFAULT 'unknown',
                actions_json TEXT NOT NULL DEFAULT '[]',
                response TEXT NOT NULL DEFAULT '',
                confidence REAL NOT NULL DEFAULT 0.0,
                devices_mentioned TEXT NOT NULL DEFAULT '',
                timestamp REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_conv_intent
                ON conversation_log(intent_type);

            -- TABLE 10: Anomaly detection log (NEW POC8)
            CREATE TABLE IF NOT EXISTS anomaly_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL,
                device_type TEXT NOT NULL DEFAULT '',
                anomaly_score REAL NOT NULL DEFAULT 0.0,
                is_anomaly INTEGER NOT NULL DEFAULT 0,
                detectors_triggered TEXT NOT NULL DEFAULT '',
                explanation TEXT NOT NULL DEFAULT '',
                readings_json TEXT NOT NULL DEFAULT '{}',
                timestamp REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_anomaly_device
                ON anomaly_log(device_id, timestamp);

            -- TABLE 11: Arbitration decision log (NEW POC8)
            CREATE TABLE IF NOT EXISTS arbitration_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conflict_device TEXT NOT NULL,
                winner_agent TEXT NOT NULL,
                loser_agents TEXT NOT NULL DEFAULT '',
                method TEXT NOT NULL DEFAULT 'priority_fallback',
                reasoning TEXT NOT NULL DEFAULT '',
                scores_json TEXT NOT NULL DEFAULT '{}',
                confidence REAL NOT NULL DEFAULT 0.0,
                timestamp REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_arb_method
                ON arbitration_log(method);

            -- TABLE 12: Governance change log (NEW POC10)
            CREATE TABLE IF NOT EXISTS governance_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                change_type TEXT NOT NULL,
                key_or_agent TEXT NOT NULL,
                old_value TEXT NOT NULL DEFAULT '',
                new_value TEXT NOT NULL DEFAULT '',
                tier INTEGER NOT NULL DEFAULT 0,
                details_json TEXT NOT NULL DEFAULT '{}',
                timestamp REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_gov_type
                ON governance_log(change_type);

            -- TABLE 13: Model usage tracking log (NEW POC10)
            CREATE TABLE IF NOT EXISTS model_usage_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id TEXT NOT NULL,
                model_name TEXT NOT NULL,
                provider TEXT NOT NULL DEFAULT '',
                tokens_approx INTEGER NOT NULL DEFAULT 0,
                cost_approx REAL NOT NULL DEFAULT 0.0,
                latency_ms REAL NOT NULL DEFAULT 0.0,
                success INTEGER NOT NULL DEFAULT 1,
                timestamp REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_model_agent
                ON model_usage_log(agent_id, timestamp);
        """)
        self.conn.commit()

    def _count_unanchored(self) -> int:
        total = 0
        for table in ("telemetry_continuous", "telemetry_events",
                       "telemetry_alerts"):
            row = self.conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE batch_id IS NULL"
            ).fetchone()
            total += row[0]
        return total

    def _get_next_batch_id(self) -> int:
        row = self.conn.execute(
            "SELECT MAX(batch_id) FROM telemetry_anchors"
        ).fetchone()
        return (row[0] or 0) + 1

    # --- Store telemetry (auto-classified) ---

    def store_telemetry_batch(self, telemetry_list):
        cont_rows = []
        event_rows = []
        alert_rows = []

        for t in telemetry_list:
            readings_json = json.dumps(t.readings, sort_keys=True)
            category = classify_device(t.device_type, t.readings)

            if category == "continuous":
                dh = hash_row("continuous", t.device_id, readings_json,
                              t.timestamp)
                cont_rows.append((t.device_id, t.device_type, readings_json,
                                  t.timestamp, dh))

            elif category == "alert":
                dh = hash_row("alert", t.device_id, readings_json,
                              t.timestamp)
                smoke = t.readings.get("smoke_level", 0)
                alarm = t.readings.get("alarm_active", False)
                gas_ppm = t.readings.get(
                    "gas_level_ppm", t.readings.get("co_level", 0))
                gas_type = t.readings.get("gas_type", "CO")
                leak = t.readings.get("leak_detected", False)
                status = t.readings.get("status", "")

                if isinstance(smoke, (int, float)) and smoke >= 0.3:
                    alert_rows.append((
                        t.device_id, t.device_type, "smoke_detected",
                        "high" if smoke >= 0.5 else "medium",
                        readings_json, t.timestamp, dh))
                elif isinstance(gas_ppm, (int, float)) and \
                        gas_ppm >= GAS_THRESHOLDS.get(gas_type, 100):
                    alert_rows.append((
                        t.device_id, t.device_type, "gas_detected",
                        "critical" if gas_ppm >= 200 else "high",
                        readings_json, t.timestamp, dh))
                elif leak:
                    alert_rows.append((
                        t.device_id, t.device_type,
                        "leak_detected", "high",
                        readings_json, t.timestamp, dh))
                elif status == "critical":
                    alert_rows.append((
                        t.device_id, t.device_type,
                        "device_critical", "high",
                        readings_json, t.timestamp, dh))
                elif status == "warning":
                    alert_rows.append((
                        t.device_id, t.device_type,
                        "device_warning", "medium",
                        readings_json, t.timestamp, dh))
                elif alarm:
                    alert_rows.append((
                        t.device_id, t.device_type,
                        "alarm_active", "high",
                        readings_json, t.timestamp, dh))
                else:
                    event_rows.append((
                        t.device_id, t.device_type, "sensor_reading",
                        readings_json, t.timestamp, dh))

            else:  # event
                dh = hash_row("event", t.device_id, readings_json,
                              t.timestamp)
                event_type = self._infer_event_type(t.device_type, t.readings)
                event_rows.append((t.device_id, t.device_type, event_type,
                                   readings_json, t.timestamp, dh))

        if cont_rows:
            self.conn.executemany(
                "INSERT INTO telemetry_continuous "
                "(device_id, device_type, readings_json, timestamp, data_hash) "
                "VALUES (?, ?, ?, ?, ?)", cont_rows)
        if event_rows:
            self.conn.executemany(
                "INSERT INTO telemetry_events "
                "(device_id, device_type, event_type, readings_json, "
                "timestamp, data_hash) VALUES (?, ?, ?, ?, ?, ?)", event_rows)
        if alert_rows:
            self.conn.executemany(
                "INSERT INTO telemetry_alerts "
                "(device_id, device_type, alert_type, severity, readings_json, "
                "timestamp, data_hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
                alert_rows)

        self.conn.commit()
        self._unanchored_count += (len(cont_rows) + len(event_rows)
                                   + len(alert_rows))
        return {
            "continuous": len(cont_rows),
            "events": len(event_rows),
            "alerts": len(alert_rows),
        }

    def _infer_event_type(self, device_type: str, readings: dict) -> str:
        """Infer event type from readings, not hardcoded device types."""
        if "is_locked" in readings:
            return "door_locked" if readings["is_locked"] else "door_unlocked"
        if "is_on" in readings and "brightness" in readings:
            return "light_on" if readings["is_on"] else "light_off"
        if "motion_detected" in readings:
            return ("motion_detected" if readings["motion_detected"]
                    else "no_motion")
        if "person_detected" in readings:
            return ("person_detected" if readings["person_detected"]
                    else "camera_idle")
        if "is_recording" in readings:
            return ("recording_started" if readings["is_recording"]
                    else "recording_stopped")
        if "is_on" in readings:
            return "device_on" if readings["is_on"] else "device_off"
        if "is_open" in readings:
            return "opened" if readings["is_open"] else "closed"
        if "mode" in readings:
            return f"mode_{readings['mode']}"
        if "status" in readings:
            return f"status_{readings['status']}"
        return "state_update"

    # --- Store emergency as alert ---

    def store_emergency(self, emergency: dict):
        readings_json = json.dumps(emergency, sort_keys=True, default=str)
        dh = hash_row("alert", emergency.get("source_device", "unknown"),
                       readings_json,
                       emergency.get("timestamp", time.time()))
        self.conn.execute(
            "INSERT INTO telemetry_alerts "
            "(device_id, device_type, alert_type, severity, readings_json, "
            "timestamp, data_hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (emergency.get("source_device", "unknown"),
             "safety_device",
             emergency.get("type", "EMERGENCY"), "critical",
             readings_json, emergency.get("timestamp", time.time()), dh)
        )
        self.conn.commit()
        self._unanchored_count += 1

    # --- Reasoning log ---

    def store_reasoning(self, reasoning_hash: str, full_text: str,
                        agent_id: str, action: str, target_device: str,
                        confidence: float):
        try:
            self.conn.execute(
                "INSERT INTO reasoning_log "
                "(reasoning_hash, full_text, agent_id, action, target_device, "
                "confidence, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (reasoning_hash, full_text, agent_id, action, target_device,
                 confidence, time.time())
            )
            self.conn.commit()
        except sqlite3.IntegrityError:
            pass

    def get_reasoning(self, reasoning_hash: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM reasoning_log WHERE reasoning_hash = ?",
            (reasoning_hash,)
        ).fetchone()
        return dict(row) if row else None

    # --- Agent conflict log (NEW in POC4) ---

    def store_conflict(self, conflict: dict):
        try:
            self.conn.execute(
                "INSERT INTO agent_conflicts "
                "(conflict_id, device_id, agent_a_id, agent_a_action, "
                "agent_a_priority, agent_b_id, agent_b_action, "
                "agent_b_priority, winner_id, resolution, timestamp) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (conflict["conflict_id"], conflict["device_id"],
                 conflict["agent_a_id"], conflict["agent_a_action"],
                 conflict["agent_a_priority"],
                 conflict["agent_b_id"], conflict["agent_b_action"],
                 conflict["agent_b_priority"],
                 conflict["winner_id"], conflict["resolution"],
                 conflict["timestamp"])
            )
            self.conn.commit()
        except sqlite3.IntegrityError:
            pass

    def query_conflicts(self, device_id: str = None,
                        limit: int = 100) -> list:
        if device_id:
            rows = self.conn.execute(
                "SELECT * FROM agent_conflicts WHERE device_id = ? "
                "ORDER BY timestamp DESC LIMIT ?", (device_id, limit)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM agent_conflicts "
                "ORDER BY timestamp DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def conflict_stats(self) -> dict:
        total = self.conn.execute(
            "SELECT COUNT(*) FROM agent_conflicts"
        ).fetchone()[0]
        by_winner = self.conn.execute(
            "SELECT winner_id, COUNT(*) FROM agent_conflicts "
            "GROUP BY winner_id"
        ).fetchall()
        return {
            "total_conflicts": total,
            "by_winner": {r[0]: r[1] for r in by_winner},
        }

    # --- Merkle anchoring ---

    def get_unanchored_count(self) -> int:
        return self._unanchored_count

    def create_anchor(self) -> Optional[dict]:
        all_hashes = []
        counts = {"continuous": 0, "events": 0, "alerts": 0}
        all_ids = {"telemetry_continuous": [], "telemetry_events": [],
                    "telemetry_alerts": []}
        timestamps = []

        for table, key in [("telemetry_continuous", "continuous"),
                            ("telemetry_events", "events"),
                            ("telemetry_alerts", "alerts")]:
            rows = self.conn.execute(
                f"SELECT id, data_hash, timestamp FROM {table} "
                f"WHERE batch_id IS NULL ORDER BY id"
            ).fetchall()
            for r in rows:
                all_hashes.append(r["data_hash"])
                all_ids[table].append(r["id"])
                timestamps.append(r["timestamp"])
            counts[key] = len(rows)

        if not all_hashes:
            return None

        root = merkle_root(all_hashes)
        batch_id = self._next_batch_id
        now = time.time()

        for table, ids in all_ids.items():
            if ids:
                self.conn.execute(
                    f"UPDATE {table} SET batch_id = ? "
                    f"WHERE id IN ({','.join('?' * len(ids))})",
                    [batch_id] + ids
                )

        self.conn.execute(
            "INSERT INTO telemetry_anchors "
            "(batch_id, merkle_root, record_count, continuous_count, "
            "event_count, alert_count, first_timestamp, last_timestamp, "
            "timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (batch_id, root, len(all_hashes),
             counts["continuous"], counts["events"], counts["alerts"],
             min(timestamps), max(timestamps), now)
        )
        self.conn.commit()

        self._next_batch_id += 1
        self._unanchored_count = 0

        return {
            "batch_id": batch_id,
            "merkle_root": root,
            "record_count": len(all_hashes),
            "continuous_count": counts["continuous"],
            "event_count": counts["events"],
            "alert_count": counts["alerts"],
        }

    def update_anchor_block(self, batch_id: int, block_index: int):
        self.conn.execute(
            "UPDATE telemetry_anchors SET block_index = ? WHERE batch_id = ?",
            (block_index, batch_id)
        )
        self.conn.commit()

    # --- Verification ---

    def verify_reasoning(self, reasoning_hash: str) -> dict:
        record = self.get_reasoning(reasoning_hash)
        if not record:
            return {"verified": False,
                    "reason": "Not found in off-chain store"}
        recomputed = hashlib.sha256(record["full_text"].encode()).hexdigest()
        match = recomputed == reasoning_hash
        return {
            "verified": match,
            "stored_hash": reasoning_hash,
            "recomputed_hash": recomputed,
            "full_text": record["full_text"],
            "agent_id": record["agent_id"],
            "reason": "Hash match" if match else "HASH MISMATCH",
        }

    def verify_anchor(self, batch_id: int) -> dict:
        anchor = self.conn.execute(
            "SELECT * FROM telemetry_anchors WHERE batch_id = ?",
            (batch_id,)
        ).fetchone()
        if not anchor:
            return {"verified": False,
                    "reason": f"Batch {batch_id} not found"}
        all_hashes = []
        for table in ("telemetry_continuous", "telemetry_events",
                       "telemetry_alerts"):
            rows = self.conn.execute(
                f"SELECT data_hash FROM {table} "
                f"WHERE batch_id = ? ORDER BY id",
                (batch_id,)
            ).fetchall()
            all_hashes.extend(r["data_hash"] for r in rows)
        recomputed = merkle_root(all_hashes)
        match = recomputed == anchor["merkle_root"]
        return {
            "verified": match,
            "batch_id": batch_id,
            "record_count": anchor["record_count"],
            "continuous_count": anchor["continuous_count"],
            "event_count": anchor["event_count"],
            "alert_count": anchor["alert_count"],
            "stored_root": anchor["merkle_root"],
            "recomputed_root": recomputed,
            "reason": "Merkle root match" if match else "MERKLE MISMATCH",
        }

    # --- ML/DL query helpers ---

    def query_continuous(self, device_id: str = None,
                         limit: int = 1000) -> list:
        if device_id:
            rows = self.conn.execute(
                "SELECT * FROM telemetry_continuous WHERE device_id = ? "
                "ORDER BY timestamp DESC LIMIT ?", (device_id, limit)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM telemetry_continuous "
                "ORDER BY timestamp DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def query_events(self, event_type: str = None,
                     limit: int = 1000) -> list:
        if event_type:
            rows = self.conn.execute(
                "SELECT * FROM telemetry_events WHERE event_type = ? "
                "ORDER BY timestamp DESC LIMIT ?", (event_type, limit)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM telemetry_events "
                "ORDER BY timestamp DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def query_alerts(self, severity: str = None,
                     limit: int = 1000) -> list:
        if severity:
            rows = self.conn.execute(
                "SELECT * FROM telemetry_alerts WHERE severity = ? "
                "ORDER BY timestamp DESC LIMIT ?", (severity, limit)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM telemetry_alerts "
                "ORDER BY timestamp DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    # --- Decision outcomes (feedback loop, NEW POC7) ---

    def store_decision_outcome(self, agent_id: str, action: str,
                               target_device: str, confidence: float,
                               accepted: bool, conflict: bool = False,
                               conflict_winner: str = "",
                               reasoning_summary: str = ""):
        self.conn.execute(
            "INSERT INTO agent_decision_outcomes "
            "(agent_id, action, target_device, confidence, accepted, "
            "conflict, conflict_winner, reasoning_summary, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (agent_id, action, target_device, confidence,
             1 if accepted else 0, 1 if conflict else 0,
             conflict_winner, reasoning_summary, time.time())
        )
        self.conn.commit()

    def get_recent_outcomes(self, agent_id: str, limit: int = 10) -> list:
        rows = self.conn.execute(
            "SELECT * FROM agent_decision_outcomes "
            "WHERE agent_id = ? ORDER BY timestamp DESC LIMIT ?",
            (agent_id, limit)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_recent_outcomes(self, limit: int = 200) -> list:
        """Get recent decision outcomes across ALL agents, newest first."""
        rows = self.conn.execute(
            "SELECT * FROM agent_decision_outcomes "
            "ORDER BY timestamp DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_outcome_stats(self, agent_id: str) -> dict:
        total = self.conn.execute(
            "SELECT COUNT(*) FROM agent_decision_outcomes WHERE agent_id = ?",
            (agent_id,)
        ).fetchone()[0]
        accepted = self.conn.execute(
            "SELECT COUNT(*) FROM agent_decision_outcomes "
            "WHERE agent_id = ? AND accepted = 1", (agent_id,)
        ).fetchone()[0]
        conflicts = self.conn.execute(
            "SELECT COUNT(*) FROM agent_decision_outcomes "
            "WHERE agent_id = ? AND conflict = 1", (agent_id,)
        ).fetchone()[0]
        return {
            "total": total,
            "accepted": accepted,
            "conflicts": conflicts,
            "acceptance_rate": accepted / total if total > 0 else 0.0,
            "conflict_rate": conflicts / total if total > 0 else 0.0,
        }

    # --- MCP health log (NEW POC7) ---

    def store_health_snapshot(self, snapshot: dict):
        self.conn.execute(
            "INSERT INTO mcp_health_log "
            "(healthy, latency_ms, consecutive_errors, fallback_active, "
            "timestamp) VALUES (?, ?, ?, ?, ?)",
            (1 if snapshot.get("healthy") else 0,
             snapshot.get("latency_ms", 0),
             snapshot.get("consecutive_errors", 0),
             1 if snapshot.get("fallback_active") else 0,
             time.time())
        )
        self.conn.commit()

    def get_health_summary(self) -> dict:
        total = self.conn.execute(
            "SELECT COUNT(*) FROM mcp_health_log"
        ).fetchone()[0]
        healthy = self.conn.execute(
            "SELECT COUNT(*) FROM mcp_health_log WHERE healthy = 1"
        ).fetchone()[0]
        fallbacks = self.conn.execute(
            "SELECT COUNT(*) FROM mcp_health_log WHERE fallback_active = 1"
        ).fetchone()[0]
        avg_lat = self.conn.execute(
            "SELECT AVG(latency_ms) FROM mcp_health_log WHERE latency_ms > 0"
        ).fetchone()[0] or 0
        max_lat = self.conn.execute(
            "SELECT MAX(latency_ms) FROM mcp_health_log WHERE latency_ms > 0"
        ).fetchone()[0] or 0
        return {
            "total_checks": total,
            "healthy_checks": healthy,
            "uptime_pct": healthy / total * 100 if total > 0 else 100.0,
            "fallback_activations": fallbacks,
            "avg_latency_ms": avg_lat,
            "max_latency_ms": max_lat,
        }

    # --- Conversation log (NLU, NEW POC8) ---

    def store_conversation_turn(self, user_text: str, intent_type: str,
                                actions_json: str, response: str,
                                confidence: float,
                                devices_mentioned: str = ""):
        self.conn.execute(
            "INSERT INTO conversation_log "
            "(user_text, intent_type, actions_json, response, confidence, "
            "devices_mentioned, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_text, intent_type, actions_json, response, confidence,
             devices_mentioned, time.time())
        )
        self.conn.commit()

    def get_conversation_history(self, limit: int = 50) -> list[dict]:
        """Retrieve recent conversation turns, newest first."""
        rows = self.conn.execute(
            "SELECT user_text, intent_type, actions_json, response, "
            "confidence, devices_mentioned, timestamp "
            "FROM conversation_log ORDER BY timestamp DESC LIMIT ?",
            (limit,)
        ).fetchall()
        result = []
        for r in rows:
            result.append({
                "user_text": r[0],
                "intent_type": r[1],
                "actions_json": r[2],
                "response": r[3],
                "confidence": r[4],
                "devices_mentioned": r[5],
                "timestamp": r[6],
            })
        result.reverse()  # oldest first for display
        return result

    def get_conversation_stats(self) -> dict:
        total = self.conn.execute(
            "SELECT COUNT(*) FROM conversation_log"
        ).fetchone()[0]
        by_intent = self.conn.execute(
            "SELECT intent_type, COUNT(*) FROM conversation_log "
            "GROUP BY intent_type"
        ).fetchall()
        avg_conf = self.conn.execute(
            "SELECT AVG(confidence) FROM conversation_log"
        ).fetchone()[0] or 0
        return {
            "total_interactions": total,
            "by_intent": {r[0]: r[1] for r in by_intent},
            "avg_confidence": avg_conf,
        }

    # --- Anomaly log (NEW POC8) ---

    def store_anomaly(self, device_id: str, device_type: str,
                      anomaly_score: float, is_anomaly: bool,
                      detectors_triggered: str, explanation: str,
                      readings_json: str = "{}"):
        self.conn.execute(
            "INSERT INTO anomaly_log "
            "(device_id, device_type, anomaly_score, is_anomaly, "
            "detectors_triggered, explanation, readings_json, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (device_id, device_type, anomaly_score,
             1 if is_anomaly else 0, detectors_triggered, explanation,
             readings_json, time.time())
        )
        self.conn.commit()

    def get_anomaly_stats(self) -> dict:
        total = self.conn.execute(
            "SELECT COUNT(*) FROM anomaly_log"
        ).fetchone()[0]
        anomalies = self.conn.execute(
            "SELECT COUNT(*) FROM anomaly_log WHERE is_anomaly = 1"
        ).fetchone()[0]
        by_device = self.conn.execute(
            "SELECT device_id, COUNT(*) FROM anomaly_log "
            "WHERE is_anomaly = 1 GROUP BY device_id"
        ).fetchall()
        return {
            "total_scans": total,
            "anomalies_detected": anomalies,
            "anomaly_rate": anomalies / total if total > 0 else 0.0,
            "normal_rate": (total - anomalies) / total if total > 0 else 0.0,
            "by_device": {r[0]: r[1] for r in by_device},
        }

    # --- Arbitration log (NEW POC8) ---

    def store_arbitration(self, conflict_device: str, winner_agent: str,
                          loser_agents: str, method: str,
                          reasoning: str, scores_json: str = "{}",
                          confidence: float = 0.0):
        self.conn.execute(
            "INSERT INTO arbitration_log "
            "(conflict_device, winner_agent, loser_agents, method, "
            "reasoning, scores_json, confidence, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (conflict_device, winner_agent, loser_agents, method,
             reasoning, scores_json, confidence, time.time())
        )
        self.conn.commit()

    def get_arbitration_stats(self) -> dict:
        total = self.conn.execute(
            "SELECT COUNT(*) FROM arbitration_log"
        ).fetchone()[0]
        by_method = self.conn.execute(
            "SELECT method, COUNT(*) FROM arbitration_log "
            "GROUP BY method"
        ).fetchall()
        safety_overrides = self.conn.execute(
            "SELECT COUNT(*) FROM arbitration_log "
            "WHERE method = 'safety_override'"
        ).fetchone()[0]
        return {
            "total_arbitrations": total,
            "by_method": {r[0]: r[1] for r in by_method},
            "safety_overrides": safety_overrides,
        }

    # --- Governance log (NEW POC10) ---

    def store_governance_change(self, change_type: str, key_or_agent: str,
                                old_value: str, new_value: str,
                                tier: int = 0, details_json: str = "{}"):
        self.conn.execute(
            "INSERT INTO governance_log "
            "(change_type, key_or_agent, old_value, new_value, tier, "
            "details_json, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (change_type, key_or_agent, str(old_value), str(new_value),
             tier, details_json, time.time())
        )
        self.conn.commit()

    def get_governance_stats(self) -> dict:
        total = self.conn.execute(
            "SELECT COUNT(*) FROM governance_log"
        ).fetchone()[0]
        by_type = self.conn.execute(
            "SELECT change_type, COUNT(*) FROM governance_log "
            "GROUP BY change_type"
        ).fetchall()
        by_tier = self.conn.execute(
            "SELECT tier, COUNT(*) FROM governance_log "
            "GROUP BY tier"
        ).fetchall()
        return {
            "total_changes": total,
            "by_type": {r[0]: r[1] for r in by_type},
            "by_tier": {r[0]: r[1] for r in by_tier},
        }

    # --- Model usage log (NEW POC10) ---

    def store_model_usage(self, agent_id: str, model_name: str,
                          provider: str = "", tokens_approx: int = 0,
                          cost_approx: float = 0.0, latency_ms: float = 0.0,
                          success: bool = True):
        self.conn.execute(
            "INSERT INTO model_usage_log "
            "(agent_id, model_name, provider, tokens_approx, cost_approx, "
            "latency_ms, success, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (agent_id, model_name, provider, tokens_approx, cost_approx,
             latency_ms, 1 if success else 0, time.time())
        )
        self.conn.commit()

    def get_model_usage_stats(self) -> dict:
        total = self.conn.execute(
            "SELECT COUNT(*) FROM model_usage_log"
        ).fetchone()[0]
        total_cost = self.conn.execute(
            "SELECT COALESCE(SUM(cost_approx), 0) FROM model_usage_log"
        ).fetchone()[0]
        by_agent = self.conn.execute(
            "SELECT agent_id, COUNT(*), SUM(cost_approx) "
            "FROM model_usage_log GROUP BY agent_id"
        ).fetchall()
        by_provider = self.conn.execute(
            "SELECT provider, COUNT(*), SUM(cost_approx) "
            "FROM model_usage_log GROUP BY provider"
        ).fetchall()
        return {
            "total_calls": total,
            "total_cost": round(total_cost, 6),
            "by_agent": {r[0]: {"calls": r[1], "cost": round(r[2], 6)}
                         for r in by_agent},
            "by_provider": {r[0]: {"calls": r[1], "cost": round(r[2], 6)}
                            for r in by_provider},
        }

    # --- Stats ---

    def stats(self) -> dict:
        s = {}
        for table in ("telemetry_continuous", "telemetry_events",
                       "telemetry_alerts", "reasoning_log",
                       "telemetry_anchors", "agent_conflicts",
                       "agent_decision_outcomes", "mcp_health_log",
                       "conversation_log", "anomaly_log",
                       "arbitration_log",
                       "governance_log", "model_usage_log"):
            s[f"{table}_count"] = self.conn.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]

        for table in ("telemetry_continuous", "telemetry_events",
                       "telemetry_alerts"):
            s[f"{table}_unanchored"] = self.conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE batch_id IS NULL"
            ).fetchone()[0]

        s["distinct_event_types"] = self.conn.execute(
            "SELECT COUNT(DISTINCT event_type) FROM telemetry_events"
        ).fetchone()[0]
        s["distinct_alert_types"] = self.conn.execute(
            "SELECT COUNT(DISTINCT alert_type) FROM telemetry_alerts"
        ).fetchone()[0]

        s["db_size_kb"] = round(os.path.getsize(self.db_path) / 1024, 1) \
            if os.path.exists(self.db_path) else 0
        return s

    def close(self):
        self.conn.close()
