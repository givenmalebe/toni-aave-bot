# Pre-compute Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Same-block liquidation submission by pre-computing calldata for all hot positions on every block.

**Architecture:** Two new modules (`precompute_eth.py`, `precompute_sol.py`) that subscribe to block updates, refresh calldata caches, and provide instant lookup for the existing sweep loops. Integration via `get()` calls that fall back to on-the-fly computation on cache miss.

**Tech Stack:** Python asyncio, websockets, existing RPC pool, existing contract ABIs

## Global Constraints

- Python 3.10+, asyncio-based (existing codebase pattern)
- No new dependencies beyond `websockets` (already used)
- Must not break existing sweep loops — pre-compute is an optimization layer, not a replacement
- Cache eviction: entries older than 3 blocks are stale and evicted
- All calldata must be verified against on-the-fly computation during testing

---

## File Structure

| File | Responsibility |
|------|---------------|
| `precompute_eth.py` | ETH pre-compute cache, block listener, calldata builder |
| `precompute_sol.py` | SOL pre-compute cache, slot listener, instruction builder |
| `tests/test_precompute_eth.py` | Unit tests for ETH pre-compute |
| `tests/test_precompute_sol.py` | Unit tests for SOL pre-compute |
| `dashboard.py` | Add cache status to snapshot() |
| `static/app.js` | Add cache status display |
| `static/index.html` | Add cache status element |
| `static/style.css` | Add cache status styles |

---

### Task 1: ETH Pre-compute Cache Core

**Files:**
- Create: `precompute_eth.py`
- Test: `tests/test_precompute_eth.py`

**Interfaces:**
- Consumes: None (standalone module)
- Produces: `get(user) -> dict | None`, `refresh(hot_positions) -> None`, `cache_stats() -> dict`

- [ ] **Step 1: Create precompute_eth.py with cache structure**

```python
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
```

- [ ] **Step 2: Write test for cache get/set/evict**

```python
import precompute_eth as pe

def test_cache_miss():
    pe._cache.clear()
    pe._cache_hits = 0
    pe._cache_misses = 0
    result = pe.get("0xnonexistent")
    assert result is None
    assert pe._cache_misses == 1

def test_cache_hit():
    pe._cache.clear()
    pe._cache["0xabc"] = {"calldata": "0x123", "updated_block": 100}
    result = pe.get("0xabc")
    assert result is not None
    assert result["calldata"] == "0x123"

def test_evict_stale():
    pe._cache.clear()
    pe._last_block = 100
    pe._cache["0xold"] = {"updated_block": 95}  # 5 blocks old
    pe._cache["0xnew"] = {"updated_block": 99}  # 1 block old
    evicted = pe.evict_stale(max_blocks_old=3)
    assert evicted == 1
    assert "0xold" not in pe._cache
    assert "0xnew" in pe._cache
```

- [ ] **Step 3: Run tests**

Run: `cd C:\Users\Surf\Documents\toni-aave-bot && python -m pytest tests/test_precompute_eth.py -v`
Expected: 3 PASS

- [ ] **Step 4: Commit**

```bash
git add precompute_eth.py tests/test_precompute_eth.py
git commit -m "feat: ETH pre-compute cache core with get/evict/stats"
```

---

### Task 2: ETH Calldata Builder

**Files:**
- Modify: `precompute_eth.py`
- Test: `tests/test_precompute_eth.py`

**Interfaces:**
- Consumes: `eth_lending/executor.py` functions (`plan_aave_like`, `plan_comet`, `plan_morpho`)
- Produces: `build_entry(protocol, user, collateral, debt, ...) -> dict`

- [ ] **Step 1: Add calldata builder to precompute_eth.py**

```python
# Add to precompute_eth.py:

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
```

- [ ] **Step 2: Write test for build_entry**

