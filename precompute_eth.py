"""ETH pre-compute cache — builds calldata for hot positions on every block."""

import asyncio
import logging
import time
from typing import Any, Optional

log = logging.getLogger("precompute_eth")

# Cache: { user_address: { calldata, selector, swap_path, gas_limit, ... } }
_cache: dict[str, dict] = {}
_last_block: int = 0
_cache_hits: int = 0
_cache_misses: int = 0


def get(user: str) -> Optional[dict]:
    """Get pre-computed calldata for a user. Returns None on cache miss."""
    global _cache_hits, _cache_misses
    entry = _cache.get(user.lower())
    if entry:
        _cache_hits += 1
        return entry
    _cache_misses += 1
    return None


def cache_stats() -> dict:
    """Return cache statistics."""
    total = _cache_hits + _cache_misses
    return {
        "positions": len(_cache),
        "hits": _cache_hits,
        "misses": _cache_misses,
        "hit_rate": _cache_hits / total if total else 0,
        "last_block": _last_block,
    }


def build_entry(
    protocol: str,
    user: str,
    collateral: str,
    debt: str,
    debt_amount_wei: int,
    hf: float,
    contract_addr: str,
    liq_sig: str,
    liq_args: list[str],
    swap_path: bytes,
    gas_limit: int,
    estimated_profit_usd: float,
    flash_amount_wei: int,
    debt_token: str,
    coll_token: str,
) -> dict:
    """Build a cache entry for a single position."""
    return {
        "protocol": protocol,
        "user": user,
        "collateral": collateral,
        "debt": debt,
        "calldata": liq_sig + "".join(a[2:] if a.startswith("0x") else a for a in liq_args),
        "selector": liq_sig[:10],
        "swap_path": swap_path.hex() if isinstance(swap_path, bytes) else swap_path,
        "gas_limit": gas_limit,
        "estimated_profit_usd": estimated_profit_usd,
        "flash_amount": str(flash_amount_wei),
        "debt_token": debt_token,
        "coll_token": coll_token,
        "hf": hf,
        "updated_block": _last_block,
        "live_ok": True,
    }


def evict_stale(max_blocks_old: int = 3) -> int:
    """Remove entries not refreshed in max_blocks_old blocks. Returns count evicted."""
    global _cache
    if not _cache or _last_block == 0:
        return 0
    evicted = 0
    stale = [k for k, v in _cache.items() if _last_block - v.get("updated_block", 0) > max_blocks_old]
    for k in stale:
        del _cache[k]
        evicted += 1
    return evicted
