# Event-Driven Liquidation Feeds Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace polling-based liquidation detection with event-driven WSS/pubsub feeds on ETH and SOL using free-tier providers, fixing the live crash-loop and Morpho adapter bugs.

**Architecture:** Two new feed modules (`eth_event_feed.py`, `sol_event_feed.py`) hold parallel connections to free providers, dedupe events, track provider health with circuit-breaking, and emit normalized events through a shared `feed_registry` into the existing execution pipeline. Polling loops remain as degraded-mode fallback. Feeds are started by `dashboard.py` behind `.env` flags and exposed via `/api/state`.

**Tech Stack:** Python 3.11, aiohttp, `websockets` (already in requirements.txt), pytest. No new dependencies.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-21-event-driven-feeds-design.md`
- No new pip dependencies — use `websockets`, `asyncio`, stdlib only.
- All existing broadcast gates (sim_only / armed / funded) must apply to event-triggered fires; feeds never bypass them.
- Polling sweeps must keep running unchanged as fallback.
- New `.env` vars (see spec Config surface): `ETH_FEED_ENABLED`, `ETH_FEED_WSS_URLS`, `SOL_FEED_ENABLED`, `SOL_FEED_WS_URLS`, `FEED_BENCH_THRESHOLD=3`, `FEED_BENCH_WINDOW_S=300`, `FEED_BENCH_SECONDS=600`.
- Follow existing repo style: plain logging via `logging.getLogger(...)`, type hints, no comments unless essential.
- Run tests with: `python -m pytest tests/ -v` from repo root.

---

### Task 1: Fix the `str` vs `float` crash in hot-position filters

**Files:**
- Modify: `dashboard.py:3913-3919` (`_get_hot_eth_positions`)
- Test: `tests/test_hot_position_filters.py` (create)

**Interfaces:**
- Consumes: nothing new.
- Produces: module-level helper `coerce_hf(value, default=999.0) -> float` in `dashboard.py` used by `_get_hot_eth_positions`; later tasks rely on watchlist `hf` always being compared as float.

- [ ] **Step 1: Write the failing test**

```python
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dashboard


def test_coerce_hf_handles_string():
    assert dashboard.coerce_hf("0.97") == 0.97


def test_coerce_hf_handles_none_and_garbage():
    assert dashboard.coerce_hf(None) == 999.0
    assert dashboard.coerce_hf("") == 999.0
    assert dashboard.coerce_hf("abc") == 999.0


def test_get_hot_eth_positions_with_string_hf():
    class FakeDash:
        state = {"watchlist": [
            {"user": "0xA", "hf": "0.97"},
            {"user": "0xB", "hf": 1.5},
            {"user": "0xC", "hf": None},
        ]}
        _get_hot_eth_positions = dashboard.Dashboard._get_hot_eth_positions
    got = FakeDash._get_hot_eth_positions(FakeDash())
    assert [p["user"] for p in got] == ["0xA"]
```

Note: adjust `Dashboard` to the actual class name in `dashboard.py` (search `class .*:` near `state_api`). If the method is standalone, bind accordingly.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_hot_position_filters.py -v`
Expected: FAIL — `AttributeError: module 'dashboard' has no attribute 'coerce_hf'` or TypeError raised inside `_get_hot_eth_positions`.

- [ ] **Step 3: Implement**

In `dashboard.py`, above `_get_hot_eth_positions` (near line 3912), add:

```python
def coerce_hf(value, default: float = 999.0) -> float:
    try:
        f = float(value)
        return f if f == f else default
    except (TypeError, ValueError):
        return default
```

Replace line 3915 with:

```python
        return [w for w in watchlist if coerce_hf(w.get("hf")) < 1.05]
```

Also grep `dashboard.py` for other bare `hf` comparisons against floats (e.g. `.get("hf")` followed by `<` or `>`) and wrap them with `coerce_hf(...)` the same way. Known candidates: search regex `\.get\("hf"[^)]*\)\s*[<>]`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_hot_position_filters.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add dashboard.py tests/test_hot_position_filters.py
git commit -m "fix: coerce string hf values in hot-position filters (crash loop)"
```

---

### Task 2: Harden precompute ingress types

**Files:**
- Modify: `precompute_eth.py:110-158` (`refresh`), `precompute_sol.py:110-158` (`refresh`)
- Test: `tests/test_precompute_ingress.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: `precompute_eth.refresh` and `precompute_sol.refresh` tolerate string numerics (`debtToCover="123"`, `hf="0.9"`, `updated_slot` as str) without raising.

- [ ] **Step 1: Write the failing test**

```python
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import precompute_eth, precompute_sol


def test_eth_refresh_tolerates_string_numerics(monkeypatch):
    class FakeResp:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def json(self): return {"result": "0x10"}
    class FakeSession:
        def post(self, *a, **k): return FakeResp()
    monkeypatch.setattr("aiohttp.ClientSession", FakeSession)
    pos = [{"user": "0xA", "debtToCover": "12", "hf": "0.9",
            "flash_amount": "100", "net_usd": "1.5"}]
    asyncio.run(precompute_eth.refresh(pos, "http://x"))
    entry = precompute_eth.get("0xa")
    assert entry is not None and entry["hf"] == 0.9


def test_sol_refresh_tolerates_string_numerics(monkeypatch):
    class FakeResp:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def json(self): return {"result": 100}
    class FakeSession:
        def post(self, *a, **k): return FakeResp()
    monkeypatch.setattr("aiohttp.ClientSession", FakeSession)
    obl = [{"obligation": "Obl1", "hf": "0.9", "debt_amount": "5",
            "expected_profit_usd": "0.5"}]
    asyncio.run(precompute_sol.refresh(obl, "http://x"))
    assert precompute_sol.get("Obl1") is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_precompute_ingress.py -v`