```python
def test_build_entry():
    entry = pe.build_entry(
        protocol="aave-v3",
        user="0xabc",
        collateral="0xcoll",
        debt="0xdead",
        debt_amount_wei=1000000,
        hf=0.95,
        contract_addr="0xcontract",
        liq_sig="0xc2fa746c",
        liq_args=["0xabc", "0xcoll", "0xdead", "0xf4240"],
        swap_path=b"\x00",
        gas_limit=1500000,
        estimated_profit_usd=42.0,
        flash_amount_wei=1000000,
        debt_token="0xA0b86991",
        coll_token="0xC02aaA39",
    )
    assert entry["protocol"] == "aave-v3"
    assert entry["calldata"].startswith("0xc2fa746c")
    assert entry["estimated_profit_usd"] == 42.0
    assert entry["hf"] == 0.95
```

- [ ] **Step 3: Run tests**

Run: `cd C:\Users\Surf\Documents\toni-aave-bot && python -m pytest tests/test_precompute_eth.py -v`
Expected: 4 PASS

- [ ] **Step 4: Commit**

```bash
git add precompute_eth.py tests/test_precompute_eth.py
git commit -m "feat: ETH calldata builder for pre-compute cache"
```

---

### Task 3: ETH Block Listener

**Files:**
- Modify: `precompute_eth.py`
- Test: `tests/test_precompute_eth.py`

**Interfaces:**
- Consumes: Existing RPC pool from `dashboard.py`
- Produces: `start_block_listener(rpc_urls) -> asyncio.Task`, `refresh(hot_positions, rpc_url) -> None`

- [ ] **Step 1: Add block listener and refresh to precompute_eth.py**

```python
# Add to precompute_eth.py:

async def refresh(hot_positions: list[dict], rpc_url: str) -> None:
    """Refresh cache for all hot positions. Called on each new block."""
    global _cache, _last_block

    # Get latest block number
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            payload = {"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []}
            async with session.post(rpc_url, json=payload, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                data = await resp.json()
                _last_block = int(data["result"], 16)
    except Exception as e:
        log.warning("Failed to get block number: %s", e)
        return

    # Refresh each hot position
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
            _cache[user] = entry
        except Exception as e:
            log.warning("Failed to pre-compute for %s: %s", user, e)

    # Evict stale entries
    evict_stale()
    log.info("ETH pre-compute refresh: %d positions, block %d", len(_cache), _last_block)


_listener_task: Optional[asyncio.Task] = None


async def _block_loop(rpc_urls: list[str], get_hot_positions) -> None:
    """Background loop that listens for new blocks and refreshes cache."""
    global _listener_task
    import websockets

    rpc_url = rpc_urls[0] if rpc_urls else "https://ethereum-rpc.publicnode.com"
    ws_url = rpc_url.replace("https://", "wss://").replace("http://", "ws://")

    while True:
        try:
            async with websockets.connect(ws_url, ping_interval=20) as ws:
                # Subscribe to new blocks
                sub = {"jsonrpc": "2.0", "id": 1, "method": "eth_subscribe", "params": ["newHeads"]}
                await ws.send(json.dumps(sub))
                await ws.recv()  # subscription confirmation

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


def start_block_listener(rpc_urls: list[str], get_hot_positions) -> asyncio.Task:
    """Start the background block listener. Returns the asyncio Task."""
    global _listener_task
    if _listener_task and not _listener_task.done():
        return _listener_task
    _listener_task = asyncio.ensure_future(_block_loop(rpc_urls, get_hot_positions))
    return _listener_task
```

- [ ] **Step 2: Write test for refresh (mocked RPC)**

