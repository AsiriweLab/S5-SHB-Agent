"""
Layer 2: Lightweight Blockchain -- Trust Anchor & Audit Trail
POC4: Added AgentPriority, ConflictDetector, conflict-aware validation.
POC8+: Added AdaptiveDifficulty -- auto-adjust PoW based on tx volume.
"""

import hashlib
import json
import time
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding, PublicFormat,
)
from cryptography.exceptions import InvalidSignature


# ---------------------------------------------------------------------------
# Crypto helpers
# ---------------------------------------------------------------------------

def generate_keypair():
    private = Ed25519PrivateKey.generate()
    pub_bytes = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return private, pub_bytes


def sign_data(private_key: Ed25519PrivateKey, data: bytes) -> bytes:
    return private_key.sign(data)


def verify_signature(pub_bytes: bytes, signature: bytes, data: bytes) -> bool:
    try:
        public_key = Ed25519PublicKey.from_public_bytes(pub_bytes)
        public_key.verify(signature, data)
        return True
    except (InvalidSignature, Exception):
        return False


# ---------------------------------------------------------------------------
# Transaction
# ---------------------------------------------------------------------------

class Transaction:
    def __init__(self, agent_id: str, action: str, target_device: str,
                 params: Dict[str, Any], confidence: float,
                 reasoning_hash: str, signature: bytes = b"",
                 tx_type: str = "agent_action"):
        self.agent_id = agent_id
        self.action = action
        self.target_device = target_device
        self.params = params
        self.confidence = confidence
        self.reasoning_hash = reasoning_hash
        self.signature = signature
        self.tx_type = tx_type
        self.timestamp = time.time()

    def payload_bytes(self) -> bytes:
        payload = {
            "agent_id": self.agent_id,
            "action": self.action,
            "target_device": self.target_device,
            "params": self.params,
            "confidence": self.confidence,
            "reasoning_hash": self.reasoning_hash,
            "tx_type": self.tx_type,
            "timestamp": self.timestamp,
        }
        return json.dumps(payload, sort_keys=True).encode()

    def tx_hash(self) -> str:
        return hashlib.sha256(self.payload_bytes()).hexdigest()

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "action": self.action,
            "target_device": self.target_device,
            "params": self.params,
            "confidence": self.confidence,
            "reasoning_hash": self.reasoning_hash,
            "signature": self.signature.hex() if self.signature else "",
            "tx_type": self.tx_type,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Transaction":
        tx = cls(
            agent_id=d["agent_id"], action=d["action"],
            target_device=d["target_device"], params=d["params"],
            confidence=d["confidence"], reasoning_hash=d["reasoning_hash"],
            signature=bytes.fromhex(d["signature"]) if d.get("signature") else b"",
            tx_type=d.get("tx_type", "agent_action"),
        )
        tx.timestamp = d["timestamp"]
        return tx


# ---------------------------------------------------------------------------
# Block
# ---------------------------------------------------------------------------

class Block:
    def __init__(self, index: int, transactions: List[Transaction],
                 previous_hash: str, nonce: int = 0):
        self.index = index
        self.transactions = transactions
        self.previous_hash = previous_hash
        self.nonce = nonce
        self.timestamp = time.time()
        self.hash = self.compute_hash()

    def compute_hash(self) -> str:
        block_data = {
            "index": self.index,
            "tx_count": len(self.transactions),
            "tx_hashes": [hashlib.sha256(tx.payload_bytes()).hexdigest()
                          for tx in self.transactions],
            "previous_hash": self.previous_hash,
            "nonce": self.nonce,
            "timestamp": self.timestamp,
        }
        return hashlib.sha256(
            json.dumps(block_data, sort_keys=True).encode()
        ).hexdigest()

    def mine(self, difficulty: int):
        target = "0" * difficulty
        while not self.hash.startswith(target):
            self.nonce += 1
            self.hash = self.compute_hash()

    def to_dict(self) -> dict:
        result = {
            "index": self.index, "hash": self.hash,
            "previous_hash": self.previous_hash,
            "nonce": self.nonce, "timestamp": self.timestamp,
            "transactions": [tx.to_dict() for tx in self.transactions],
        }
        if hasattr(self, "difficulty_used"):
            result["difficulty_used"] = self.difficulty_used
        return result

    @classmethod
    def from_dict(cls, d: dict) -> "Block":
        txs = [Transaction.from_dict(td) for td in d["transactions"]]
        block = cls(d["index"], txs, d["previous_hash"], d["nonce"])
        block.timestamp = d["timestamp"]
        block.hash = d["hash"]
        if "difficulty_used" in d:
            block.difficulty_used = d["difficulty_used"]
        return block