Expected: FAIL with TypeError (`float`/`int` arithmetic on str) inside `build_entry` or `evict_stale`.

- [ ] **Step 3: Implement**

In both files add one shared-style helper near the top (duplicate locally, files are siblings):

```python
def _num(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
```

In `precompute_eth.refresh` wrap position fields: `debt_amount_wei=_num(pos.get("debtToCover"))`, `hf=float(_num(pos.get("hf"), 0) )` → replace direct `pos.get(...)` ints with `_num(...)`; in `precompute_sol.refresh` same for `debt_amount`, `hf`, `estimated_profit_usd`. In `evict_stale` (both), coerce: `_last_block - _num(v.get("data", {}).get("updated_block", 0))`.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_precompute_ingress.py tests/test_precompute_eth.py tests/test_precompute_sol.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add precompute_eth.py precompute_sol.py tests/test_precompute_ingress.py
git commit -m "fix: tolerate string numerics in precompute caches"
```

---

### Task 3: Fix Morpho GraphQL adapter errors

**Files:**
- Modify: `eth_lending/morpho.py:70-117` (`_gql_positions`)
- Test: `tests/test_morpho_gql.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: `_gql_positions()` returns `([], [])` fast (no network) for 10 minutes after an API-level rejection instead of hammering every sweep; logs response body on HTTP 400.

- [ ] **Step 1: Write the failing test**

```python
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "eth_lending"))

import time
import morpho


def test_gql_backoff_after_repeated_errors(monkeypatch):
    calls = {"n": 0}
    class FakeResp:
        def raise_for_status(self):
            raise RuntimeError("400 Client Error")
    def fake_post(*a, **k):
        calls["n"] += 1
        return FakeResp()
    monkeypatch.setattr(morpho.requests, "post", fake_post)
    morpho._gql_fail_until = 0.0
    a, errs = morpho._gql_positions()
    b, _ = morpho._gql_positions()
    first_calls = calls["n"]
    assert first_calls >= 1 and b == ([], [])
    morpho._gql_fail_until = time.time() + 600
    before = calls["n"]
    c, errs2 = morpho._gql_positions()
    assert calls["n"] == before and c == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_morpho_gql.py -v`
Expected: FAIL — `AttributeError: module 'morpho' has no attribute '_gql_fail_until'` or backoff not honored.

- [ ] **Step 3: Implement**

In `morpho.py` add module globals near `GRAPHQL`:

```python
_GQL_BACKOFF_S = 600
_gql_fail_until = 0.0
```

At top of `_gql_positions`:

```python
    global _gql_fail_until
    if time.time() < _gql_fail_until:
        return [], ["morpho gql backing off"]
```

In the `except Exception as e:` branch (line ~115) and after collecting GraphQL `errors`, set `_gql_fail_until = time.time() + _GQL_BACKOFF_S` once both queries failed. Add `import time` if missing. On `r.raise_for_status()` failure include `r.text[:200]` in the error string for diagnosis.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_morpho_gql.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add eth_lending/morpho.py tests/test_morpho_gql.py
git commit -m "fix: morpho graphql backoff + error-body diagnostics"
```

---

### Task 4: Shared feed plumbing — health, dedupe, registry

**Files:**
- Create: `feeds/__init__.py` (empty), `feeds/common.py`, `feeds/registry.py`
- Test: `tests/test_feeds_common.py`, `tests/test_feeds_registry.py`

**Interfaces:**
- Produces (used by Tasks 5–7):
  - `feeds.common.ProviderHealth(name, bench_threshold=3, bench_window_s=300, bench_seconds=600)` with `record_ok()`, `record_fail() -> bool`, `available -> bool`, `snapshot() -> dict`.
  - `feeds.common.FireDedupe(ttl_s=300)` with `seen(key) -> bool` (records and returns True if key fired within ttl).
  - `feeds.common.shard(items: list, n: int) -> list[list]`.
  - `feeds.registry.build_eth_registry(watchlist, feed_for_asset) -> dict[str, list[dict]]` mapping lowercase feed address → `[{"user","asset","side","hf","coll_usd","debt_usd","liq_threshold"}]`.
  - `feeds.registry.affected(registry, feed_addr) -> list[dict]`.
  - `feeds.registry.recomputed_hf(pos, price_ratio) -> float` where `price_ratio = new_price / old_price`; collateral-side positions scale `coll_usd` by ratio.
  - `feeds.registry.build_sol_shards(obligations: list[str], n: int) -> list[list[str]]` (alias of shard).

- [ ] **Step 1: Write failing tests**

`tests/test_feeds_common.py`:

```python
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from feeds.common import ProviderHealth, FireDedupe, shard


def test_provider_benches_after_threshold():
    ph = ProviderHealth("p1", bench_threshold=3, bench_window_s=300, bench_seconds=600)
    assert ph.available
    for _ in range(3):
        benched = ph.record_fail()
    assert benched and not ph.available
    ph._benched_until = time.time() - 1
    assert ph.available


def test_ok_resets_fail_streak():
    ph = ProviderHealth("p1", bench_threshold=3)
    ph.record_fail(); ph.record_fail()
    ph.record_ok()
    assert not ph.record_fail()


def test_dedupe_within_ttl():
    d = FireDedupe(ttl_s=60)
    assert not d.seen("k")
    assert d.seen("k")


def test_shard_round_robin():
    s = shard(list(range(7)), 3)
    assert [len(x) for x in s] == [3, 2, 2]
    assert sorted(sum(s, [])) == list(range(7))
```

`tests/test_feeds_registry.py`:

```python
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from feeds.registry import (build_eth_registry, affected, recomputed_hf,
                            build_sol_shards)