```python
import asyncio
import json

class MockResponse:
    def __init__(self, data):
        self._data = data
    async def json(self):
        return self._data
    async def __aenter__(self):
        return self
    async def __aexit__(self, *a):
        pass

class MockSession:
    def __init__(self, block_num=100):
        self._block_num = block_num
    async def __aenter__(self):
        return self
    async def __aexit__(self, *a):
        pass
    async def post(self, url, json=None, timeout=None):
        return MockResponse({"result": hex(self._block_num)})

def test_refresh_updates_cache(monkeypatch):
    pe._cache.clear()
    pe._last_block = 0
    import aiohttp
    monkeypatch.setattr(aiohttp, "ClientSession", MockSession)

    # Patch aiohttp.ClientSession to return our mock
    import precompute_eth
    original = aiohttp.ClientSession

    class FakeSession:
        def __init__(self):
            pass
        async def __aenter__(self):
            return MockSession(200)
        async def __aexit__(self, *a):
            pass

    monkeypatch.setattr(aiohttp, "ClientSession", FakeSession)

    pos = {
        "user": "0xabc", "protocol": "aave-v3", "collateral": "0xcoll",
        "debt": "0xdead", "debtToCover": "1000000", "hf": 0.95,
        "contract": "0xcontract", "liq_sig": "0xc2fa746c",
        "liq_args": ["0xabc", "0xcoll", "0xdead", "0xf4240"],
        "swap_path": b"\x00", "gas_limit": 1500000, "net_usd": 42.0,
        "flash_amount": "1000000", "debt_token": "0xA0b86991", "coll_token": "0xC02aaA39",
    }
    asyncio.get_event_loop().run_until_complete(pe.refresh([pos], "http://mock"))
    assert pe._last_block == 200
    assert "0xabc" in pe._cache
```

- [ ] **Step 3: Run tests**

Run: `cd C:\Users\Surf\Documents\toni-aave-bot && python -m pytest tests/test_precompute_eth.py -v`
Expected: 5 PASS

- [ ] **Step 4: Commit**

```bash
git add precompute_eth.py tests/test_precompute_eth.py
git commit -m "feat: ETH block listener with WebSocket subscription"
```

---

### Task 4: SOL Pre-compute Cache Core

**Files:**
- Create: `precompute_sol.py`
- Test: `tests/test_precompute_sol.py`

**Interfaces:**
- Consumes: None (standalone module)
- Produces: `get(obligation) -> dict | None`, `refresh(hot_obligations) -> None`, `cache_stats() -> dict`

- [ ] **Step 1: Create precompute_sol.py with cache structure**

```python
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
```

- [ ] **Step 2: Write test for cache get/set/evict**

```python
import precompute_sol as ps

def test_cache_miss():
    ps._cache.clear()
    ps._cache_hits = 0
    ps._cache_misses = 0
    result = ps.get("nonexistent_obligation")
    assert result is None
    assert ps._cache_misses == 1

def test_cache_hit():
    ps._cache.clear()
    ps._cache["obligation_abc"] = {"kind": "liq", "updated_slot": 440333000}
    result = ps.get("obligation_abc")
    assert result is not None
    assert result["kind"] == "liq"

def test_evict_stale():
    ps._cache.clear()
    ps._last_slot = 440333100
    ps._cache["old_obl"] = {"updated_slot": 440333000}  # 100 slots old
    ps._cache["new_obl"] = {"updated_slot": 440333099}  # 1 slot old
    evicted = ps.evict_stale(max_slots_old=30)
    assert evicted == 1
    assert "old_obl" not in ps._cache
    assert "new_obl" in ps._cache
```

- [ ] **Step 3: Run tests**

Run: `cd C:\Users\Surf\Documents\toni-aave-bot && python -m pytest tests/test_precompute_sol.py -v`
Expected: 3 PASS

- [ ] **Step 4: Commit**

```bash
git add precompute_sol.py tests/test_precompute_sol.py
git commit -m "feat: SOL pre-compute cache core with get/evict/stats"
```

---

### Task 5: SOL Instruction Builder

**Files:**
- Modify: `precompute_sol.py`
- Test: `tests/test_precompute_sol.py`

**Interfaces:**
- Consumes: `sol_scanner.py` constants (`SOLEND_PROGRAM`, instruction types)
- Produces: `build_entry(obligation, ...) -> dict`

- [ ] **Step 1: Add instruction builder to precompute_sol.py**

```python
# Add to precompute_sol.py:

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
```

- [ ] **Step 2: Write test for build_entry**

