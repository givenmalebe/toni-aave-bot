"""SOL pre-compute cache — builds instruction sequences for hot obligations on every slot."""

import asyncio
import logging
import time
from typing import Optional

log = logging.getLogger("precompute_sol")

_cache: dict[str, dict] = {}
_last_slot: int = 0
_cache_hits: int = 0
_cache_misses: int = 0


def get(obligation: str) -> Optional[dict]:
    """Get pre-computed instructions for an obligation. Returns None on cache miss."""
    global _cache_hits, _cache_misses
    entry = _cache.get(obligation)
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
        "last_slot": _last_slot,
    }


def evict_stale(max_slots_old: int = 30) -> int:
    """Remove entries not refreshed in max_slots_old slots. Returns count evicted."""
    global _cache
    if not _cache or _last_slot == 0:
        return 0
    evicted = 0
    stale = [k for k, v in _cache.items() if _last_slot - v.get("updated_slot", 0) > max_slots_old]
    for k in stale:
        del _cache[k]
        evicted += 1
    return evicted