WL = [
    {"user": "0xA", "collateral": "0xC1", "hf": 1.04, "coll_usd": 1000.0,
     "debt_usd": 900.0, "liq_threshold": 0.85, "side": "collateral"},
    {"user": "0xB", "collateral": "0xC2", "hf": 1.5, "coll_usd": 500.0,
     "debt_usd": 300.0, "liq_threshold": 0.8, "side": "collateral"},
]


def test_build_and_lookup():
    reg = build_eth_registry(WL, {"0xC1": "0xF1", "0xC2": "0xF2"})
    assert [p["user"] for p in affected(reg, "0xf1")] == ["0xA"]
    assert affected(reg, "0xff") == []


def test_recomputed_hf_crosses_threshold():
    pos = affected(build_eth_registry(WL, {"0xC1": "0xF1"}), "0xF1")[0]
    hf = recomputed_hf(pos, price_ratio=0.90)
    assert hf < 1.0
    assert recomputed_hf(pos, price_ratio=1.0) > 1.0


def test_sol_shards():
    shards = build_sol_shards([f"obl{i}" for i in range(5)], 2)
    assert len(shards) == 2 and sum(len(s) for s in shards) == 5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_feeds_common.py tests/test_feeds_registry.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'feeds'`.

- [ ] **Step 3: Implement**

`feeds/common.py`:

```python
"""Shared feed plumbing: provider health, fire dedupe, sharding."""
import threading
import time


class ProviderHealth:
    def __init__(self, name: str, bench_threshold: int = 3,
                 bench_window_s: int = 300, bench_seconds: int = 600):
        self.name = name
        self.bench_threshold = max(1, bench_threshold)
        self.bench_window_s = bench_window_s
        self.bench_seconds = bench_seconds
        self._fails: list[float] = []
        self._benched_until = 0.0
        self.ok_count = 0
        self.fail_count = 0
        self.last_event_ts = 0.0
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return time.time() >= self._benched_until

    def record_ok(self) -> None:
        with self._lock:
            self.ok_count += 1
            self.last_event_ts = time.time()
            self._fails.clear()

    def record_fail(self) -> bool:
        with self._lock:
            self.fail_count += 1
            now = time.time()
            self._fails = [t for t in self._fails if now - t < self.bench_window_s]
            self._fails.append(now)
            if len(self._fails) >= self.bench_threshold:
                self._benched_until = now + self.bench_seconds
                self._fails.clear()
                return True
            return False

    def note_event(self) -> None:
        with self._lock:
            self.last_event_ts = time.time()

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "name": self.name,
                "available": self.available,
                "ok": self.ok_count,
                "fail": self.fail_count,
                "last_event_age_s": round(time.time() - self.last_event_ts, 1)
                if self.last_event_ts else None,
            }


class FireDedupe:
    def __init__(self, ttl_s: int = 300):
        self.ttl_s = ttl_s
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()

    def seen(self, key: str) -> bool:
        now = time.time()
        with self._lock:
            self._seen = {k: t for k, t in self._seen.items()
                          if now - t < self.ttl_s}
            if key in self._seen:
                return True
            self._seen[key] = now
            return False


def shard(items: list, n: int) -> list[list]:
    n = max(1, n)
    return [items[i::n] for i in range(n)]
```

`feeds/registry.py`:

```python
"""Event-to-position registries for both chains."""
import threading


def build_eth_registry(watchlist: list[dict],
                       feed_for_asset: dict[str, str]) -> dict[str, list[dict]]:
    reg: dict[str, list[dict]] = {}
    for w in watchlist or []:
        asset = (w.get("collateral") or "").lower()
        feed = feed_for_asset.get(asset)
        if not feed:
            continue
        rec = {
            "user": w.get("user", ""),
            "asset": asset,
            "side": w.get("side", "collateral"),
            "hf": float(w.get("hf") or 999.0),
            "coll_usd": float(w.get("coll_usd") or 0.0),
            "debt_usd": float(w.get("debt_usd") or 0.0),
            "liq_threshold": float(w.get("liq_threshold") or 0.8),
        }
        reg.setdefault(feed.lower(), []).append(rec)
    return reg


def affected(registry: dict[str, list[dict]], feed_addr: str) -> list[dict]:
    return registry.get((feed_addr or "").lower(), [])


def recomputed_hf(pos: dict, price_ratio: float) -> float:
    if price_ratio <= 0:
        price_ratio = 1.0
    coll = pos["coll_usd"] * (price_ratio if pos.get("side") == "collateral" else 1.0)
    debt = pos["debt_usd"]
    if debt <= 0:
        return 999.0
    return (coll * pos["liq_threshold"]) / debt


def build_sol_shards(obligations: list[str], n: int) -> list[list[str]]:
    from feeds.common import shard
    return shard(list(obligations or []), n)
```

`feeds/__init__.py`: empty file.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_feeds_common.py tests/test_feeds_registry.py -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add feeds/ tests/test_feeds_common.py tests/test_feeds_registry.py
git commit -m "feat: shared feed plumbing (health, dedupe, registries)"
```

---

### Task 5: ETH event feed

**Files:**
- Create: `feeds/eth_feed.py`
- Test: `tests/test_eth_feed.py`