# ---------------------------------------------------------------------------
# Agent Registry & Permission Matrix
# ---------------------------------------------------------------------------

class AgentRegistry:
    def __init__(self):
        self._agents: Dict[str, bytes] = {}

    def register(self, agent_id: str, pub_bytes: bytes):
        self._agents[agent_id] = pub_bytes

    def is_registered(self, agent_id: str) -> bool:
        return agent_id in self._agents

    def get_pubkey(self, agent_id: str) -> Optional[bytes]:
        return self._agents.get(agent_id)

    def verify_agent_signature(self, agent_id: str, signature: bytes,
                                data: bytes) -> bool:
        pub = self.get_pubkey(agent_id)
        if not pub:
            return False
        return verify_signature(pub, signature, data)

    def to_dict(self) -> dict:
        return {k: v.hex() for k, v in self._agents.items()}

    def load_dict(self, d: dict):
        for k, v in d.items():
            self._agents[k] = bytes.fromhex(v)


class PermissionMatrix:
    def __init__(self):
        self._perms: Dict[str, set] = {}

    def grant(self, agent_id: str, device_id: str, command: str):
        self._perms.setdefault(agent_id, set()).add((device_id, command))

    def grant_all(self, agent_id: str):
        self._perms[agent_id] = {("*", "*")}

    def is_allowed(self, agent_id: str, device_id: str, command: str) -> bool:
        perms = self._perms.get(agent_id, set())
        if ("*", "*") in perms:
            return True
        return ((device_id, command) in perms or
                (device_id, "*") in perms or
                ("*", command) in perms)

    def to_dict(self) -> dict:
        return {k: list(v) for k, v in self._perms.items()}

    def load_dict(self, d: dict):
        for k, v in d.items():
            self._perms[k] = {tuple(p) for p in v}


# ---------------------------------------------------------------------------
# Agent Priority (NEW in POC4)
# ---------------------------------------------------------------------------

class AgentPriority:
    """Manages agent priority rankings for conflict resolution."""

    def __init__(self):
        self._priorities: Dict[str, float] = {}

    def set_priority(self, agent_id: str, priority: float):
        self._priorities[agent_id] = priority

    def get_priority(self, agent_id: str) -> float:
        return self._priorities.get(agent_id, 0.0)

    def compare(self, agent_a: str, agent_b: str) -> str:
        """Returns the agent_id with higher priority."""
        pa = self._priorities.get(agent_a, 0.0)
        pb = self._priorities.get(agent_b, 0.0)
        return agent_a if pa >= pb else agent_b

    def to_dict(self) -> dict:
        return dict(self._priorities)

    def load_dict(self, d: dict):
        self._priorities = {k: float(v) for k, v in d.items()}


# ---------------------------------------------------------------------------
# Conflict Detection & Resolution (NEW in POC4)
# ---------------------------------------------------------------------------

@dataclass
class ConflictRecord:
    """Record of a detected conflict between two agents."""
    conflict_id: str
    timestamp: float
    device_id: str
    agent_a_id: str
    agent_a_action: str
    agent_a_priority: float
    agent_b_id: str
    agent_b_action: str
    agent_b_priority: float
    winner_id: str
    resolution: str  # "priority" or "equal_priority_first_wins"

    def to_dict(self) -> dict:
        return {
            "conflict_id": self.conflict_id,
            "timestamp": self.timestamp,
            "device_id": self.device_id,
            "agent_a_id": self.agent_a_id,
            "agent_a_action": self.agent_a_action,
            "agent_a_priority": self.agent_a_priority,
            "agent_b_id": self.agent_b_id,
            "agent_b_action": self.agent_b_action,
            "agent_b_priority": self.agent_b_priority,
            "winner_id": self.winner_id,
            "resolution": self.resolution,
        }


