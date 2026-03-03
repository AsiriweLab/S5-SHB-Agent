"""
Blockchain explorer endpoints.

Provides read-only access to the blockchain state:
- Chain summary, blocks (paginated), transactions (paginated, filterable)
- Agent registry, permission matrix, priorities
- Conflict history
- Adaptive PoW statistics
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query

from web.core.state import get_app_state

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_active_session():
    """Raise 400 if no active session with blockchain."""
    state = get_app_state()
    if not state.is_active or state.chain is None:
        raise HTTPException(
            status_code=400,
            detail="No active session. Create or resume a session first.",
        )
    return state


# ---------------------------------------------------------------------------
# Chain Summary
# ---------------------------------------------------------------------------

@router.get("/summary")
async def blockchain_summary() -> dict[str, Any]:
    """Get blockchain summary: blocks, transactions, agents, difficulty."""
    state = _require_active_session()
    chain = state.chain

    total_tx = sum(len(b.transactions) for b in chain.chain)
    agent_count = len(chain.registry._agents)
    conflict_count = len(chain.conflict_detector.conflict_log)

    return {
        "total_blocks": len(chain.chain),
        "total_transactions": total_tx,
        "pending_transactions": len(chain.pending_tx),
        "rejected_transactions": len(chain.rejected_tx),
        "registered_agents": agent_count,
        "conflicts_detected": conflict_count,
        "difficulty": chain.difficulty,
        "adaptive_pow": chain.adaptive.get_stats(),
        "chain_valid": chain.validate_chain(),
        "latest_block_hash": chain.last_block.hash if chain.chain else None,
        "latest_block_index": chain.last_block.index if chain.chain else None,
    }


# ---------------------------------------------------------------------------
# Blocks
# ---------------------------------------------------------------------------

@router.get("/blocks")
async def list_blocks(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(10, ge=1, le=100, description="Items per page"),
    order: str = Query("desc", pattern="^(asc|desc)$", description="Sort order"),
) -> dict[str, Any]:
    """List all blocks with pagination."""
    state = _require_active_session()
    chain = state.chain

    blocks = list(chain.chain)
    if order == "desc":
        blocks = list(reversed(blocks))

    total = len(blocks)
    start = (page - 1) * page_size
    end = start + page_size
    page_blocks = blocks[start:end]

    return {
        "total_blocks": total,
        "page": page,
        "page_size": page_size,
        "blocks": [
            {
                "index": b.index,
                "hash": b.hash,
                "previous_hash": b.previous_hash,
                "nonce": b.nonce,
                "timestamp": b.timestamp,
                "transaction_count": len(b.transactions),
                "difficulty_used": getattr(b, "difficulty_used", None),
            }
            for b in page_blocks
        ],
    }


@router.get("/blocks/latest")
async def get_latest_block() -> dict[str, Any]:
    """Get the most recent block with full transaction details."""
    state = _require_active_session()
    chain = state.chain

    if not chain.chain:
        raise HTTPException(status_code=404, detail="No blocks in chain")

    block = chain.last_block
    return _block_to_dict(block)


@router.get("/blocks/{index}")
async def get_block(index: int) -> dict[str, Any]:
    """Get a specific block by index with full transaction details."""
    state = _require_active_session()
    chain = state.chain

    if index < 0 or index >= len(chain.chain):
        raise HTTPException(
            status_code=404,
            detail=f"Block {index} not found (chain has {len(chain.chain)} blocks)",
        )

    return _block_to_dict(chain.chain[index])


def _block_to_dict(block) -> dict[str, Any]:
    """Convert a Block object to a detailed dict."""
    return {
        "index": block.index,
        "hash": block.hash,
        "previous_hash": block.previous_hash,
        "nonce": block.nonce,
        "timestamp": block.timestamp,
        "difficulty_used": getattr(block, "difficulty_used", None),
        "transaction_count": len(block.transactions),
        "transactions": [tx.to_dict() for tx in block.transactions],
    }


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------

@router.get("/transactions")
async def list_transactions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    agent_id: Optional[str] = Query(None, description="Filter by agent ID"),
    tx_type: Optional[str] = Query(None, description="Filter by tx type"),
    device_id: Optional[str] = Query(None, description="Filter by target device"),
) -> dict[str, Any]:
    """List all transactions across all blocks (paginated, filterable)."""
    state = _require_active_session()
    chain = state.chain

    # Collect all transactions with block context
    all_tx = []
    for block in chain.chain:
        for tx in block.transactions:
            all_tx.append({
                "block_index": block.index,
                "block_hash": block.hash,
                **tx.to_dict(),
                "tx_hash": tx.tx_hash(),
            })

    # Apply filters
    if agent_id:
        all_tx = [t for t in all_tx if t["agent_id"] == agent_id]
    if tx_type:
        all_tx = [t for t in all_tx if t["tx_type"] == tx_type]
    if device_id:
        all_tx = [t for t in all_tx if t["target_device"] == device_id]

    # Reverse chronological
    all_tx.reverse()

    total = len(all_tx)
    start = (page - 1) * page_size
    end = start + page_size

    return {
        "total_transactions": total,
        "page": page,
        "page_size": page_size,
        "transactions": all_tx[start:end],
    }


@router.get("/transactions/{tx_hash}")
async def get_transaction(tx_hash: str) -> dict[str, Any]:
    """Get a single transaction by its hash."""
    state = _require_active_session()
    chain = state.chain

    for block in chain.chain:
        for tx in block.transactions:
            if tx.tx_hash() == tx_hash:
                return {
                    "block_index": block.index,
                    "block_hash": block.hash,
                    **tx.to_dict(),
                    "tx_hash": tx.tx_hash(),
                }

    raise HTTPException(status_code=404, detail=f"Transaction '{tx_hash}' not found")


# ---------------------------------------------------------------------------
# Agent Registry & Permissions
# ---------------------------------------------------------------------------

@router.get("/agents")
async def list_registered_agents() -> dict[str, Any]:
    """List all registered agents with public keys."""
    state = _require_active_session()
    chain = state.chain

    agents = []
    for agent_id, pub_hex in chain.registry.to_dict().items():
        priority = chain.priorities.get_priority(agent_id)
        agents.append({
            "agent_id": agent_id,
            "public_key_hex": pub_hex,
            "priority": priority,
        })

    # Sort by priority descending
    agents.sort(key=lambda a: a["priority"], reverse=True)

    return {
        "total_agents": len(agents),
        "agents": agents,
    }


@router.get("/permissions")
async def get_permission_matrix() -> dict[str, Any]:
    """Get the full permission matrix (agent -> device/command pairs)."""
    state = _require_active_session()
    chain = state.chain

    raw_perms = chain.permissions.to_dict()
    # Convert sets of tuples to readable format
    formatted = {}
    for agent_id, perms in raw_perms.items():
        formatted[agent_id] = [
            {"device_id": p[0], "command": p[1]}
            for p in perms
        ]

    return {"permissions": formatted}


@router.get("/priorities")
async def get_agent_priorities() -> dict[str, Any]:
    """Get agent priority levels."""
    state = _require_active_session()
    chain = state.chain

    priorities = chain.priorities.to_dict()
    # Sort by priority descending
    sorted_priorities = sorted(priorities.items(), key=lambda x: x[1], reverse=True)

    return {
        "priorities": [
            {"agent_id": aid, "priority": p}
            for aid, p in sorted_priorities
        ],
    }


# ---------------------------------------------------------------------------
# Conflicts
# ---------------------------------------------------------------------------

@router.get("/conflicts")
async def list_conflicts() -> dict[str, Any]:
    """Get conflict history from the conflict detector."""
    state = _require_active_session()
    chain = state.chain

    conflicts = [c.to_dict() for c in chain.conflict_detector.conflict_log]

    return {
        "total_conflicts": len(conflicts),
        "conflicts": conflicts,
    }


# ---------------------------------------------------------------------------
# Adaptive PoW
# ---------------------------------------------------------------------------

@router.get("/adaptive-pow")
async def get_adaptive_pow() -> dict[str, Any]:
    """Get adaptive Proof-of-Work difficulty statistics."""
    state = _require_active_session()
    chain = state.chain

    stats = chain.adaptive.get_stats()
    adjustment_log = [a.to_dict() for a in chain.adaptive.adjustment_log]

    return {
        "stats": stats,
        "adjustment_log": adjustment_log,
    }