**Interfaces:**
- Consumes: `ProviderHealth`, `FireDedupe` from Task 4.
- Produces: `EthEventFeed(wss_urls, on_oracle_tick, on_new_block, enabled=True)` with:
  - `on_oracle_tick: Callable[[str feed_addr_lower, float new_price], None]` — synchronous callback, called once per unique (feed, block) tick.
  - `on_new_block: Callable[[int], None]`
  - `async run()` — spawns one consumer task per URL; returns cancellable awaitable.
  - `stop()`, `stats() -> dict` shaped `{"mode": "live"|"degraded"|"off", "events_seen": int, "providers": [snapshot...]}`.
  - Module constant `ANSWER_UPDATED_TOPIC0 = "0x0559884fd3a4dab5802a313c0de45135c99f5adfe2e5d167a357b41c9ae4bf76"` (Chainlink `AnswerUpdated(int256,uint256,uint256)`).

- [ ] **Step 1: Write the failing test**

```python
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from feeds.eth_feed import EthEventFeed, parse_log


SUB_RES = json.dumps({"jsonrpc": "2.0", "id": 1,
                      "result": "0xsub"})
TICK = json.dumps({"jsonrpc": "2.0", "method": "eth_subscription", "params": {
    "subscription": "0xsub",
    "result": {"address": "0xF1", "topics": [
        "0x0559884fd3a4dab5802a313c0de45135c99f5adfe2e5d167a357b41c9ae4bf76",
        "0x00000000000000000000000000000000000000000000000000000000000f4240",
        "0x" + "0"*64,
    ], "blockNumber": "0x64"}}})
HEAD = json.dumps({"jsonrpc": "2.0", "method": "eth_subscription", "params": {
    "subscription": "0xsub",
    "result": {"number": "0x65"}}})


class FakeWS:
    def __init__(self, msgs):
        self._msgs = msgs
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def send(self, raw): pass
    async def recv(self): return SUB_RES
    async def iter_msgs(self):
        for m in self._msgs:
            yield m


def test_parse_answer_updated():
    evt = parse_log(json.loads(TICK)["params"]["result"])
    assert evt and evt["feed"] == "0xf1"
    assert abs(evt["price"] - 1_000_000) < 1e-6


def test_stats_mode_off_when_disabled():
    f = EthEventFeed([], on_oracle_tick=lambda a, p: None,
                     on_new_block=lambda b: None, enabled=False)
    assert f.stats()["mode"] == "off"


def test_run_consumes_events(monkeypatch):
    import feeds.eth_feed as ef
    ticks, blocks = [], []
    f = EthEventFeed(["wss://fake"], ticks.append, blocks.append)
    async def fake_connect(url, **k):
        return FakeWS([TICK, HEAD])
    monkeypatch.setattr(ef.websockets, "connect", fake_connect)
    async def once():
        await f._consume_one("wss://fake", single_pass=True)
    import asyncio
    asyncio.run(once())
    assert len(ticks) == 1 and blocks == [101]
    assert f.stats()["events_seen"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_eth_feed.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'feeds.eth_feed'`.

- [ ] **Step 3: Implement**

`feeds/eth_feed.py`:

```python
"""ETH event feed: WSS fan-out for oracle ticks, competitor liqs, new heads."""
import asyncio
import json
import logging
import websockets

from feeds.common import ProviderHealth, FireDedupe

log = logging.getLogger("eth_feed")

ANSWER_UPDATED_TOPIC0 = (
    "0x0559884fd3a4dab5802a313c0de45135c99f5adfe2e5d167a357b41c9ae4bf76")


def parse_log(lg: dict) -> dict | None:
    topics = lg.get("topics") or []
    if not topics:
        return None
    if topics[0].lower() == ANSWER_UPDATED_TOPIC0 and len(topics) >= 2:
        try:
            price = int(topics[1], 16)
            if price >= 2 ** 255:
                price -= 2 ** 256
            return {"kind": "oracle_tick", "feed": (lg.get("address") or "").lower(),
                    "price": float(price)}
        except ValueError:
            return None
    return None


class EthEventFeed:
    def __init__(self, wss_urls: list[str], on_oracle_tick, on_new_block,
                 enabled: bool = True, bench_threshold: int = 3,
                 bench_window_s: int = 300, bench_seconds: int = 600):
        self.urls = [u for u in (wss_urls or []) if u]
        self.on_oracle_tick = on_oracle_tick
        self.on_new_block = on_new_block
        self.enabled = enabled and bool(self.urls)
        self.health = {u: ProviderHealth(u, bench_threshold, bench_window_s,
                                         bench_seconds) for u in self.urls}
        self.dedupe = FireDedupe(ttl_s=30)
        self.events_seen = 0
        self.mode = "off"

    def stats(self) -> dict:
        if not self.enabled:
            self.mode = "off"
        else:
            live = sum(1 for h in self.health.values() if h.available)
            self.mode = "live" if live else "degraded"
        return {"mode": self.mode, "events_seen": self.events_seen,
                "providers": [h.snapshot() for h in self.health.values()]}

    def stop(self) -> None:
        self.enabled = False

    def _handle(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            return
        params = msg.get("params") or {}
        result = params.get("result")
        if not isinstance(result, dict):
            return
        self.events_seen += 1
        evt = parse_log(result)
        if evt:
            key = f"{evt['feed']}:{result.get('blockNumber')}"
            if not self.dedupe.seen(key):
                self.on_oracle_tick(evt["feed"], evt["price"])
            return
        number = result.get("number")
        if number is not None:
            try:
                self.on_new_block(int(number, 16))
            except (TypeError, ValueError):
                pass

    async def _consume_one(self, url: str, single_pass: bool = False) -> None:
        h = self.health[url]
        while self.enabled:
            try:
                async with websockets.connect(url, ping_interval=20) as ws:
                    sub = {"jsonrpc": "2.0", "id": 1, "method": "eth_subscribe",
                           "params": ["logs", {"topics": [ANSWER_UPDATED_TOPIC0]}]}
                    await ws.send(json.dumps(sub))
                    await ws.recv()
                    sub2 = {"jsonrpc": "2.0", "id": 2,
                            "method": "eth_subscribe", "params": ["newHeads"]}
                    await ws.send(json.dumps(sub2))
                    await ws.recv()
                    h.record_ok()
                    async for raw in ws:
                        h.note_event()
                        self._handle(raw)
                        if single_pass:
                            return
            except Exception as e:
                log.warning("eth feed %s error: %s", url, e)
                h.record_fail()
            if single_pass:
                return
            await asyncio.sleep(min(30, 2 ** min(h.fail_count, 5)))

    async def run(self) -> None:
        if not self.enabled:
            return
        await asyncio.gather(*(self._consume_one(u) for u in self.urls))
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_eth_feed.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add feeds/eth_feed.py tests/test_eth_feed.py
git commit -m "feat: ETH event feed with WSS fan-out and health tracking"
```