class ConflictDetector:
    """Detects and resolves conflicts between agent transactions."""

    def __init__(self, priorities: AgentPriority):
        self.priorities = priorities
        self.conflict_log: List[ConflictRecord] = []

    def check_conflicts(self, new_tx: Transaction,
                        pending: List[Transaction]) -> Optional[ConflictRecord]:
        """Check if new_tx conflicts with any pending transaction."""
        for existing_tx in pending:
            if existing_tx.agent_id == new_tx.agent_id:
                continue
            if existing_tx.target_device != new_tx.target_device:
                continue
            if existing_tx.tx_type != "agent_action":
                continue
            if new_tx.tx_type != "agent_action":
                continue
            if self._are_conflicting(existing_tx, new_tx):
                return self._resolve(existing_tx, new_tx)
        return None

    def _are_conflicting(self, tx_a: Transaction, tx_b: Transaction) -> bool:
        """Two agent_action txs on the same device with different commands
        are considered conflicting."""
        if tx_a.action != tx_b.action:
            return True
        return False

    def _resolve(self, tx_existing: Transaction,
                 tx_new: Transaction) -> ConflictRecord:
        """Resolve conflict by priority. Higher priority wins."""
        p_existing = self.priorities.get_priority(tx_existing.agent_id)
        p_new = self.priorities.get_priority(tx_new.agent_id)

        if p_new > p_existing:
            winner = tx_new.agent_id
            resolution = "priority"
        elif p_existing > p_new:
            winner = tx_existing.agent_id
            resolution = "priority"
        else:
            winner = tx_existing.agent_id
            resolution = "equal_priority_first_wins"

        conflict_data = (f"{tx_existing.agent_id}|{tx_new.agent_id}|"
                         f"{tx_existing.target_device}|{time.time()}")
        conflict_id = hashlib.sha256(conflict_data.encode()).hexdigest()[:16]

        record = ConflictRecord(
            conflict_id=conflict_id,
            timestamp=time.time(),
            device_id=tx_existing.target_device,
            agent_a_id=tx_existing.agent_id,
            agent_a_action=tx_existing.action,
            agent_a_priority=p_existing,
            agent_b_id=tx_new.agent_id,
            agent_b_action=tx_new.action,
            agent_b_priority=p_new,
            winner_id=winner,
            resolution=resolution,
        )
        self.conflict_log.append(record)
        return record


# ---------------------------------------------------------------------------
# Adaptive PoW Difficulty (NEW in POC8+)
# ---------------------------------------------------------------------------

@dataclass
class DifficultyAdjustment:
    """Record of a difficulty adjustment event."""
    block_index: int
    timestamp: float
    old_difficulty: int
    new_difficulty: int
    reason: str          # "high_volume", "low_volume", "normal", "initial"
    tx_count: int
    mining_time_ms: float

    def to_dict(self) -> dict:
        return {
            "block_index": self.block_index,
            "timestamp": self.timestamp,
            "old_difficulty": self.old_difficulty,
            "new_difficulty": self.new_difficulty,
            "reason": self.reason,
            "tx_count": self.tx_count,
            "mining_time_ms": self.mining_time_ms,
        }


