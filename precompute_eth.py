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