---

### Task 6: SOL event feed

**Files:**
- Create: `feeds/sol_feed.py`
- Test: `tests/test_sol_feed.py`

**Interfaces:**
- Consumes: `ProviderHealth`, `shard` from Task 4.
- Produces: `SolEventFeed(ws_urls, obligations_provider, on_account_change, on_program_log, enabled=True)` with:
  - `obligations_provider: Callable[[], list[str]]` — current watchlist pubkeys; re-sharded on each reconnect.
  - `on_account_change: Callable[[str pubkey], None]`
  - `on_program_log: Callable[[dict notification], None]`
  - `async run()`, `stop()`, `stats() -> dict` same shape as ETH side.

- [ ] **Step 1: Write the failing test**

```python
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from feeds.sol_feed import SolEventFeed


class FakeWS:
    def __init__(self, msgs):
        self.sent = []
        self._msgs = msgs
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def send(self, raw): self.sent.append(json.loads(raw))
    async def recv(self): return json.dumps({"jsonrpc": "2.0", "id": 1, "result": 1})
    async def iter_msgs(self):
        for m in self._msgs:
            yield m


ACCT = json.dumps({"jsonrpc": "2.0", "method": "accountNotification", "params": {
    "result": {"value": {"pubkey": "Obl1"}}}})


def test_account_notification_routes_to_callback(monkeypatch):
    import feeds.sol_feed as sf
    seen = []
    f = SolEventFeed(["ws://fake"], lambda: ["Obl1", "Obl2"],
                     seen.append, lambda n: None)
    async def fake_connect(url, **k):
        return FakeWS([ACCT])
    monkeypatch.setattr(sf.websockets, "connect", fake_connect)
    import asyncio
    asyncio.run(f._consume_one("ws://fake", shard_idx=0, total=1,
                               single_pass=True))
    assert seen == ["Obl1"]
    assert f.stats()["events_seen"] == 1


def test_disabled_mode_off():
    f = SolEventFeed([], lambda: [], lambda p: None, lambda n: None,
                     enabled=False)
    assert f.stats()["mode"] == "off"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_sol_feed.py -v`
Expected: FAIL — `No module named 'feeds.sol_feed'`.

- [ ] **Step 3: Implement**

`feeds/sol_feed.py`:

```python
"""SOL event feed: pubsub fan-out for obligations, program logs, slots."""
import asyncio
import json
import logging
import websockets

from feeds.common import ProviderHealth, shard

log = logging.getLogger("sol_feed")

SOLEND_PROGRAM = "So1endDgqrzd2skUgnsnCHupjG5CLQYp3LPJwCviYb"


class SolEventFeed:
    def __init__(self, ws_urls: list[str], obligations_provider,
                 on_account_change, on_program_log, enabled: bool = True,
                 bench_threshold: int = 3, bench_window_s: int = 300,
                 bench_seconds: int = 600):
        self.urls = [u for u in (ws_urls or []) if u]
        self.obligations_provider = obligations_provider
        self.on_account_change = on_account_change
        self.on_program_log = on_program_log
        self.enabled = enabled and bool(self.urls)
        self.health = {u: ProviderHealth(u, bench_threshold, bench_window_s,
                                         bench_seconds) for u in self.urls}
        self.events_seen = 0
        self.mode = "off"

    def stats(self) -> dict:
        if not self.enabled:
            self.mode = "off"
        else:
            live = sum(1 for h in self.health.values() if h.available)
            self.mode = "live" if live else "degraded"
        return {"mode": self.mode, "events_seen": self.events_seen,
                "providers": [h.snapshot() for h in self.health.values()]}

    def stop(self) -> None:
        self.enabled = False

    def _handle(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            return
        method = msg.get("method") or ""
        result = (msg.get("params") or {}).get("result") or {}
        self.events_seen += 1
        if method == "accountNotification":
            pubkey = (result.get("value") or {}).get("pubkey")
            if pubkey:
                self.on_account_change(pubkey)
        elif method == "programNotification":
            self.on_program_log(result)

    async def _consume_one(self, url: str, shard_idx: int, total: int,
                           single_pass: bool = False) -> None:
        h = self.health[url]
        while self.enabled:
            try:
                my_obls = shard(self.obligations_provider(), total)[shard_idx]
                async with websockets.connect(url, ping_interval=20) as ws:
                    rid = 1
                    for obl in my_obls:
                        await ws.send(json.dumps({
                            "jsonrpc": "2.0", "id": rid, "method": "accountSubscribe",
                            "params": [obl, {"encoding": "base64", "commitment":
                                             "confirmed"}]}))
                        rid += 1
                    await ws.send(json.dumps({
                        "jsonrpc": "2.0", "id": rid, "method": "logsSubscribe",
                        "params": [SOLEND_PROGRAM, {"commitment": "confirmed"}]}))
                    for _ in range(rid):
                        await ws.recv()
                    h.record_ok()
                    async for raw in ws:
                        h.note_event()
                        self._handle(raw)
                        if single_pass:
                            return
            except Exception as e:
                log.warning("sol feed %s error: %s", url, e)
                h.record_fail()
            if single_pass:
                return
            await asyncio.sleep(min(30, 2 ** min(h.fail_count, 5)))

    async def run(self) -> None:
        if not self.enabled:
            return
        n = len(self.urls)
        await asyncio.gather(
            *(self._consume_one(u, i, n) for i, u in enumerate(self.urls)))
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_sol_feed.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add feeds/sol_feed.py tests/test_sol_feed.py
git commit -m "feat: SOL event feed with pubsub fan-out and sharding"
```

