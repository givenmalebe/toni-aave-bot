"""ETH pre-compute cache — builds calldata for hot positions on every block."""

import asyncio
import json
import logging
import threading
import time
from typing import Any, Callable, Optional

log = logging.getLogger("precompute_eth")

_lock = threading.Lock()
_shutdown = False
CACHE_TTL = 120  # seconds

# Cache: { user_address: { ts, data: { calldata, selector, ... } } }
_cache: dict[str, dict] = {}
_last_block: int = 0
_cache_hits: int = 0
_cache_misses: int = 0


def stop():
    global _shutdown
    _shutdown = True


def get(user: str) -> Optional[dict]:
    """Get pre-computed calldata for a user. Returns None on cache miss."""
    global _cache_hits, _cache_misses
    with _lock:
        entry = _cache.get(user.lower())
    if not entry:
        with _lock:
            _cache_misses += 1
        return None
    if time.time() - entry["ts"] > CACHE_TTL:
        with _lock:
            _cache.pop(user.lower(), None)
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
    with _lock:
        if not _cache or _last_block == 0:
            return 0
        evicted = 0
        stale = [k for k, v in _cache.items() if _last_block - v.get("data", {}).get("updated_block", 0) > max_blocks_old]
        for k in stale:
            del _cache[k]
            evicted += 1
        return evicted


async def refresh(hot_positions: list[dict], rpc_url: str) -> None:
    """Refresh cache for all hot positions. Called on each new block."""
    global _last_block

    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            payload = {"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []}
            async with session.post(rpc_url, json=payload, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                data = await resp.json()
                with _lock:
                    _last_block = int(data["result"], 16)
    except Exception as e:
        log.warning("Failed to get block number: %s", e)
        return

    new_cache: dict[str, dict] = {}
    for pos in hot_positions:
        user = pos.get("user", "").lower()
        if not user:
            continue
        try:
            entry = build_entry(
                protocol=pos.get("protocol", "unknown"),
                user=pos.get("user", ""),
                collateral=pos.get("collateral", ""),
                debt=pos.get("debt", ""),
                debt_amount_wei=int(pos.get("debtToCover", "0")),
                hf=pos.get("hf", 0),
                contract_addr=pos.get("contract", ""),
                liq_sig=pos.get("liq_sig", "0x"),
                liq_args=pos.get("liq_args", []),
                swap_path=pos.get("swap_path", b""),
                gas_limit=pos.get("gas_limit", 1500000),
                estimated_profit_usd=pos.get("net_usd", 0),
                flash_amount_wei=int(pos.get("flash_amount", "0")),
                debt_token=pos.get("debt_token", ""),
                coll_token=pos.get("coll_token", ""),
            )
            new_cache[user] = {"ts": time.time(), "data": entry}
        except Exception as e:
            log.warning("Failed to pre-compute for %s: %s", user, e)

    with _lock:
        _cache.update(new_cache)

    evict_stale()
    with _lock:
        log.info("ETH pre-compute refresh: %d positions, block %d", len(_cache), _last_block)


_listener_task: Optional[asyncio.Task] = None


async def _block_loop(rpc_urls: list[str], get_hot_positions: Callable) -> None:
    """Background loop that listens for new blocks and refreshes cache."""
    import websockets

    rpc_url = rpc_urls[0] if rpc_urls else "https://ethereum-rpc.publicnode.com"
    ws_url = rpc_url.replace("https://", "wss://").replace("http://", "ws://")

    while not _shutdown:
        try:
            async with websockets.connect(ws_url, ping_interval=20) as ws:
                sub = {"jsonrpc": "2.0", "id": 1, "method": "eth_subscribe", "params": ["newHeads"]}
                await ws.send(json.dumps(sub))
                await ws.recv()

                async for msg in ws:
                    try:
                        data = json.loads(msg)
                        if "params" in data:
                            hot = get_hot_positions()
                            await refresh(hot, rpc_url)
                    except Exception as e:
                        log.warning("Block listener error: %s", e)
        except Exception as e:
            log.warning("WebSocket disconnected, reconnecting in 5s: %s", e)
            await asyncio.sleep(5)


def start_block_listener(rpc_urls: list[str], get_hot_positions: Callable) -> asyncio.Task:
    """Start the background block listener. Returns the asyncio Task."""
    global _listener_task
    if _listener_task and not _listener_task.done():
        return _listener_task
    _listener_task = asyncio.ensure_future(_block_loop(rpc_urls, get_hot_positions))
    return _listener_task
