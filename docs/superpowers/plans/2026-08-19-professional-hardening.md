# Professional Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform both ETH and SOL bots from PROFESSIONAL_WITH_GAPS to PROFESSIONAL by fixing logging, thread safety, shutdown handling, retry logic, bare excepts, memory growth, session lifecycle, and wiring the hybrid executor.

**Architecture:** Targeted hardening of existing modules — no rewrites, no new modules. Add logging, locks, signal handlers, and exception specificity in-place.

**Tech Stack:** Python 3.x, threading, asyncio, logging, signal

## Global Constraints

- Do NOT restructure sol_scanner.py (6085 lines) — too risky, fix in-place
- Do NOT change public interfaces — all changes are internal
- All existing tests must continue passing
- New tests required for: thread safety locks, backoff/circuit breaker, shutdown handler

---

## Task 1: Add Structured Logging to liquidation_bot.py

**Files:**
- Modify: `aave-v4-liquidation-bot/liquidation_bot.py`

**Interfaces:**
- Produces: `log = logging.getLogger("liquidation_bot")` used throughout

### Steps

- [ ] **Step 1: Add logging import and logger after existing imports**

At the top of the file (after line ~30), add:

```python
import logging
log = logging.getLogger("liquidation_bot")
```

- [ ] **Step 2: Instrument key functions with log calls**

Add log calls to these locations:

| Location | Level | Message |
|----------|-------|---------|
| `jrpc()` failover attempt | `log.debug` | "RPC %s failed (%s), trying next" |
| `jrpc()` all fail | `log.error` | "All RPCs failed for %s: %s" |
| `sweep_users()` start | `log.info` | "Sweep: %d users, block=%s" |
| `sweep_users()` per-user exception | `log.warning` | "User %s sweep error: %s" |
| `sweep_users()` result | `log.info` | "Sweep complete: %d opportunities" |
| `get_logs_chunked()` error | `log.warning` | "getLogs chunk error block %d-%d: %s" |
| `load_borrower_cache()` | `log.debug` | "Loaded %d borrowers from cache" |
| `get_v3_account()` error | `log.debug` | "V3 account %s fetch failed" |
| `get_v4_account()` error | `log.debug` | "V4 account %s fetch failed" |
| `pending_event_users()` | `log.debug` | "Pending event users: %d" |

- [ ] **Step 3: Run existing tests**

Run: `cd C:\Users\Surf\Documents\toni-aave-bot && python -m pytest tests/ -v --tb=short`
Expected: existing tests still pass

- [ ] **Step 4: Commit**

```bash
git add aave-v4-liquidation-bot/liquidation_bot.py
git commit -m "feat: add structured logging to liquidation_bot.py"
```

---

## Task 2: Add Structured Logging to sol_scanner.py

**Files:**
- Modify: `sol_scanner.py`

**Interfaces:**
- Produces: `log = logging.getLogger("sol_scanner")` used throughout

### Steps

- [ ] **Step 1: Add logging import and logger**

After the existing imports, add:

```python
import logging
log = logging.getLogger("sol_scanner")
```

- [ ] **Step 2: Instrument key functions**

| Location | Level | Message |
|----------|-------|---------|
| `sol_rpc()` failover | `log.debug` | "SOL RPC %s failed, trying next" |
| `sol_rpc()` all fail | `log.error` | "All SOL RPCs failed for %s" |
| `_jup_quote()` 429 | `log.warning` | "Jupiter 429, attempt %d/3" |
| `_jup_quote()` success | `log.debug` | "Jupiter quote: %s -> %s" |
| `_ensure_solend_index()` | `log.info` | "Solend index refreshed: %d reserves" |
| `_parse_reserve_account()` | `log.debug` | "Reserve %s parsed" |
| `decode_solend_competitors()` | `log.info` | "Decoded %d competitor txs" |
| `watch_solend_landing()` | `log.info` | "Watch landing: %d sigs" |
| `fetch_jito_tip_pressure()` | `log.debug` | "Jito tip p50=%.1f p90=%.1f" |
| `_live_send_liq()` stages | `log.info` | "Live liq: sim=%s bundle=%s" |
| `_live_send_liq()` error | `log.error` | "Live liq failed: %s" |
| `submit_sol_plan()` | `log.info` | "Submit plan: kind=%s mode=%s" |
| `fetch_sol_price()` | `log.debug` | "SOL price: $%.2f" |