---

### Task 7: Dashboard integration

**Files:**
- Modify: `dashboard.py` (imports near other imports; `start_loops` around line 3921-3959; `state_api` around line 4004)
- Modify: `.env.example`
- Test: `tests/test_dashboard_feeds_state.py`

**Interfaces:**
- Consumes: `EthEventFeed.stats()`, `SolEventFeed.stats()` from Tasks 5–6; `coerce_hf` from Task 1.
- Produces: `/api/state` payload gains `"feeds": {"eth": {...}, "sol": {...}}`; dashboard attributes `self.eth_feed`, `self.sol_feed`; oracle-tick handler `self._on_oracle_tick(feed_addr, price)` recomputes affected HFs from `self.state["watchlist"]` and sets `self._eth_hot_kick` when any cross below 1.0; SOL `on_account_change` sets `self._sol_hot_kick` (new `asyncio.Event` created in `start_loops` next to `self._eth_hot_kick` at line 3923).

- [ ] **Step 1: Write the failing test**

```python
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dashboard


def test_on_oracle_tick_flags_crossed_position():
    class FakeDash(dashboard.Dashboard):
        def __init__(self):
            self.state = {"watchlist": [{
                "user": "0xA", "collateral": "0xc1", "hf": 1.04,
                "coll_usd": 1000.0, "debt_usd": 900.0,
                "liq_threshold": 0.85, "side": "collateral"}]}
            self.kicks = 0
        def log(self, *a, **k): pass
    fd = FakeDash()
    fd._reg_cache = ({"0xc1": "0xf1"}, {})
    fd._on_oracle_tick("0xF1", 0.85)
    assert fd.kicks == 1


def test_feed_status_shape():
    class FakeDash(dashboard.Dashboard):
        eth_feed = None
        sol_feed = None
    out = FakeDash()._feeds_status()
    assert out == {"eth": {"mode": "off"}, "sol": {"mode": "off"}}
```

Adjust constructor/fakes to the real `Dashboard` class shape found in `dashboard.py` (the class owning `state_api`); keep the assertions identical.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_dashboard_feeds_state.py -v`
Expected: FAIL — missing `_on_oracle_tick` / `_feeds_status`.

- [ ] **Step 3: Implement**

In `dashboard.py`:

1. Imports section: `from feeds.eth_feed import EthEventFeed` and `from feeds.sol_feed import SolEventFeed` (wrap in try/except ImportError like existing optional imports).
2. In `Dashboard.__init__` (locate via `self.state = ` initialization): add `self.eth_feed = None`, `self.sol_feed = None`, `self._reg_cache = ({}, {})`, `self._sol_hot_kick = None`.
3. Add methods next to `_get_hot_eth_positions` (line ~3913):

```python
    def _rebuild_registries(self):
        wl = self.state.get("watchlist", [])
        feed_for_asset = {}
        try:
            from eth_lending.aave import oracle_feed_for_assets
            feed_for_asset = oracle_feed_for_assets(
                [w.get("collateral") for w in wl if w.get("collateral")])
        except Exception:
            feed_for_asset = {}
        self._reg_cache = (feed_registry.build_eth_registry(wl, feed_for_asset),
                           time.time())

    def _on_oracle_tick(self, feed_addr: str, price: float):
        reg, built_ts = self._reg_cache
        if time.time() - built_ts > 300:
            self._rebuild_registries()
            reg = self._reg_cache[0]
        crossed = []
        for pos in feed_registry.affected(reg, feed_addr):
            ratio = 0.0 if price <= 0 else 1.0
            hf = feed_registry.recomputed_hf(pos, ratio)
            if hf < 1.0:
                crossed.append(pos["user"])
        if crossed:
            self.log("eth-feed", "warn",
                     f"oracle tick {feed_addr} -> crossed: {crossed}")
            if self._eth_hot_kick:
                self._eth_hot_kick.set()

    def _on_sol_account_change(self, pubkey: str):
        if self._sol_hot_kick:
            self._sol_hot_kick.set()

    def _feeds_status(self):
        return {
            "eth": self.eth_feed.stats() if self.eth_feed else {"mode": "off"},
            "sol": self.sol_feed.stats() if self.sol_feed else {"mode": "off"},
        }
```

Add `from feeds import registry as feed_registry` to imports. Note: `ratio` uses 1.0 placeholder until Task 8 wires real prior-price lookup; crossing detection still works because `recomputed_hf` with ratio 1.0 equals cached-HF recompute — positions already below 1.0 in cache get flagged immediately on any tick of a related feed.

4. In `start_loops` after line 3923 (`self._eth_hot_kick = asyncio.Event()`):

```python
        self._sol_hot_kick = asyncio.Event()
        if os.environ.get("ETH_FEED_ENABLED", "1") == "1":
            urls = [u for u in (os.environ.get("ETH_FEED_WSS_URLS", "")
                                .split(",") if os.environ.get(
                                    "ETH_FEED_WSS_URLS") else []) if u]
            self.eth_feed = EthEventFeed(urls, self._on_oracle_tick,
                                         lambda b: None)
        if os.environ.get("SOL_FEED_ENABLED", "1") == "1":
            surls = [u for u in (os.environ.get("SOL_FEED_WS_URLS", "")
                                 .split(",") if os.environ.get(
                                     "SOL_FEED_WS_URLS") else []) if u]
            self.sol_feed = SolEventFeed(surls, self._get_hot_sol_pubkeys,
                                         self._on_sol_account_change,
                                         lambda n: None)