class AdaptiveDifficulty:
    """Auto-adjusts mining difficulty based on transaction volume.

    - High tx volume (emergencies) -> lower difficulty -> faster mining
    - Low tx volume (idle periods) -> higher difficulty -> save compute
    - Difficulty is clamped between min_difficulty and max_difficulty
    """

    def __init__(self, base_difficulty: int = 2,
                 min_difficulty: int = 1, max_difficulty: int = 4,
                 tx_volume_low: int = 3, tx_volume_high: int = 10,
                 adjustment_window: int = 3, enabled: bool = True):
        self.base_difficulty = base_difficulty
        self.current_difficulty = base_difficulty
        self.min_difficulty = min_difficulty
        self.max_difficulty = max_difficulty
        self.tx_volume_low = tx_volume_low
        self.tx_volume_high = tx_volume_high
        self.adjustment_window = adjustment_window  # blocks to average over
        self.enabled = enabled

        # History tracking
        self._block_history: List[dict] = []  # {tx_count, mining_time_ms}
        self.adjustment_log: List[DifficultyAdjustment] = []

    def get_difficulty(self) -> int:
        """Return current difficulty (adaptive or base)."""
        if not self.enabled:
            return self.base_difficulty
        return self.current_difficulty

    def record_block(self, block_index: int, tx_count: int,
                     mining_time_ms: float):
        """Record a mined block and adjust difficulty if needed."""
        self._block_history.append({
            "block_index": block_index,
            "tx_count": tx_count,
            "mining_time_ms": mining_time_ms,
        })

        if not self.enabled:
            return

        # Use recent window to compute average tx volume
        window = self._block_history[-self.adjustment_window:]
        avg_tx = sum(b["tx_count"] for b in window) / len(window)

        old_diff = self.current_difficulty
        reason = "normal"

        if avg_tx >= self.tx_volume_high:
            # High volume (emergency) -> decrease difficulty for faster mining
            new_diff = max(self.min_difficulty, old_diff - 1)
            reason = "high_volume"
        elif avg_tx <= self.tx_volume_low:
            # Low volume (idle) -> increase difficulty to save compute
            new_diff = min(self.max_difficulty, old_diff + 1)
            reason = "low_volume"
        else:
            # Normal volume -> tend toward base difficulty
            if old_diff > self.base_difficulty:
                new_diff = old_diff - 1
                reason = "normal_decrease"
            elif old_diff < self.base_difficulty:
                new_diff = old_diff + 1
                reason = "normal_increase"
            else:
                new_diff = old_diff
                reason = "normal"

        self.current_difficulty = new_diff

        adj = DifficultyAdjustment(
            block_index=block_index,
            timestamp=time.time(),
            old_difficulty=old_diff,
            new_difficulty=new_diff,
            reason=reason,
            tx_count=tx_count,
            mining_time_ms=mining_time_ms,
        )
        self.adjustment_log.append(adj)

    def get_stats(self) -> dict:
        """Return adaptive difficulty statistics."""
        if not self._block_history:
            return {
                "enabled": self.enabled,
                "current_difficulty": self.current_difficulty,
                "base_difficulty": self.base_difficulty,
                "blocks_tracked": 0,
                "adjustments": 0,
                "difficulty_changes": 0,
                "avg_mining_time_ms": 0.0,
                "min_difficulty_reached": self.base_difficulty,
                "max_difficulty_reached": self.base_difficulty,
            }

        changes = sum(1 for a in self.adjustment_log
                      if a.old_difficulty != a.new_difficulty)
        difficulties = [a.new_difficulty for a in self.adjustment_log]
        avg_time = (sum(b["mining_time_ms"] for b in self._block_history)
                    / len(self._block_history))

        return {
            "enabled": self.enabled,
            "current_difficulty": self.current_difficulty,
            "base_difficulty": self.base_difficulty,
            "blocks_tracked": len(self._block_history),
            "adjustments": len(self.adjustment_log),
            "difficulty_changes": changes,
            "avg_mining_time_ms": round(avg_time, 2),
            "min_difficulty_reached": min(difficulties) if difficulties else self.base_difficulty,
            "max_difficulty_reached": max(difficulties) if difficulties else self.base_difficulty,
        }

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "base_difficulty": self.base_difficulty,
            "current_difficulty": self.current_difficulty,
            "min_difficulty": self.min_difficulty,
            "max_difficulty": self.max_difficulty,
            "tx_volume_low": self.tx_volume_low,
            "tx_volume_high": self.tx_volume_high,
            "adjustment_window": self.adjustment_window,
            "block_history": self._block_history,
            "adjustment_log": [a.to_dict() for a in self.adjustment_log],
        }

    def load_dict(self, d: dict):
        self.enabled = d.get("enabled", True)
        self.base_difficulty = d.get("base_difficulty", 2)
        self.current_difficulty = d.get("current_difficulty", 2)
        self.min_difficulty = d.get("min_difficulty", 1)
        self.max_difficulty = d.get("max_difficulty", 4)
        self.tx_volume_low = d.get("tx_volume_low", 3)
        self.tx_volume_high = d.get("tx_volume_high", 10)
        self.adjustment_window = d.get("adjustment_window", 3)
        self._block_history = d.get("block_history", [])
        self.adjustment_log = [
            DifficultyAdjustment(**a) for a in d.get("adjustment_log", [])
        ]


# ---------------------------------------------------------------------------
# Blockchain (with persistence + conflict awareness)
# ---------------------------------------------------------------------------