- [ ] **Step 3: Run existing tests**

Run: `cd C:\Users\Surf\Documents\toni-aave-bot && python -m pytest tests/ -v --tb=short`
Expected: existing tests still pass

- [ ] **Step 4: Commit**

```bash
git add sol_scanner.py
git commit -m "feat: add structured logging to sol_scanner.py"
```

---

## Task 3: Thread Safety — All Modules

**Files:**
- Modify: `precompute_eth.py`
- Modify: `precompute_sol.py`
- Modify: `gas_bidder.py`
- Modify: `execution_tracker.py`
- Modify: `backrun.py`
- Modify: `sol_scanner.py` (global state locks)

### Steps

- [ ] **Step 1: precompute_eth.py — add threading.Lock**

Add `import threading` and a module-level `_lock = threading.Lock()`. Wrap `_cache` and `_last_block` mutations/reads:

```python
_lock = threading.Lock()

def get(obligation_addr: str) -> dict | None:
    with _lock:
        entry = _cache.get(obligation_addr)
    if not entry:
        with _lock:
            global _cache_misses
            _cache_misses += 1
        return None
    if time.time() - entry["ts"] > CACHE_TTL:
        with _lock:
            _cache.pop(obligation_addr, None)
            global _cache_misses
            _cache_misses += 1
        return None
    with _lock:
        global _cache_hits
        _cache_hits += 1
    return entry.get("data")
```

Wrap `refresh()` similarly.

- [ ] **Step 2: precompute_sol.py — same pattern as precompute_eth.py**

- [ ] **Step 3: gas_bidder.py — add Lock for _competitor_window**

```python
import threading

class GasBiddingEngine:
    def __init__(self, ...):
        ...
        self._lock = threading.Lock()
    
    def track_competitor(self, tx):
        with self._lock:
            self._competitor_window.append(...)
            if len(self._competitor_window) > 100:
                self._competitor_window = self._competitor_window[-100:]
    
    def get_competitor_p95(self):
        with self._lock:
            window = list(self._competitor_window)
        ...
```

- [ ] **Step 4: execution_tracker.py — add Lock for all mutable state**

```python
import threading

class ExecutionTracker:
    def __init__(self):
        self._lock = threading.Lock()
        ...
    
    def log_attempt(self, attempt):
        with self._lock:
            self._attempts.append(attempt)
            ...
    
    def should_skip(self, opp_id, current_block):
        with self._lock:
            ...
    
    def get_adapted_bid(self, ...):
        with self._lock:
            ...
```

- [ ] **Step 5: backrun.py — add Lock for _recent_competitor_txs**

```python
import threading

class BackrunEngine:
    def __init__(self):
        self._lock = threading.Lock()
        ...
    
    def detect_competitor_tx(self, block_txs, our_tx_hash):
        ...
        with self._lock:
            self._recent_competitor_txs.append(landing)
        return landing
    
    def get_recent_competitors(self, count=10):
        with self._lock:
            return list(self._recent_competitor_txs[-count:])
```

- [ ] **Step 6: sol_scanner.py — add module-level locks for global state**

Add after the global declarations (~line 860):

```python
import threading
_RESERVE_INDEX_LOCK = threading.Lock()
_COMP_LOCK = threading.Lock()
_SEEN_SIGS_LOCK = threading.Lock()
_OBLIGATION_KEYS_LOCK = threading.Lock()
_HYDRATE_CACHE_LOCK = threading.Lock()
```

Then wrap the mutation sites. This is the riskiest part — only wrap the inner mutations, not entire functions.

- [ ] **Step 7: Run all tests**

Run: `cd C:\Users\Surf\Documents\toni-aave-bot && python -m pytest tests/ -v --tb=short`
Expected: all tests pass

- [ ] **Step 8: Commit**

```bash
git add precompute_eth.py precompute_sol.py gas_bidder.py execution_tracker.py backrun.py sol_scanner.py
git commit -m "feat: add thread safety locks to all shared mutable state"
```

---

## Task 4: Graceful Shutdown

**Files:**
- Modify: `precompute_eth.py`
- Modify: `precompute_sol.py`
- Modify: `dashboard.py`

### Steps

- [ ] **Step 1: precompute_eth.py — add shutdown event**