```

5. In the `coros = (...)` tuple construction (line ~3971), append conditionally:

```python
        extra = []
        if self.eth_feed:
            extra.append(self.eth_feed.run())
        if self.sol_feed:
            extra.append(self.sol_feed.run())
        coros = coros + tuple(extra)
```

(match however `coros` is consumed — likely `asyncio.gather(*coros)`; verify by reading lines 3971-3990.)

6. Add helper next to `_get_hot_sol_obligations`:

```python
    def _get_hot_sol_pubkeys(self):
        return [w.get("obligation") for w in (self.state.get("sol", {})
                .get("watchlist") or []) if w.get("obligation")]
```

7. In `state_api` (line ~4004) add to the returned dict: `"feeds": self._feeds_status(),`.

8. In shutdown handler `_handle_shutdown` (line ~3961): add `self.eth_feed.stop(); self.sol_feed.stop()` guarded with `if self.eth_feed` / `if self.sol_feed`.

Append to `.env.example`:

```
ETH_FEED_ENABLED=1
ETH_FEED_WSS_URLS=wss://eth-mainnet.g.alchemy.com/v2/YOUR_KEY,wss://ethereum-rpc.publicnode.com
SOL_FEED_ENABLED=1
SOL_FEED_WS_URLS=wss://api.mainnet-beta.solana.com,wss://solana-rpc.publicnode.com/websockets
FEED_BENCH_THRESHOLD=3
FEED_BENCH_WINDOW_S=300
FEED_BENCH_SECONDS=600
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_dashboard_feeds_state.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard.py .env.example tests/test_dashboard_feeds_state.py
git commit -m "feat: start event feeds from dashboard, expose feed status"
```

---

### Task 8: Real price-ratio crossing + `oracle_feed_for_assets`

**Files:**
- Modify: `eth_lending/aave.py` (add function), `dashboard.py` (`_on_oracle_tick`)
- Test: `tests/test_oracle_tick_crossing.py`

**Interfaces:**
- Consumes: `feeds.registry.recomputed_hf`, `eth_lending.util.call`.
- Produces: `eth_lending.aave.oracle_feed_for_assets(assets: list[str]) -> dict[str, str]` mapping lowercase asset → lowercase Chainlink feed via Aave `AggregatorsHub.getSource(asset)` (V3 Pool oracle) with graceful `{}` on RPC failure. Dashboard `_on_oracle_tick` keeps last-seen price per feed in `self._last_prices: dict[str, float]` and computes `ratio = new/old`.

- [ ] **Step 1: Write the failing test**

```python
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "eth_lending"))

import aave


def test_oracle_feed_map_graceful_failure(monkeypatch):
    monkeypatch.setattr(aave, "call", lambda to, data: None)
    assert aave.oracle_feed_for_assets(["0xC1"]) == {}


def test_last_price_ratio_logic():
    last = {}
    def ratio(feed, price):
        old = last.get(feed)
        last[feed] = price
        return None if old in (None, 0) else price / old
    assert ratio("f", 100.0) is None
    assert abs(ratio("f", 50.0) - 0.5) < 1e-9
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_oracle_tick_crossing.py -v`
Expected: FAIL — `AttributeError: module 'aave' has no attribute 'oracle_feed_for_assets'`.

- [ ] **Step 3: Implement**

In `eth_lending/aave.py` append:

```python
def oracle_feed_for_assets(assets) -> dict[str, str]:
    out: dict[str, str] = {}
    oracle = ORACLE_ADDR if "ORACLE_ADDR" in dir() else None
    if not oracle:
        try:
            oracle = _oracle_address()
        except Exception:
            return out
    sel = "0x3850c7bd"  # getSource(address)
    for a in assets or []:
        try:
            padded = util.pad_addr((a or "").lower())
            raw = util.call(oracle, sel + padded)
            if raw and len(raw) >= 66:
                feed = "0x" + raw[-40:]
                if int(feed, 16):
                    out[a.lower()] = feed.lower()
        except Exception:
            continue
    return out
```

Read the top of `aave.py` first and adapt: reuse whatever pool/oracle address constant and `util.call` helpers already exist there (mirror how `scan_liquidatable` performs eth_calls). If no oracle constant exists, fetch `getAssetPrice`'s source via the pool's `ADDRESSES_PROVIDER.getPool()` then `POOL.getSource(asset)` — mirror existing patterns in `spark.py:_oracle()`.

In `dashboard.py` `_on_oracle_tick`, replace the placeholder ratio block:

```python
        last = getattr(self, "_last_prices", None)
        if last is None:
            last = self._last_prices = {}
        old = last.get(feed_addr)
        last[feed_addr] = price
        ratio = 1.0 if not old else price / old
```

and use `recomputed_hf(pos, ratio)`.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_oracle_tick_crossing.py tests/test_dashboard_feeds_state.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add eth_lending/aave.py dashboard.py tests/test_oracle_tick_crossing.py
git commit -m "feat: real oracle price ratios drive ETH crossing detection"
```

---

### Task 9: Feed status cards in dashboard UI

**Files:**
- Modify: `static/index.html` (find the bots/status card cluster — search `bc-stat-label`), `static/app.js` (find where `/api/state` fields render — search `bots.` usage)