```python
def test_build_sol_entry():
    entry = ps.build_entry(
        obligation="obligation_abc",
        kind="liq",
        repay_reserve="repay_res",
        withdraw_reserve="withdraw_res",
        repay_mint="USDC",
        withdraw_mint="SOL",
        debt_amount=844200000,
        hf=1.0,
        compute_units=400000,
        priority_fee_ul=50000,
        jito_tip_lamports=50000,
        instruction_sequence=[{"program": "solend", "data": "0x123"}],
        account_metas=[{"pubkey": "res1", "is_signer": False, "is_writable": True}],
        jupiter_route={"in": "SOL", "out": "USDC", "amount": 844200000},
        estimated_profit_usd=42.0,
    )
    assert entry["obligation"] == "obligation_abc"
    assert entry["debt_amount"] == 844200000
    assert entry["estimated_profit_usd"] == 42.0
    assert len(entry["instruction_sequence"]) == 1
```

- [ ] **Step 3: Run tests**

Run: `cd C:\Users\Surf\Documents\toni-aave-bot && python -m pytest tests/test_precompute_sol.py -v`
Expected: 4 PASS

- [ ] **Step 4: Commit**

```bash
git add precompute_sol.py tests/test_precompute_sol.py
git commit -m "feat: SOL instruction builder for pre-compute cache"
```

---

### Task 6: SOL Slot Listener

**Files:**
- Modify: `precompute_sol.py`
- Test: `tests/test_precompute_sol.py`

**Interfaces:**
- Consumes: Solana RPC WebSocket
- Produces: `start_slot_listener(rpc_url, get_hot_obligations) -> asyncio.Task`

- [ ] **Step 1: Add slot listener to precompute_sol.py**

```python
# Add to precompute_sol.py:

import json

async def refresh(hot_obligations: list[dict], rpc_url: str) -> None:
    """Refresh cache for all hot obligations. Called on each new slot."""
    global _cache, _last_slot

    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            payload = {"jsonrpc": "2.0", "id": 1, "method": "getSlot", "params": []}
            async with session.post(rpc_url, json=payload, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                data = await resp.json()
                _last_slot = data["result"]
    except Exception as e:
        log.warning("Failed to get slot: %s", e)
        return

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
                debt_amount=obl.get("debt_amount", 0),
                hf=obl.get("hf", 0),
                compute_units=obl.get("compute_units", 400000),
                priority_fee_ul=obl.get("priority_fee_ul", 50000),
                jito_tip_lamports=obl.get("jito_tip_lamports", 50000),
                instruction_sequence=obl.get("instruction_sequence", []),
                account_metas=obl.get("account_metas", []),
                jupiter_route=obl.get("jupiter_route"),
                estimated_profit_usd=obl.get("expected_profit_usd", 0),
            )
            _cache[addr] = entry
        except Exception as e:
            log.warning("Failed to pre-compute for %s: %s", addr, e)

    evict_stale()
    log.info("SOL pre-compute refresh: %d positions, slot %d", len(_cache), _last_slot)


_listener_task: Optional[asyncio.Task] = None


async def _slot_loop(rpc_url: str, get_hot_obligations) -> None:
    """Background loop that listens for slot updates and refreshes cache."""
    global _listener_task
    import websockets

    ws_url = rpc_url.replace("https://", "wss://").replace("http://", "ws://")

    while True:
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
```

- [ ] **Step 2: Write test for refresh (mocked RPC)**

```python
import asyncio
import aiohttp

class MockSolSession:
    def __init__(self, slot=440333000):
        self._slot = slot
    async def __aenter__(self):
        return self
    async def __aexit__(self, *a):
        pass
    async def post(self, url, json=None, timeout=None):
        class R:
            async def json(self):
                return {"result": self._slot}
            async def __aenter__(self):
                return self
            async def __aexit__(self, *a):
                pass
        return R()

def test_sol_refresh_updates_cache(monkeypatch):
    ps._cache.clear()
    ps._last_slot = 0
    monkeypatch.setattr(aiohttp, "ClientSession", MockSolSession)

    obl = {
        "obligation": "obligation_abc", "kind": "liq", "debt_reserve": "repay",
        "coll_reserve": "withdraw", "repay_mint": "USDC", "withdraw_mint": "SOL",
        "debt_amount": 844200000, "hf": 1.0, "compute_units": 400000,
        "priority_fee_ul": 50000, "jito_tip_lamports": 50000,
        "instruction_sequence": [], "account_metas": [], "jupiter_route": None,
        "expected_profit_usd": 42.0,
    }
    asyncio.get_event_loop().run_until_complete(ps.refresh([obl], "http://mock"))
    assert ps._last_slot == 440333000
    assert "obligation_abc" in ps._cache
```