```python
import asyncio
_shutdown = asyncio.Event() if hasattr(asyncio, 'Event') else None

def stop():
    """Signal all loops to stop."""
    global _shutdown
    _shutdown = True
```

In `_block_loop()`, change `while True:` to:

```python
async def _block_loop(rpc_urls, positions_fn):
    global _last_block, _shutdown
    while not _shutdown:
        ...
```

- [ ] **Step 2: precompute_sol.py — same pattern**

- [ ] **Step 3: dashboard.py — add signal handlers**

In `start_loops()`, add:

```python
import signal

def _handle_shutdown(sig):
    self.log("shutdown", "warn", f"Received {sig.name}, shutting down...")
    _pre_eth.stop()
    _pre_sol.stop()
    # Cancel all tasks
    for task in asyncio.all_tasks():
        task.cancel()

for sig in (signal.SIGTERM, signal.SIGINT):
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(sig, lambda s=sig: _handle_shutdown(s))
```

- [ ] **Step 4: Run tests**

Run: `cd C:\Users\Surf\Documents\toni-aave-bot && python -m pytest tests/ -v --tb=short`
Expected: all tests pass

- [ ] **Step 5: Commit**

```bash
git add precompute_eth.py precompute_sol.py dashboard.py
git commit -m "feat: add graceful shutdown with signal handlers"
```

---

## Task 5: Exponential Backoff + Circuit Breaker for RPC

**Files:**
- Modify: `aave-v4-liquidation-bot/liquidation_bot.py` (jrpc function)
- Modify: `sol_scanner.py` (sol_rpc function)

### Steps

- [ ] **Step 1: liquidation_bot.py — add backoff to jrpc()**

Replace the flat sleep in `jrpc()` (~line 146) with:

```python
import random

# Add after RPC_CALL definition:
_RPC_CIRCUIT = {}  # url -> (fail_count, open_until)

def _rpc_is_open(url):
    """Circuit breaker: True if endpoint is healthy."""
    state = _RPC_CIRCUIT.get(url)
    if not state:
        return True
    fails, open_until = state
    if time.time() > open_until:
        return True  # half-open: try again
    return False

def _rpc_record_failure(url):
    """Record failure, open circuit if threshold reached."""
    state = _RPC_CIRCUIT.get(url, (0, 0))
    fails = state[0] + 1
    if fails >= 3:
        _RPC_CIRCUIT[url] = (fails, time.time() + min(60 * (2 ** min(fails - 3, 4)), 600))
    else:
        _RPC_CIRCUIT[url] = (fails, 0)

def _rpc_record_success(url):
    _RPC_CIRCUIT.pop(url, None)
```

In `jrpc()`, replace the flat sleep:

```python
def jrpc(url, method, params):
    for attempt, u in enumerate(url):
        if not _rpc_is_open(u):
            continue
        try:
            r = _rpc_requests(u, method, params)
            _rpc_record_success(u)
            return r
        except Exception as e:
            _rpc_record_failure(u)
            if attempt < len(url) - 1:
                time.sleep(0.05 * (2 ** min(attempt, 4)) + random.uniform(0, 0.05))
    raise RuntimeError(f"all RPCs failed for {method}")
```

- [ ] **Step 2: sol_scanner.py — add backoff to sol_rpc()**

Same pattern for `sol_rpc()` (~line 354-370). Replace the loop with:

```python
def sol_rpc(method, params, **kw):
    urls = list(RPC_URLS)
    for attempt, u in enumerate(urls):
        if not _rpc_is_open(u):
            continue
        try:
            r = _rpc_requests(u, method, params, **kw)
            _rpc_record_success(u)
            return r
        except Exception as e:
            _rpc_record_failure(u)
            if attempt < len(urls) - 1:
                time.sleep(0.05 * (2 ** min(attempt, 4)) + random.uniform(0, 0.05))
    raise RuntimeError(f"all SOL RPCs failed for {method}")
```

- [ ] **Step 3: Run tests**

Run: `cd C:\Users\Surf\Documents\toni-aave-bot && python -m pytest tests/ -v --tb=short`

- [ ] **Step 4: Commit**

```bash
git add aave-v4-liquidation-bot/liquidation_bot.py sol_scanner.py
git commit -m "feat: exponential backoff + circuit breaker for RPC retries"
```

---