class Blockchain:
    def __init__(self, difficulty: int = 2,
                 adaptive_enabled: bool = False,
                 adaptive_min: int = 1, adaptive_max: int = 4,
                 adaptive_tx_low: int = 3, adaptive_tx_high: int = 10,
                 adaptive_window: int = 3):
        self.difficulty = difficulty
        self.adaptive = AdaptiveDifficulty(
            base_difficulty=difficulty,
            min_difficulty=adaptive_min,
            max_difficulty=adaptive_max,
            tx_volume_low=adaptive_tx_low,
            tx_volume_high=adaptive_tx_high,
            adjustment_window=adaptive_window,
            enabled=adaptive_enabled,
        )
        self.registry = AgentRegistry()
        self.permissions = PermissionMatrix()
        self.priorities = AgentPriority()
        self.conflict_detector = ConflictDetector(self.priorities)
        self.chain: List[Block] = []
        self.pending_tx: List[Transaction] = []
        self.rejected_tx: List[dict] = []
        self._create_genesis()

    def _create_genesis(self):
        genesis = Block(0, [], "0" * 64)
        t0 = time.time()
        genesis.mine(self.difficulty)
        mining_ms = (time.time() - t0) * 1000
        genesis.difficulty_used = self.difficulty
        self.chain.append(genesis)
        self.adaptive.record_block(0, 0, mining_ms)

    @property
    def last_block(self) -> Block:
        return self.chain[-1]

    # --- Validation (with conflict detection) ---

    def validate_and_add(self, tx: Transaction) -> dict:
        """Validate transaction: registration, signature, permissions, conflicts."""
        if not self.registry.is_registered(tx.agent_id):
            reason = f"Agent '{tx.agent_id}' is NOT registered"
            self.rejected_tx.append({"tx": tx.to_dict(), "reason": reason})
            return {"accepted": False, "reason": reason}

        if not self.registry.verify_agent_signature(
                tx.agent_id, tx.signature, tx.payload_bytes()):
            reason = "Invalid signature"
            self.rejected_tx.append({"tx": tx.to_dict(), "reason": reason})
            return {"accepted": False, "reason": reason}

        if not self.permissions.is_allowed(
                tx.agent_id, tx.target_device, tx.action):
            reason = (f"Agent '{tx.agent_id}' lacks permission for "
                      f"'{tx.action}' on '{tx.target_device}'")
            self.rejected_tx.append({"tx": tx.to_dict(), "reason": reason})
            return {"accepted": False, "reason": reason}

        # Conflict detection (NEW in POC4)
        conflict = self.conflict_detector.check_conflicts(tx, self.pending_tx)
        if conflict:
            if conflict.winner_id == tx.agent_id:
                # New tx wins -- remove the conflicting pending tx
                loser_id = conflict.agent_a_id
                self.pending_tx = [
                    ptx for ptx in self.pending_tx
                    if not (ptx.target_device == tx.target_device
                            and ptx.agent_id == loser_id
                            and ptx.tx_type == "agent_action")
                ]
                self.rejected_tx.append({
                    "tx": None,  # original loser tx already removed
                    "reason": (f"Overridden by {tx.agent_id} "
                               f"(priority {conflict.agent_b_priority} > "
                               f"{conflict.agent_a_priority})"),
                    "conflict": conflict.to_dict(),
                })
                self.pending_tx.append(tx)
                return {
                    "accepted": True,
                    "reason": "Accepted (won conflict)",
                    "conflict": conflict.to_dict(),
                }
            else:
                # Existing tx wins -- reject the new one
                reason = (f"Conflict: {conflict.agent_a_id} "
                          f"(priority {conflict.agent_a_priority}) > "
                          f"{tx.agent_id} "
                          f"(priority {conflict.agent_b_priority})")
                self.rejected_tx.append({
                    "tx": tx.to_dict(),
                    "reason": reason,
                    "conflict": conflict.to_dict(),
                })
                return {"accepted": False, "reason": reason,
                        "conflict": conflict.to_dict()}

        self.pending_tx.append(tx)
        return {"accepted": True, "reason": "Validated"}

    def record_emergency(self, emergency: dict):
        tx = Transaction(
            agent_id="DEVICE_FIRMWARE", action="EMERGENCY",
            target_device=emergency.get("source_device", "unknown"),
            params=emergency, confidence=1.0,
            reasoning_hash="N/A", tx_type="emergency",
        )
        self.pending_tx.append(tx)

    def record_fallback(self, fallback_actions: List[dict]):
        for fa in fallback_actions:
            tx = Transaction(
                agent_id="FALLBACK_SYSTEM",
                action=fa.get("action", "fallback"),
                target_device=fa.get("device", "unknown"), params=fa,
                confidence=1.0, reasoning_hash="N/A", tx_type="fallback",
            )
            self.pending_tx.append(tx)

    def record_telemetry_anchor(self, merkle_root: str, batch_id: int,
                                 record_count: int):
        tx = Transaction(
            agent_id="TELEMETRY_ANCHOR", action="anchor_telemetry",
            target_device="all",
            params={"merkle_root": merkle_root, "batch_id": batch_id,
                    "record_count": record_count},
            confidence=1.0, reasoning_hash=merkle_root,
            tx_type="telemetry_anchor",
        )
        self.pending_tx.append(tx)

    def mine_pending(self) -> Optional[Block]:
        if not self.pending_tx:
            return None
        # Use adaptive difficulty if enabled, otherwise static
        effective_difficulty = self.adaptive.get_difficulty()
        tx_count = len(self.pending_tx)
        block = Block(len(self.chain), list(self.pending_tx),
                      self.last_block.hash)
        t0 = time.time()
        block.mine(effective_difficulty)
        mining_time_ms = (time.time() - t0) * 1000
        block.difficulty_used = effective_difficulty  # store for audit
        self.chain.append(block)
        self.pending_tx.clear()
        # Record for adaptive adjustment
        self.adaptive.record_block(block.index, tx_count, mining_time_ms)
        return block

    def validate_chain(self) -> bool:
        for i in range(1, len(self.chain)):
            curr = self.chain[i]
            prev = self.chain[i - 1]
            if curr.previous_hash != prev.hash:
                return False
            if curr.hash != curr.compute_hash():
                return False
        return True

    # --- Persistence ---

    def save(self, filepath: str):
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        data = {
            "difficulty": self.difficulty,
            "adaptive_difficulty": self.adaptive.to_dict(),
            "chain": [b.to_dict() for b in self.chain],
            "rejected": self.rejected_tx,
            "registry": self.registry.to_dict(),
            "permissions": self.permissions.to_dict(),
            "priorities": self.priorities.to_dict(),
            "conflicts": [c.to_dict()
                          for c in self.conflict_detector.conflict_log],
        }
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2, default=str)

    @classmethod
    def load(cls, filepath: str) -> "Blockchain":
        with open(filepath, "r") as f:
            data = json.load(f)
        chain = cls.__new__(cls)
        chain.difficulty = data["difficulty"]
        chain.adaptive = AdaptiveDifficulty(base_difficulty=chain.difficulty)
        if "adaptive_difficulty" in data:
            chain.adaptive.load_dict(data["adaptive_difficulty"])
        chain.chain = [Block.from_dict(bd) for bd in data["chain"]]
        chain.pending_tx = []
        chain.rejected_tx = data.get("rejected", [])
        chain.registry = AgentRegistry()
        chain.registry.load_dict(data.get("registry", {}))
        chain.permissions = PermissionMatrix()
        chain.permissions.load_dict(data.get("permissions", {}))
        chain.priorities = AgentPriority()
        chain.priorities.load_dict(data.get("priorities", {}))
        chain.conflict_detector = ConflictDetector(chain.priorities)
        return chain

    # --- Reporting ---

    def summary(self) -> str:
        lines = [f"Blockchain: {len(self.chain)} blocks, "
                 f"{sum(len(b.transactions) for b in self.chain)} total tx, "
                 f"{len(self.rejected_tx)} rejected"]
        if self.adaptive.enabled:
            stats = self.adaptive.get_stats()
            lines.append(f"  Adaptive PoW: difficulty={stats['current_difficulty']} "
                         f"(base={stats['base_difficulty']}, "
                         f"range=[{self.adaptive.min_difficulty}-"
                         f"{self.adaptive.max_difficulty}], "
                         f"changes={stats['difficulty_changes']})")
        for b in self.chain:
            diff_str = ""
            if hasattr(b, "difficulty_used"):
                diff_str = f" d={b.difficulty_used}"
            lines.append(f"  Block #{b.index} [{b.hash[:12]}...] "
                         f"{len(b.transactions)} tx{diff_str}")
            for tx in b.transactions:
                lines.append(f"    - [{tx.tx_type}] {tx.agent_id}: "
                             f"{tx.action} -> {tx.target_device} "
                             f"(conf={tx.confidence})")
        if self.rejected_tx:
            lines.append(f"  Rejected: {len(self.rejected_tx)} transaction(s)")
        return "\n".join(lines)