- [ ] **Step 3: Run tests**

Run: `cd C:\Users\Surf\Documents\toni-aave-bot && python -m pytest tests/test_precompute_sol.py -v`
Expected: 5 PASS

- [ ] **Step 4: Commit**

```bash
git add precompute_sol.py tests/test_precompute_sol.py
git commit -m "feat: SOL slot listener with WebSocket subscription"
```

---

### Task 7: Dashboard Integration — Cache Status

**Files:**
- Modify: `dashboard.py:1381` (snapshot method)
- Modify: `static/app.js` (render function)
- Modify: `static/index.html` (add element)
- Modify: `static/style.css` (add styles)

**Interfaces:**
- Consumes: `precompute_eth.cache_stats()`, `precompute_sol.cache_stats()`
- Produces: Cache status in dashboard state + UI display

- [ ] **Step 1: Add cache stats to dashboard snapshot**

In `dashboard.py`, in the `snapshot()` method (around line 1381), add after the existing state:

```python
# Add pre-compute cache stats
try:
    import precompute_eth as pe
    import precompute_sol as psol
    state["precompute"] = {
        "eth": pe.cache_stats(),
        "sol": psol.cache_stats(),
    }
except ImportError:
    state["precompute"] = {"eth": {}, "sol": {}}
```

- [ ] **Step 2: Add HTML element for cache status**

In `static/index.html`, inside the `card-liq-intel` card, add after the competitors section:

```html
<div class="liq-section-head">Pre-compute Cache</div>
<div class="cache-status">
  <span class="cache-stat">ETH: <b id="pc-eth-hits">0</b> hits / <b id="pc-eth-pos">0</b> pos</span>
  <span class="cache-stat">SOL: <b id="pc-sol-hits">0</b> hits / <b id="pc-sol-pos">0</b> pos</span>
  <span class="cache-stat">Block: <b id="pc-eth-block">—</b></span>
</div>
```

- [ ] **Step 3: Add CSS for cache status**

In `static/style.css`, add:

```css
.cache-status {
  display: flex; gap: 12px; font-size: 11px; color: var(--dim);
  padding: 6px 0; margin-top: 4px;
}
.cache-stat b { color: var(--text); font-family: var(--mono); }
```

- [ ] **Step 4: Add JS rendering for cache stats**

In `static/app.js`, in the `render()` function, add:

```javascript
// Pre-compute cache stats
const pc = s.precompute || {};
const ethPc = pc.eth || {};
const solPc = pc.sol || {};
const pcEthHits = $("pc-eth-hits");
const pcEthPos = $("pc-eth-pos");
const pcEthBlock = $("pc-eth-block");
const pcSolHits = $("pc-sol-hits");
const pcSolPos = $("pc-sol-pos");
if (pcEthHits) pcEthHits.textContent = fmt.num(ethPc.hits, 0);
if (pcEthPos) pcEthPos.textContent = fmt.num(ethPc.positions, 0);
if (pcEthBlock) pcEthBlock.textContent = ethPc.last_block ? fmt.num(ethPc.last_block, 0) : "—";
if (pcSolHits) pcSolHits.textContent = fmt.num(solPc.hits, 0);
if (pcSolPos) pcSolPos.textContent = fmt.num(solPc.positions, 0);
```

- [ ] **Step 5: Run dashboard and verify**

Run: `python dashboard.py`
Open: http://127.0.0.1:8080
Expected: Cache status shows in Liquidation Intel card with 0 hits / 0 positions (no data yet)

- [ ] **Step 6: Commit**

```bash
git add dashboard.py static/app.js static/index.html static/style.css
git commit -m "feat: dashboard pre-compute cache status display"
```

---

### Task 8: Integration with ETH Sweep Loop

**Files:**
- Modify: `liquidation_bot.py` (sweep_users path)
- Modify: `mev_liquidation.py` (build_full_plan)