## Task 6: Refactor Bare Excepts in liquidation_bot.py

**Files:**
- Modify: `aave-v4-liquidation-bot/liquidation_bot.py`

### Steps

For each bare `except Exception`, replace with the specific exception type. Key replacements:

| Line | Current | Replace With |
|------|---------|-------------|
| 217 | `except Exception: pass` | `except (KeyError, TypeError, ValueError): pass` |
| 237 | `except Exception: px = 0.0` | `except (requests.RequestException, KeyError, ValueError): px = 0.0` |
| 260 | `except Exception: return 0.0` | `except (requests.RequestException, KeyError, ValueError): return 0.0` |
| 276 | `except Exception: addrs = []` | `except (requests.RequestException, KeyError, ValueError): addrs = []` |
| 293 | `except Exception: return [], []` | `except (KeyError, TypeError, ValueError): return [], []` |
| 386 | `except Exception: return None` | `except (requests.RequestException, KeyError, ValueError): return None` |
| 395 | `except Exception: return None` | `except (requests.RequestException, KeyError, ValueError): return None` |
| 511 | `except Exception: return []` | `except (json.JSONDecodeError, OSError, ValueError): return []` |

Leave `jrpc()` (144) and `get_logs_chunked()` (560) and `pending_event_users()` (644) as `except Exception as e` with logging — these are intentional catch-all-with-log patterns.

- [ ] **Step 1: Make replacements**
- [ ] **Step 2: Run tests**
- [ ] **Step 3: Commit**

```bash
git add aave-v4-liquidation-bot/liquidation_bot.py
git commit -m "feat: replace bare excepts with specific exceptions in liquidation_bot"
```

---

## Task 7: Cap Memory Growth in execution_tracker + gas_bidder

**Files:**
- Modify: `execution_tracker.py`
- Modify: `gas_bidder.py`

### Steps

- [ ] **Step 1: execution_tracker.py — cap _attempts list and add trim**

In `log_attempt()`, after appending, trim:

```python
MAX_ATTEMPTS = 500

def log_attempt(self, attempt):
    with self._lock:
        self._attempts.append(attempt)
        if len(self._attempts) > MAX_ATTEMPTS:
            self._attempts = self._attempts[-MAX_ATTEMPTS:]
```

In `should_skip()`, limit the scan window:

```python
def should_skip(self, opp_id, current_block):
    with self._lock:
        recent = [a for a in self._attempts[-100:] if a.opportunity_id == opp_id]
    ...
```

- [ ] **Step 2: gas_bidder.py — cap _competitor_window with bounded deque**

Replace the list with `collections.deque(maxlen=100)`:

```python
from collections import deque

class GasBiddingEngine:
    def __init__(self, ...):
        self._competitor_window = deque(maxlen=100)
```

Remove the manual trim in `track_competitor()`.

- [ ] **Step 3: Run tests**

- [ ] **Step 4: Commit**

```bash
git add execution_tracker.py gas_bidder.py
git commit -m "feat: cap memory growth in execution_tracker and gas_bidder"
```

---

## Task 8: Fix alchemy_relay Session Lifecycle

**Files:**
- Modify: `alchemy_relay.py`

### Steps

- [ ] **Step 1: Add async context manager and session protection**

```python
class AlchemyRelay:
    def __init__(self, ...):
        ...
        self._lock = asyncio.Lock() if hasattr(asyncio, 'Lock') else None
        self._closed = False
    
    async def _get_session(self) -> aiohttp.ClientSession:
        if self._closed:
            raise RuntimeError("relay is closed")
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session
    
    async def close(self):
        self._closed = True
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, *args):
        await self.close()
```

- [ ] **Step 2: Add specific exception handling in send methods**

Replace `except Exception as e` with:

```python
except aiohttp.ClientError as e:
    log.warning("Alchemy network error: %s", e)
    return {"success": False, "error": f"network: {e}"}
except asyncio.TimeoutError:
    log.warning("Alchemy timeout")
    return {"success": False, "error": "timeout"}
except Exception as e:
    log.error("Alchemy unexpected error: %s", e)
    return {"success": False, "error": str(e)}
```

- [ ] **Step 3: Run tests**

- [ ] **Step 4: Commit**

```bash
git add alchemy_relay.py
git commit -m "feat: fix alchemy_relay session lifecycle and exception handling"
```