**Interfaces:**
- Consumes: `state.feeds.{eth,sol}.{mode,events_seen,providers}` from Task 7.
- Produces: two pills labeled `ETH FEED` / `SOL FEED` colored green (`live`), amber (`degraded`), gray (`off`), with tooltip showing per-provider ok/fail counts.

- [ ] **Step 1: Locate render point**

Grep `static/app.js` for where `bots` statuses are rendered into DOM (search `intel` or `broadcast` pill updates). Note the exact function and element-id convention used (e.g., `el('bot-intel')`).

- [ ] **Step 2: Implement**

Following the located convention exactly, add two pills in `index.html` next to existing bot pills:

```html
<div class="pill" id="pill-feed-eth" title="">ETH FEED</div>
<div class="pill" id="pill-feed-sol" title="">SOL FEED</div>
```

and in `app.js` inside the state-render function:

```javascript
function feedPill(id, feed) {
  const el = document.getElementById(id);
  if (!el) return;
  const mode = (feed && feed.mode) || "off";
  el.dataset.state = mode;
  el.textContent = (id.endsWith("eth") ? "ETH FEED " : "SOL FEED ") + mode.toUpperCase();
  const provs = (feed && feed.providers) || [];
  el.title = provs.map(p => `${p.name}: ok=${p.ok} fail=${p.fail}`).join("\n") || "disabled";
}
// inside render: feedPill("pill-feed-eth", st.feeds && st.feeds.eth);
//                feedPill("pill-feed-sol", st.feeds && st.feeds.sol);
```

Match existing CSS state classes (`data-state` conventions or class toggles) used by bot pills; copy their color rules for `live`/`degraded`/`off`.

- [ ] **Step 3: Verify manually**

Run: `python dashboard.py --host 127.0.0.1 --port 8081 --broadcast` then open http://127.0.0.1:8081/ and confirm pills render (gray/off without URLs configured, since `.env` lacks feed URLs by default).
Expected: page loads, pills visible, no JS console errors.

- [ ] **Step 4: Commit**

```bash
git add static/index.html static/app.js
git commit -m "feat: feed status pills on dashboard"
```

---

### Task 10: Shadow-mode metrics + full verification

**Files:**
- Modify: `dashboard.py` (`_on_oracle_tick`, `_on_sol_account_change`): increment counters `self.state.setdefault("shadow", {"eth_ticks": 0, "eth_crossings": 0, "sol_events": 0})`
- Test: `tests/test_shadow_counters.py`

**Interfaces:**
- Produces: `state.shadow` counters surfaced in `/api/state`; these are the shadow-mode go/no-go metrics from the spec (≥90% catch rate measured over 1–2 weeks against competitor landings).

- [ ] **Step 1: Write the failing test**

```python
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard


def test_shadow_counters_increment():
    class FakeDash(dashboard.Dashboard):
        def __init__(self):
            self.state = {"watchlist": [], "sol": {}}
            self._reg_cache = ({}, 0)
            self._last_prices = {}
        def log(self, *a, **k): pass
    fd = FakeDash()
    fd._on_oracle_tick("0xF1", 100.0)
    fd._on_sol_account_change("Obl1")
    assert fd.state["shadow"]["eth_ticks"] == 1
    assert fd.state["shadow"]["sol_events"] == 1
    assert fd.state["shadow"]["eth_crossings"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_shadow_counters.py -v`
Expected: FAIL — `KeyError: 'shadow'`.

- [ ] **Step 3: Implement**

In `_on_oracle_tick`: first lines —

```python
        sh = self.state.setdefault("shadow", {"eth_ticks": 0,
                                              "eth_crossings": 0,
                                              "sol_events": 0})
        sh["eth_ticks"] += 1
```

increment `sh["eth_crossings"] += 1` inside the `if crossed:` block. In `_on_sol_account_change`: `self.state.setdefault("shadow", {...})["sol_events"] += 1`. Expose in `state_api` output: `"shadow": self.state.get("shadow", {}),`.

- [ ] **Step 4: Full suite + lint**

Run: `python -m pytest tests/ -v`
Expected: ALL PASS (existing suites included).

Run: `python -m py_compile dashboard.py feeds/eth_feed.py feeds/sol_feed.py feeds/common.py feeds/registry.py eth_lending/aave.py eth_lending/morpho.py precompute_eth.py precompute_sol.py`
Expected: no output (success).

- [ ] **Step 5: Live smoke test**

Kill old dashboards, start fresh: `python dashboard.py --host 127.0.0.1 --port 8081 --broadcast`, wait 60s, then:
- `GET /api/state` → `feeds.eth.mode` is `live` (publicnode WSS works without key) or `degraded`; no `Block listener error` spam in stderr over 5 minutes.
- Confirm `shadow.eth_ticks` grows while CHAINLINK feeds tick.

- [ ] **Step 6: Commit**

```bash
git add dashboard.py tests/test_shadow_counters.py
git commit -m "feat: shadow-mode counters for feed catch-rate measurement"
```

---

## Self-Review Notes

- Spec coverage: ETH feed (Task 5), SOL feed (Task 6), registry (Task 4), crash fix (Tasks 1–2), Morpho fix (Task 3), dashboard integration + status (Tasks 7, 9), shadow gate (Task 10), .env surface (Task 7). L2 adapters and paid-key gRPC are spec non-goals. ✔
- Type consistency: `stats()` shape identical across both feeds; `coerce_hf` defined once in Task 1 and reused; registry signatures match between Tasks 4, 7, 8. ✔
- Known risk: `FakeDash` binding style in tests depends on the real `Dashboard` class name/constructor — implementers must adapt fakes to actual class shape (noted inline in Tasks 1, 7, 10).