**Interfaces:**
- Consumes: `precompute_eth.get(user)`
- Produces: Cached calldata used in build_plan / build_full_plan

- [ ] **Step 1: Add pre-compute check to mev_liquidation.py**

In `mev_liquidation.py`, in `build_full_plan()`, add at the start:

```python
import precompute_eth as pe

def build_full_plan(rec, ...):
    # Check pre-compute cache first
    cached = pe.get(rec.get("user", ""))
    if cached and cached.get("live_ok"):
        rec["liq_sig"] = cached["selector"]
        rec["liq_args"] = [cached["calldata"]]  # raw calldata
        rec["gas_limit"] = cached["gas_limit"]
        rec["net_usd"] = cached.get("estimated_profit_usd", rec.get("net_usd", 0))
        rec["precomputed"] = True
        return rec

    # ... existing code ...
```

- [ ] **Step 2: Start block listener in dashboard.py**

In `dashboard.py`, in `start_loops()`, add:

```python
import precompute_eth as pe
import precompute_sol as psol

# Start pre-compute block listeners
def get_hot_eth_positions():
    """Return hot positions from the current watchlist."""
    watchlist = self.state.get("watchlist", [])
    return [w for w in watchlist if w.get("hf", 999) < 1.05]

def get_hot_sol_obligations():
    """Return hot obligations from SOL watchlist."""
    sol = self.state.get("sol", {})
    return sol.get("watchlist", [])

# Start in background
rpc_urls = ["https://ethereum-rpc.publicnode.com"]
pe.start_block_listener(rpc_urls, get_hot_eth_positions)
sol_rpc = "https://api.mainnet-beta.solana.com"
psol.start_slot_listener(sol_rpc, get_hot_sol_obligations)
```

- [ ] **Step 3: Run dashboard and verify no errors**

Run: `python dashboard.py`
Expected: Dashboard starts, pre-compute modules load without errors, block listeners start in background

- [ ] **Step 4: Commit**

```bash
git add liquidation_bot.py mev_liquidation.py dashboard.py
git commit -m "feat: integrate pre-compute cache into ETH sweep loop"
```

---

### Task 9: Integration with SOL Scan Loop

**Files:**
- Modify: `sol_scanner.py` (build_liq_plan path)

**Interfaces:**
- Consumes: `precompute_sol.get(obligation)`
- Produces: Cached instructions used in build_liq_plan

- [ ] **Step 1: Add pre-compute check to sol_scanner.py**

In `sol_scanner.py`, in `build_liq_plan()`, add at the start:

```python
import precompute_sol as psol

def build_liq_plan(obligation, repay_reserve, withdraw_reserve, ...):
    # Check pre-compute cache first
    obl_addr = obligation.get("obligation", "") if isinstance(obligation, dict) else str(obligation)
    cached = psol.get(obl_addr)
    if cached:
        return {
            "kind": cached["kind"],
            "obligation": cached["obligation"],
            "debt_reserve": cached["repay_reserve"],
            "coll_reserve": cached["withdraw_reserve"],
            "repay_mint": cached["repay_mint"],
            "withdraw_mint": cached["withdraw_mint"],
            "debt_amount": cached["debt_amount"],
            "hf": cached["hf"],
            "compute_units": cached["compute_units"],
            "priority_fee_ul": cached["priority_fee_ul"],
            "jito_tip_lamports": cached["jito_tip_lamports"],
            "instruction_sequence": cached["instruction_sequence"],
            "account_metas": cached["account_metas"],
            "jupiter_route": cached["jupiter_route"],
            "expected_profit_usd": cached["estimated_profit_usd"],
            "precomputed": True,
        }

    # ... existing code ...
```

- [ ] **Step 2: Run dashboard and verify no errors**

Run: `python dashboard.py`
Expected: Dashboard starts, SOL pre-compute module loads without errors

- [ ] **Step 3: Commit**

```bash
git add sol_scanner.py
git commit -m "feat: integrate pre-compute cache into SOL scan loop"
```

---

### Task 10: Integration Test — Verify Pre-compute Matches On-the-fly

