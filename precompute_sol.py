"""SOL pre-compute cache — builds instruction sequences for hot obligations on every slot."""

import asyncio
import json
import logging
import threading
import time
from typing import Optional

log = logging.getLogger("precompute_sol")


def _num(value, default=0):
    if isinstance(value, bool) or value is None:
        return default
    if isinstance(value, (int, float)):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

_lock = threading.Lock()
_shutdown = False
CACHE_TTL = 120  # seconds

_cache: dict[str, dict] = {}
_last_slot: int = 0
_cache_hits: int = 0
_cache_misses: int = 0


def stop():
    global _shutdown
    _shutdown = True


def get(obligation: str) -> Optional[dict]:
    """Get pre-computed instructions for an obligation. Returns None on cache miss."""
    global _cache_hits, _cache_misses
    with _lock:
        entry = _cache.get(obligation)
    if not entry:
        with _lock:
            _cache_misses += 1
        return None
    if time.time() - entry["ts"] > CACHE_TTL:
        with _lock:
            _cache.pop(obligation, None)
            _cache_misses += 1
        return None
    with _lock:
        _cache_hits += 1
    return entry.get("data")


def cache_stats() -> dict:
    """Return cache statistics."""
    with _lock:
        total = _cache_hits + _cache_misses
        return {
            "positions": len(_cache),
            "hits": _cache_hits,
            "misses": _cache_misses,
            "hit_rate": _cache_hits / total if total else 0,
            "last_slot": _last_slot,
        }


def build_entry(
    obligation: str,
    kind: str,
    repay_reserve: str,
    withdraw_reserve: str,
    repay_mint: str,
    withdraw_mint: str,
    debt_amount: int,
    hf: float,
    compute_units: int,
    priority_fee_ul: int,
    jito_tip_lamports: int,
    instruction_sequence: list,
    account_metas: list,
    jupiter_route: dict | None,
    estimated_profit_usd: float,
) -> dict:
    """Build a cache entry for a single SOL obligation."""
    return {
        "obligation": obligation,
        "kind": kind,
        "repay_reserve": repay_reserve,
        "withdraw_reserve": withdraw_reserve,
        "repay_mint": repay_mint,
        "withdraw_mint": withdraw_mint,
        "debt_amount": debt_amount,
        "hf": hf,
        "compute_units": compute_units,
        "priority_fee_ul": priority_fee_ul,
        "jito_tip_lamports": jito_tip_lamports,
        "instruction_sequence": instruction_sequence,
        "account_metas": account_metas,
        "jupiter_route": jupiter_route,
        "estimated_profit_usd": estimated_profit_usd,
        "updated_slot": _last_slot,
    }


def evict_stale(max_slots_old: int = 30) -> int:
    """Remove entries not refreshed in max_slots_old slots. Returns count evicted."""
    with _lock:
        if not _cache or _last_slot == 0:
            return 0
        evicted = 0
        stale = [k for k, v in _cache.items() if _last_slot - _num(v.get("data", {}).get("updated_slot", 0)) > max_slots_old]
        for k in stale:
            del _cache[k]
            evicted += 1
        return evicted


async def refresh(hot_obligations: list[dict], rpc_url: str) -> None:
    """Refresh cache for all hot obligations. Called on each new slot."""
    global _last_slot

    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            payload = {"jsonrpc": "2.0", "id": 1, "method": "getSlot", "params": []}
            async with session.post(rpc_url, json=payload, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                data = await resp.json()
                with _lock:
                    _last_slot = data["result"]
    except Exception as e:
        log.warning("Failed to get slot: %s", e)
        return

    new_cache: dict[str, dict] = {}
    for obl in hot_obligations:
        addr = obl.get("obligation", "")
        if not addr:
            continue
        try:
            entry = build_entry(
                obligation=addr,
                kind=obl.get("kind", "liq"),
                repay_reserve=obl.get("debt_reserve", ""),
                withdraw_reserve=obl.get("coll_reserve", ""),
                repay_mint=obl.get("repay_mint", ""),
                withdraw_mint=obl.get("withdraw_mint", ""),
                debt_amount=_num(obl.get("debt_amount")),
                hf=_num(obl.get("hf"), 0),
                compute_units=_num(obl.get("compute_units"), 400000),
                priority_fee_ul=_num(obl.get("priority_fee_ul"), 50000),
                jito_tip_lamports=_num(obl.get("jito_tip_lamports"), 50000),
                instruction_sequence=obl.get("instruction_sequence", []),
                account_metas=obl.get("account_metas", []),
                jupiter_route=obl.get("jupiter_route"),
                estimated_profit_usd=_num(obl.get("expected_profit_usd"), 0),
            )
            new_cache[addr] = {"ts": time.time(), "data": entry}
        except Exception as e:
            log.warning("Failed to pre-compute for %s: %s", addr, e)

    with _lock:
        _cache.update(new_cache)

    evict_stale()
    with _lock:
        log.info("SOL pre-compute refresh: %d positions, slot %d", len(_cache), _last_slot)


_listener_task: Optional[asyncio.Task] = None


async def _slot_loop(rpc_url: str, get_hot_obligations) -> None:
    """Background loop that listens for slot updates and refreshes cache."""
    global _listener_task
    import websockets

    ws_url = rpc_url.replace("https://", "wss://").replace("http://", "ws://")

    while not _shutdown:
        try:
            async with websockets.connect(ws_url, ping_interval=30) as ws:
                sub = {"jsonrpc": "2.0", "id": 1, "method": "slotSubscribe", "params": []}
                await ws.send(json.dumps(sub))
                await ws.recv()

                async for msg in ws:
                    try:
                        data = json.loads(msg)
                        if "params" in data:
                            hot = get_hot_obligations()
                            await refresh(hot, rpc_url)
                    except Exception as e:
                        log.warning("Slot listener error: %s", e)
        except Exception as e:
            log.warning("WebSocket disconnected, reconnecting in 5s: %s", e)
            await asyncio.sleep(5)


def start_slot_listener(rpc_url: str, get_hot_obligations) -> asyncio.Task:
    """Start the background slot listener."""
    global _listener_task
    if _listener_task and not _listener_task.done():
        return _listener_task
    _listener_task = asyncio.ensure_future(_slot_loop(rpc_url, get_hot_obligations))
    return _listener_task