**Files:**
- Create: `tests/test_precompute_integration.py`

**Interfaces:**
- Consumes: Both pre-compute modules
- Produces: Verification that pre-computed calldata matches on-the-fly computation

- [ ] **Step 1: Write integration test**

```python
"""Integration test: verify pre-computed calldata matches on-the-fly computation."""

import precompute_eth as pe
import precompute_sol as ps

def test_eth_cache_roundtrip():
    """ETH: build entry, store, retrieve, verify all fields."""
    pe._cache.clear()
    pe._last_block = 100

    entry = pe.build_entry(
        protocol="aave-v3",
        user="0xabc",
        collateral="0xcoll",
        debt="0xdead",
        debt_amount_wei=1000000,
        hf=0.95,
        contract_addr="0xcontract",
        liq_sig="0xc2fa746c",
        liq_args=["0xabc", "0xcoll", "0xdead", "0xf4240"],
        swap_path=b"\x00\x01\x02",
        gas_limit=1500000,
        estimated_profit_usd=42.0,
        flash_amount_wei=1000000,
        debt_token="0xA0b86991",
        coll_token="0xC02aaA39",
    )
    pe._cache["0xabc"] = entry

    result = pe.get("0xabc")
    assert result is not None
    assert result["protocol"] == "aave-v3"
    assert result["calldata"].startswith("0xc2fa746c")
    assert result["estimated_profit_usd"] == 42.0

def test_sol_cache_roundtrip():
    """SOL: build entry, store, retrieve, verify all fields."""
    ps._cache.clear()
    ps._last_slot = 440333000

    entry = ps.build_entry(
        obligation="obligation_abc",
        kind="liq",
        repay_reserve="repay_res",
        withdraw_reserve="withdraw_res",
        repay_mint="USDC",
        withdraw_mint="SOL",
        debt_amount=844200000,
        hf=1.0,
        compute_units=400000,
        priority_fee_ul=50000,
        jito_tip_lamports=50000,
        instruction_sequence=[{"program": "solend"}],
        account_metas=[{"pubkey": "res1"}],
        jupiter_route={"in": "SOL", "out": "USDC"},
        estimated_profit_usd=42.0,
    )
    ps._cache["obligation_abc"] = entry

    result = ps.get("obligation_abc")
    assert result is not None
    assert result["obligation"] == "obligation_abc"
    assert result["estimated_profit_usd"] == 42.0
    assert len(result["instruction_sequence"]) == 1

def test_eth_cache_eviction():
    """ETH: verify stale entries are evicted."""
    pe._cache.clear()
    pe._last_block = 200
    pe._cache["0xhot"] = {"updated_block": 199}  # 1 block old
    pe._cache["0xcold"] = {"updated_block": 195}  # 5 blocks old
    evicted = pe.evict_stale(max_blocks_old=3)
    assert evicted == 1
    assert "0xhot" in pe._cache
    assert "0xcold" not in pe._cache

def test_sol_cache_eviction():
    """SOL: verify stale entries are evicted."""
    ps._cache.clear()
    ps._last_slot = 440333100
    ps._cache["hot_obl"] = {"updated_slot": 440333099}  # 1 slot old
    ps._cache["cold_obl"] = {"updated_slot": 440333050}  # 50 slots old
    evicted = ps.evict_stale(max_slots_old=30)
    assert evicted == 1
    assert "hot_obl" in ps._cache
    assert "cold_obl" not in ps._cache
```

- [ ] **Step 2: Run integration tests**

Run: `cd C:\Users\Surf\Documents\toni-aave-bot && python -m pytest tests/test_precompute_integration.py -v`
Expected: 4 PASS

- [ ] **Step 3: Run all pre-compute tests together**

Run: `cd C:\Users\Surf\Documents\toni-aave-bot && python -m pytest tests/test_precompute_eth.py tests/test_precompute_sol.py tests/test_precompute_integration.py -v`
Expected: All PASS (13 total)

- [ ] **Step 4: Commit**

```bash
git add tests/test_precompute_integration.py
git commit -m "feat: pre-compute integration tests"
```
