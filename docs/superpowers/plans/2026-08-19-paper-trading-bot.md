# Paper Trading Bot — Daily Range Breakout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a backend paper trading bot that trades ETH and SOL using the Daily Range Breakout strategy on 5m candles, with persistent JSON logs and ML-ready trade records, plus frontend chart overlays showing range lines, trade markers, and PnL.

**Architecture:** Backend `PaperTrader` class in `dashboard.py` receives 5m candles from a background loop, computes range/signals/fills, and pushes state via WebSocket. Frontend renders range lines (Lightweight Charts `addLineSeries`), trade markers (`.setMarkers()`), and a PnL strip. Trades append to `paper_trades.json`.

**Tech Stack:** Python 3 (aiohttp), Lightweight Charts v4.1.3 (TradingView), Binance REST API, JSON persistence.

## Global Constraints

- Paper trades only — no real money, no wallet interaction
- All fills at candle close price (no slippage in v1)
- One position per asset at a time
- Bot runs only while dashboard server is running
- Existing candlestick chart and all other dashboard features remain unchanged
- Range lines and markers overlay on top of existing candles

---

## Task 1: PaperTrader class — core strategy logic

**Files:**
- Create: `paper_trader.py`

**Interfaces:**
- Consumes: candle arrays `[[ts_ms, open, high, low, close, volume], ...]` from Binance klines
- Produces: `PaperTrader` class with `.on_candle(candle)` method, `.state` dict, `.trades` list

- [ ] **Step 1: Create `paper_trader.py` with the PaperTrader class skeleton**

```python
"""Paper trading bot — Daily Range Breakout strategy on 5m candles."""
import time
import json
import os
from collections import deque

class PaperTrader:
    """Paper trades ETH or SOL using the Daily Range Breakout strategy."""

    def __init__(self, asset: str, starting_balance: float = 100.0):
        self.asset = asset
        self.starting_balance = starting_balance
        self.balance = starting_balance
        self.range_mode = "orb"  # "orb" or "prev_day"
        self.enabled = True

        # Range state
        self.range_high = None
        self.range_low = None
        self.range_start_ts = None
        self.range_candles = []  # candles collected for range building
        self.range_ready = False

        # Position state
        self.position = None  # dict or None
        # position = {direction, entry_price, entry_ts, qty, leg1_done, trail_stop, best_price, atr}

        # Trade log
        self.trades = []
        self.cooldown_until = 0  # timestamp — no new trade until this time

        # Recent candles for ATR / RSI
        self._candle_buffer = deque(maxlen=200)

    def state_dict(self):
        """Return serializable state for WebSocket."""
        return {
            "asset": self.asset,
            "balance": round(self.balance, 4),
            "starting_balance": self.starting_balance,
            "range_mode": self.range_mode,
            "range_high": self.range_high,
            "range_low": self.range_low,
            "range_ready": self.range_ready,
            "range_start_ts": self.range_start_ts,
            "position": self.position,
            "enabled": self.enabled,
            "stats": self._compute_stats(),
            "recent_trades": [t for t in self.trades[-10:]],
        }

    def _compute_stats(self):
        """Compute summary stats from trade history."""
        wins = [t for t in self.trades if t.get("total_pnl", 0) > 0]
        losses = [t for t in self.trades if t.get("total_pnl", 0) <= 0]
        total_pnl = sum(t.get("total_pnl", 0) for t in self.trades)
        return {
            "total_trades": len(self.trades),
            "wins": len(wins),
            "losses": len(losses),
            "pnl": round(total_pnl, 4),
            "pnl_pct": round(total_pnl / self.starting_balance * 100, 2),
            "win_rate": round(len(wins) / len(self.trades) * 100, 1) if self.trades else 0,
        }
```

- [ ] **Step 2: Add range computation methods**

Add these methods to the `PaperTrader` class:

```python
    def _compute_atr(self, candles, period=14):
        """Compute ATR(period) from recent candles."""
        if len(candles) < period + 1:
            return None
        trs = []
        for i in range(1, len(candles)):
            h = candles[i][2]  # high
            l = candles[i][3]  # low
            pc = candles[i - 1][4]  # prev close
            tr = max(h - l, abs(h - pc), abs(l - pc))
            trs.append(tr)
        if len(trs) < period:
            return None
        return sum(trs[-period:]) / period

    def _compute_rsi(self, candles, period=14):
        """Compute RSI(period) from recent candles."""
        if len(candles) < period + 1:
            return None
        closes = [c[4] for c in candles]
        gains, losses = [], []
        for i in range(1, len(closes)):
            d = closes[i] - closes[i - 1]
            gains.append(max(d, 0))
            losses.append(max(-d, 0))
        if len(gains) < period:
            return None
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def _update_range(self, candle):
        """Update daily range based on current range_mode."""
        ts = candle[0]  # ms
        hour = (ts // 3600000) % 24  # UTC hour

        # Reset range at UTC 00:00
        day_start = (ts // 86400000) * 86400000
        if self.range_start_ts is not None and day_start > self.range_start_ts:
            self._finalize_range()
            self.range_candles = []
            self.range_ready = False

        if self.range_mode == "orb":
            # Collect first 24 candles (2 hours) of the day for range
            if not self.range_ready and ts >= day_start and ts < day_start + 7200000:
                self.range_candles.append(candle)
                self.range_start_ts = day_start
                if len(self.range_candles) >= 24:
                    self._finalize_range()
            elif not self.range_ready and ts >= day_start + 7200000:
                # Missed the window — backfill from what we have
                if self.range_candles:
                    self._finalize_range()
                else:
                    # No candles collected, mark ready with current candle as range
                    self.range_high = candle[2]
                    self.range_low = candle[3]
                    self.range_ready = True
                    self.range_start_ts = day_start
        elif self.range_mode == "prev_day":
            # Range is set from yesterday — handled in on_candle when we have enough data
            if not self.range_ready:
                self.range_candles.append(candle)
                if len(self.range_candles) >= 2:
                    # Use the candle before last as "yesterday's" reference
                    self.range_high = max(c[2] for c in self.range_candles[:-1])
                    self.range_low = min(c[3] for c in self.range_candles[:-1])
                    self.range_ready = True
                    self.range_start_ts = day_start

    def _finalize_range(self):
        """Set range from collected candles."""
        if not self.range_candles:
            return
        self.range_high = max(c[2] for c in self.range_candles)
        self.range_low = min(c[3] for c in self.range_candles)
        self.range_ready = True
```

- [ ] **Step 3: Add entry/exit signal logic**

```python
    def on_candle(self, candle):
        """Process a new 5m candle. candle = [ts_ms, open, high, low, close, volume]."""
        self._candle_buffer.append(candle)
        self._update_range(candle)

        if not self.range_ready or not self.enabled:
            return None

        ts, o, h, l, c, vol = candle
        atr = self._compute_atr(list(self._candle_buffer))
        rsi = self._compute_rsi(list(self._candle_buffer))

        # If in position — check exits
        if self.position:
            return self._check_exit(candle, atr)

        # If no position — check entry
        if ts < self.cooldown_until:
            return None

        range_height = self.range_high - self.range_low
        if range_height <= 0:
            return None

        # Long breakout: close above range_high
        if c > self.range_high:
            return self._enter("long", c, ts, vol, atr, rsi, range_height)

        # Short breakout: close below range_low
        if c < self.range_low:
            return self._enter("short", c, ts, vol, atr, rsi, range_height)

        return None

    def _enter(self, direction, price, ts, vol, atr, rsi, range_height):
        """Open a new position."""
        qty = self.balance / price
        self.position = {
            "direction": direction,
            "entry_price": price,
            "entry_ts": ts,
            "qty": qty,
            "leg1_done": False,
            "leg1_qty": qty / 2,
            "trail_stop": None,
            "best_price": price,
            "atr": atr or 0,
            "range_high": self.range_high,
            "range_low": self.range_low,
            "range_height": range_height,
            "volume_at_entry": vol,
            "rsi_at_entry": rsi or 0,
            "candles_held": 0,
        }
        return {"type": "entry", "direction": direction, "price": price, "ts": ts}

    def _check_exit(self, candle, atr):
        """Check TP1, trail stop, and hard SL."""
        ts, o, h, l, c, vol = candle
        pos = self.position
        pos["candles_held"] += 1

        direction = pos["direction"]
        entry = pos["entry_price"]
        rh = pos["range_height"]

        # Update best price for trailing
        if direction == "long":
            pos["best_price"] = max(pos["best_price"], c)
        else:
            pos["best_price"] = min(pos["best_price"], c)

        # TP1: 1.5x range height (50% of position)
        if not pos["leg1_done"]:
            if direction == "long":
                tp1 = entry + rh * 1.5
                if c >= tp1:
                    return self._close_leg1(tp1, ts)
            else:
                tp1 = entry - rh * 1.5
                if c <= tp1:
                    return self._close_leg1(tp1, ts)

        # Trail stop: 2x ATR from best price (remaining 50%)
        if atr and atr > 0:
            if direction == "long":
                trail = pos["best_price"] - 2 * atr
                pos["trail_stop"] = max(pos["trail_stop"] or 0, trail)
                if c <= pos["trail_stop"]:
                    return self._close_leg2(c, ts, "trail_stop")
            else:
                trail = pos["best_price"] + 2 * atr
                pos["trail_stop"] = min(pos["trail_stop"] or float("inf"), trail)
                if c >= pos["trail_stop"]:
                    return self._close_leg2(c, ts, "trail_stop")

        # Hard SL: opposite side of range
        if direction == "long" and l <= pos["range_low"]:
            return self._close_leg2(pos["range_low"], ts, "stop_loss")
        if direction == "short" and h >= pos["range_high"]:
            return self._close_leg2(pos["range_high"], ts, "stop_loss")

        return None

    def _close_leg1(self, price, ts):
        """Close 50% at TP1."""
        pos = self.position
        pnl = (price - pos["entry_price"]) * pos["leg1_qty"]
        if pos["direction"] == "short":
            pnl = -pnl
        self.balance += pnl
        pos["leg1_done"] = True
        pos["leg1_exit_price"] = price
        pos["leg1_exit_ts"] = ts
        pos["leg1_pnl"] = round(pnl, 4)
        return {"type": "tp1", "price": price, "ts": ts, "pnl": round(pnl, 4)}

    def _close_leg2(self, price, ts, reason):
        """Close remaining 50% at trail/SL."""
        pos = self.position
        remaining_qty = pos["qty"] - pos["leg1_qty"]
        pnl = (price - pos["entry_price"]) * remaining_qty
        if pos["direction"] == "short":
            pnl = -pnl
        self.balance += pnl

        # Streak tracking
        total_pnl_so_far = pos.get("leg1_pnl", 0) + pnl
        if total_pnl_so_far > 0:
            self._consecutive_wins = getattr(self, "_consecutive_wins", 0) + 1
            self._consecutive_losses = 0
        else:
            self._consecutive_losses = getattr(self, "_consecutive_losses", 0) + 1
            self._consecutive_wins = 0

        # Build trade record
        trade = {
            "id": f"{self.asset.lower()}_{pos['entry_ts']}_{len(self.trades)}",
            "asset": self.asset,
            "direction": pos["direction"],
            "entry_ts": pos["entry_ts"],
            "entry_price": pos["entry_price"],
            "range_high": pos["range_high"],
            "range_low": pos["range_low"],
            "range_height": pos["range_height"],
            "range_mode": self.range_mode,
            "atr_at_entry": round(pos["atr"], 4),
            "qty": round(pos["qty"], 8),
            "leg1_exit_ts": pos.get("leg1_exit_ts"),
            "leg1_exit_price": pos.get("leg1_exit_price"),
            "leg1_pnl": pos.get("leg1_pnl", 0),
            "leg2_exit_ts": ts,
            "leg2_exit_price": price,
            "leg2_pnl": round(pnl, 4),
            "total_pnl": round(total_pnl_so_far, 4),
            "pnl_pct": round(total_pnl_so_far / self.starting_balance * 100, 4),
            "exit_reason": reason,
            "candles_held": pos["candles_held"],
            "volume_at_entry": pos["volume_at_entry"],
            "rsi_at_entry": round(pos["rsi_at_entry"], 2),
            "price_vs_range": round((pos["entry_price"] - pos["range_low"]) / max(pos["range_height"], 0.0001), 4),
            "hour_bucket": (pos["entry_ts"] // 14400000) % 6,
            "consecutive_wins": getattr(self, "_consecutive_wins", 0),
            "consecutive_losses": getattr(self, "_consecutive_losses", 0),
        }
        self.trades.append(trade)
        self.cooldown_until = ts + 900000  # 15 min cooldown

        result = {"type": reason, "price": price, "ts": ts, "pnl": round(pnl, 4), "trade": trade}
        self.position = None
        return result
```

- [ ] **Step 4: Add range mode toggle**

```python
    def set_range_mode(self, mode):
        """Switch between 'orb' and 'prev_day'."""
        if mode in ("orb", "prev_day"):
            self.range_mode = mode
            self.range_ready = False
            self.range_candles = []
            self.range_high = None
            self.range_low = None
```

- [ ] **Step 5: Test the class in isolation**

```bash
python -c "
from paper_trader import PaperTrader
pt = PaperTrader('ETH', 100.0)
# Simulate candles
import time
now = int(time.time() * 1000)
# 24 range candles
for i in range(25):
    c = [now + i*300000, 3200+i*0.5, 3210+i*0.5, 3190+i*0.5, 3205+i*0.5, 100]
    r = pt.on_candle(c)
    print(f'candle {i}: range_ready={pt.range_ready} pos={pt.position is not None}')
print(f'balance: {pt.balance}')
print(f'state: {pt.state_dict()[\"range_ready\"]}')
"
```

Expected: Range builds over 24 candles, breakout triggers at candle 25, position opens.

- [ ] **Step 6: Commit**

```bash
git add paper_trader.py
git commit -m "feat: PaperTrader class with daily range breakout strategy"
```

---

## Task 2: Persistent JSON log

**Files:**
- Modify: `paper_trader.py`

**Interfaces:**
- Consumes: `self.trades` list from Task 1
- Produces: `paper_trades.json` file with trade records and stats

- [ ] **Step 1: Add JSON save/load methods to PaperTrader**

Add to `paper_trader.py`:

```python
    PAPER_LOG = "paper_trades.json"

    def save(self):
        """Append trade log to persistent JSON file."""
        data = {}
        if os.path.exists(self.PAPER_LOG):
            try:
                with open(self.PAPER_LOG, "r") as f:
                    data = json.load(f)
            except Exception:
                data = {}

        key = self.asset.lower()
        if "meta" not in data:
            data["meta"] = {
                "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "version": 1,
                "strategy": "daily_range_breakout",
                "timeframe": "5m",
            }
        if key not in data:
            data[key] = {"balance": self.balance, "starting_balance": self.starting_balance, "trades": [], "stats": {}}

        data[key]["balance"] = round(self.balance, 4)
        data[key]["trades"] = self.trades
        data[key]["stats"] = self._compute_stats()

        with open(self.PAPER_LOG, "w") as f:
            json.dump(data, f, indent=2)

    @classmethod
    def load(cls, asset, starting_balance=100.0):
        """Load existing state from JSON file."""
        pt = cls(asset, starting_balance)
        if not os.path.exists(cls.PAPER_LOG):
            return pt
        try:
            with open(cls.PAPER_LOG, "r") as f:
                data = json.load(f)
            key = asset.lower()
            if key in data:
                pt.balance = data[key].get("balance", starting_balance)
                pt.trades = data[key].get("trades", [])
        except Exception:
            pass
        return pt
```

- [ ] **Step 2: Call save() after each trade close**

In `_close_leg2`, after appending the trade, add:

```python
        self.save()
```

- [ ] **Step 3: Test persistence**

```bash
python -c "
from paper_trader import PaperTrader
import os, time
if os.path.exists('paper_trades.json'): os.remove('paper_trades.json')
pt = PaperTrader('ETH', 100.0)
now = int(time.time() * 1000)
for i in range(30):
    c = [now + i*300000, 3200+i, 3210+i, 3190+i, 3205+i, 100]
    pt.on_candle(c)
pt.save()
# Reload
pt2 = PaperTrader.load('ETH', 100.0)
print(f'loaded balance: {pt2.balance}')
print(f'loaded trades: {len(pt2.trades)}')
import json
with open('paper_trades.json') as f:
    d = json.load(f)
print(f'json keys: {list(d.keys())}')
print(f'eth trades: {len(d[\"eth\"][\"trades\"])}')
"
```

Expected: `paper_trades.json` created, reload shows same balance and trades.

- [ ] **Step 4: Commit**

```bash
git add paper_trader.py
git commit -m "feat: persistent JSON trade log with save/load"
```

---

## Task 3: Integrate PaperTrader into Dashboard backend

**Files:**
- Modify: `dashboard.py` (lines ~690-779 for `__init__`, ~1327-1377 for `snapshot`, ~4039-4048 for `ticker`)
- Create: background candle feed loop

**Interfaces:**
- Consumes: `PaperTrader` from Task 1/2, existing `_fetch_klines()` at line 305
- Produces: `self.state["paper_eth"]` and `self.state["paper_sol"]` dicts available to WebSocket

- [ ] **Step 1: Add import and initialize PaperTraders in `__init__`**

At the top of `dashboard.py`, add import (near line 1):

```python
from paper_trader import PaperTrader
```

Inside `Dashboard.__init__()`, after line 778 (`self.state["sol"] = ...`), add:

```python
            "paper_eth": PaperTrader.load("ETH").state_dict(),
            "paper_sol": PaperTrader.load("SOL").state_dict(),
```

Also store the live instances (after the state dict, around line 780):

```python
        self._paper_eth = PaperTrader.load("ETH")
        self._paper_sol = PaperTrader.load("SOL")
```

- [ ] **Step 2: Add paper state to `snapshot()`**

In `snapshot()` at line ~1376 (before `return out`), add:

```python
        out["paper_eth"] = self._paper_eth.state_dict()
        out["paper_sol"] = self._paper_sol.state_dict()
```

- [ ] **Step 3: Create background candle feed loop**

Add a new method to the Dashboard class:

```python
    async def paper_candle_loop(self):
        """Feed 5m candles to paper traders every 15 seconds."""
        while True:
            await asyncio.sleep(15)
            for asset, symbol, trader in [
                ("ETH", "ETHUSDT", self._paper_eth),
                ("SOL", "SOLUSDT", self._paper_sol),
            ]:
                if not trader.enabled:
                    continue
                try:
                    data = await asyncio.wait_for(
                        asyncio.to_thread(_fetch_klines, symbol, "5m", 5),
                        timeout=10)
                    if data:
                        for candle in data:
                            trader.on_candle(candle)
                except Exception:
                    pass
```

- [ ] **Step 4: Launch the loop in `startup()`**

In `startup()` at line ~4085 (after `asyncio.create_task(dash.ticker())`), add:

```python
        asyncio.create_task(dash.paper_candle_loop())
```

- [ ] **Step 5: Add control endpoint for range mode toggle and paper ON/OFF**

Add a new HTTP handler method:

```python
    async def paper_control(self, request):
        """Handle paper bot control: range mode toggle, enable/disable."""
        try:
            body = await request.json()
        except Exception:
            return aiohttp.web.json_response({"error": "bad json"}, status=400)
        asset = (body.get("asset") or "").upper()
        trader = self._paper_eth if asset == "ETH" else self._paper_sol if asset == "SOL" else None
        if not trader:
            return aiohttp.web.json_response({"error": "bad asset"}, status=400)
        if "range_mode" in body:
            trader.set_range_mode(body["range_mode"])
        if "enabled" in body:
            trader.enabled = bool(body["enabled"])
        return aiohttp.web.json_response(trader.state_dict())
```

Register the route in `startup()` (near where other routes are added, around line 4075):

```python
        app.router.add_post("/api/paper/control", dash.paper_control)
```

- [ ] **Step 6: Restart and verify state appears in API**

```bash
python dashboard.py --broadcast &
sleep 20
curl -s http://127.0.0.1:8080/api/state | python -c "import sys,json; d=json.load(sys.stdin); print('paper_eth:', json.dumps(d.get('paper_eth',{}), indent=2)[:500])"
```

Expected: `paper_eth` key present with `balance`, `range_ready`, `position`, `stats` fields.

- [ ] **Step 7: Commit**

```bash
git add dashboard.py
git commit -m "feat: integrate PaperTrader into dashboard — candle loop, state, control endpoint"
```

---

## Task 4: Frontend — Range lines on charts

**Files:**
- Modify: `static/app.js` (lines ~3247 for ethSeries/solSeries, ~3265 for initCandles, ~3409 for initSolCandles, ~3300 for loadKlines, ~3439 for loadSolKlines)

**Interfaces:**
- Consumes: `paper_eth` and `paper_sol` from WebSocket state (Task 3)
- Produces: Two `LineSeries` per chart (range_high, range_low) rendered on Lightweight Charts

- [ ] **Step 1: Declare range line series variables**

Near line 3247 (where `ethChart`/`ethSeries` are declared), add:

```js
    let ethRangeHigh = null, ethRangeLow = null;
```

Near line 3366 (where `solChart`/`solSeries` are declared), add:

```js
    let solRangeHigh = null, solRangeLow = null;
```

- [ ] **Step 2: Create range line series in `initCandles()`**

Inside `initCandles()`, after `ethSeries = ethChart.addCandlestickSeries(candleStyle);` (line 3278), add:

```js
    ethRangeHigh = ethChart.addLineSeries({
      color: "#22d3ee", lineWidth: 1, lineStyle: 2,
      priceLineVisible: false, lastValueVisible: false,
      crosshairMarkerVisible: false,
    });
    ethRangeLow = ethChart.addLineSeries({
      color: "#f59e0b", lineWidth: 1, lineStyle: 2,
      priceLineVisible: false, lastValueVisible: false,
      crosshairMarkerVisible: false,
    });
```

- [ ] **Step 3: Create range line series in `initSolCandles()`**

Inside `initSolCandles()`, after `solSeries = solChart.addCandlestickSeries(candleStyle);` (line 3422), add:

```js
    solRangeHigh = solChart.addLineSeries({
      color: "#22d3ee", lineWidth: 1, lineStyle: 2,
      priceLineVisible: false, lastValueVisible: false,
      crosshairMarkerVisible: false,
    });
    solRangeLow = solChart.addLineSeries({
      color: "#f59e0b", lineWidth: 1, lineStyle: 2,
      priceLineVisible: false, lastValueVisible: false,
      crosshairMarkerVisible: false,
    });
```

- [ ] **Step 4: Add helper function to update range lines**

Add this function near the chart code (after `initCandles`):

```js
    const updateRangeLines = (chart, hiSeries, loSeries, paper) => {
      if (!chart || !hiSeries || !loSeries || !paper || !paper.range_ready) {
        if (hiSeries) hiSeries.setData([]);
        if (loSeries) loSeries.setData([]);
        return;
      }
      const rh = paper.range_high;
      const rl = paper.range_low;
      const start = paper.range_start_ts ? Math.floor(paper.range_start_ts / 1000) : 0;
      const end = Math.floor(Date.now() / 1000);
      const pts = [{ time: start, value: rh }, { time: end, value: rh }];
      const loPts = [{ time: start, value: rl }, { time: end, value: rl }];
      hiSeries.setData(pts);
      loSeries.setData(loPts);
    };
```

- [ ] **Step 5: Call updateRangeLines from render/renderSol**

In the `render(s)` function (line ~3715), add at the end:

```js
    updateRangeLines(ethChart, ethRangeHigh, ethRangeLow, s.paper_eth);
```

In the `renderSol(s)` function (line ~1408), add at the end:

```js
    const sol = s.sol || {};
    updateRangeLines(solChart, solRangeHigh, solRangeLow, s.paper_sol);
```

- [ ] **Step 6: Test — restart dashboard, open browser, verify range lines appear**

```bash
Stop-Process -Name python -Force -ErrorAction SilentlyContinue
Start-Sleep 2
Start-Process python -ArgumentList "dashboard.py --broadcast" -WindowStyle Hidden
Start-Sleep 20
```

Open `http://127.0.0.1:8080`, switch to 5m timeframe on ETH chart. Verify:
- Two horizontal dashed lines appear (cyan high, amber low)
- Lines span from range start to right edge
- If `range_ready` is false, no lines appear

- [ ] **Step 7: Commit**

```bash
git add static/app.js
git commit -m "feat: range lines on ETH and SOL candlestick charts"
```

---

## Task 5: Frontend — Trade markers on candles

**Files:**
- Modify: `static/app.js`

**Interfaces:**
- Consumes: `paper_eth.recent_trades` and `paper_sol.recent_trades` from state
- Produces: `.setMarkers()` calls on `ethSeries` and `solSeries`

- [ ] **Step 1: Add marker builder function**

```js
    const buildTradeMarkers = (trades) => {
      if (!trades || !trades.length) return [];
      const markers = [];
      for (const t of trades) {
        if (t.entry_ts) {
          markers.push({
            time: Math.floor(t.entry_ts / 1000),
            position: t.direction === "long" ? "belowBar" : "aboveBar",
            color: t.direction === "long" ? "#22c55e" : "#ef4444",
            shape: t.direction === "long" ? "arrowUp" : "arrowDown",
            text: `${t.direction.toUpperCase()} @ $${fmt.num(t.entry_price, 2)}`,
          });
        }
        if (t.leg1_exit_price && t.leg1_exit_ts) {
          markers.push({
            time: Math.floor(t.leg1_exit_ts / 1000),
            position: "aboveBar",
            color: "#22d3ee",
            shape: "circle",
            text: `TP1 ${t.leg1_pnl > 0 ? "+" : ""}$${fmt.num(t.leg1_pnl, 2)}`,
          });
        }
        if (t.leg2_exit_price && t.leg2_exit_ts) {
          markers.push({
            time: Math.floor(t.leg2_exit_ts / 1000),
            position: "aboveBar",
            color: t.exit_reason === "stop_loss" ? "#ef4444" : "#22d3ee",
            shape: "cross",
            text: `${t.exit_reason === "stop_loss" ? "SL" : "TRAIL"} ${t.leg2_pnl > 0 ? "+" : ""}$${fmt.num(t.leg2_pnl, 2)}`,
          });
        }
      }
      markers.sort((a, b) => a.time - b.time);
      return markers;
    };
```

- [ ] **Step 2: Call setMarkers from render/renderSol**

In `render(s)` (line ~3715), add before the range lines update:

```js
    if (ethSeries) ethSeries.setMarkers(buildTradeMarkers(s.paper_eth && s.paper_eth.recent_trades));
```

In `renderSol(s)` (line ~1408), add:

```js
    if (solSeries) solSeries.setMarkers(buildTradeMarkers(s.paper_sol && s.paper_sol.recent_trades));
```

- [ ] **Step 3: Test — verify markers appear on chart after trades execute**

Wait for the bot to take a trade (or simulate by watching the 5m candles). Verify:
- Green up-arrow below candle at entry
- Cyan dot at TP1
- Cyan cross or red cross at final exit

- [ ] **Step 4: Commit**

```bash
git add static/app.js
git commit -m "feat: trade entry/exit markers on candlestick charts"
```

---

## Task 6: Frontend — PnL panel (HTML + JS)

**Files:**
- Modify: `static/index.html` (lines ~600 for ETH card, ~1235 for SOL card)
- Modify: `static/app.js`

**Interfaces:**
- Consumes: `paper_eth` and `paper_sol` from state
- Produces: DOM elements showing balance, PnL, win rate, open position

- [ ] **Step 1: Add PnL panel HTML to ETH Market Context card**

After the `mc-reserves` div (line ~606) and before the closing `</div>` of `#card-prices`, add:

```html
      <div class="paper-panel" id="eth-paper-panel">
        <div class="paper-head">
          <h3 class="watch">Paper Bot <span class="tag" id="eth-paper-status">loading</span></h3>
          <div class="paper-toggles">
            <button type="button" class="paper-toggle" id="eth-range-mode" data-mode="orb">ORB (2h)</button>
            <button type="button" class="paper-toggle" id="eth-range-mode-pd" data-mode="prev_day">Prev Day</button>
            <button type="button" class="paper-toggle on" id="eth-paper-on">Paper ON</button>
          </div>
        </div>
        <div class="paper-stats" id="eth-paper-stats">
          <div class="paper-stat"><span class="paper-stat-label">balance</span><span class="paper-stat-val" id="eth-paper-bal">$100.00</span></div>
          <div class="paper-stat"><span class="paper-stat-label">PnL</span><span class="paper-stat-val" id="eth-paper-pnl">$0.00</span></div>
          <div class="paper-stat"><span class="paper-stat-label">W/L</span><span class="paper-stat-val" id="eth-paper-wl">0 / 0</span></div>
          <div class="paper-stat"><span class="paper-stat-label">win%</span><span class="paper-stat-val" id="eth-paper-wr">--</span></div>
          <div class="paper-stat"><span class="paper-stat-label">trades</span><span class="paper-stat-val" id="eth-paper-count">0</span></div>
        </div>
        <div class="paper-open" id="eth-paper-open" style="display:none">
          <span class="paper-open-label">open:</span> <span id="eth-paper-pos">--</span>
        </div>
      </div>
```

- [ ] **Step 2: Add PnL panel HTML to SOL Market Context card**

After the `sol-reserves` div (line ~1235) and before the closing `</div>` of `#sol-card-prices`, add:

```html
      <div class="paper-panel" id="sol-paper-panel">
        <div class="paper-head">
          <h3 class="watch">Paper Bot <span class="tag" id="sol-paper-status">loading</span></h3>
          <div class="paper-toggles">
            <button type="button" class="paper-toggle" id="sol-range-mode" data-mode="orb">ORB (2h)</button>
            <button type="button" class="paper-toggle" id="sol-range-mode-pd" data-mode="prev_day">Prev Day</button>
            <button type="button" class="paper-toggle on" id="sol-paper-on">Paper ON</button>
          </div>
        </div>
        <div class="paper-stats" id="sol-paper-stats">
          <div class="paper-stat"><span class="paper-stat-label">balance</span><span class="paper-stat-val" id="sol-paper-bal">$100.00</span></div>
          <div class="paper-stat"><span class="paper-stat-label">PnL</span><span class="paper-stat-val" id="sol-paper-pnl">$0.00</span></div>
          <div class="paper-stat"><span class="paper-stat-label">W/L</span><span class="paper-stat-val" id="sol-paper-wl">0 / 0</span></div>
          <div class="paper-stat"><span class="paper-stat-label">win%</span><span class="paper-stat-val" id="sol-paper-wr">--</span></div>
          <div class="paper-stat"><span class="paper-stat-label">trades</span><span class="paper-stat-val" id="sol-paper-count">0</span></div>
        </div>
        <div class="paper-open" id="sol-paper-open" style="display:none">
          <span class="paper-open-label">open:</span> <span id="sol-paper-pos">--</span>
        </div>
      </div>
```

- [ ] **Step 3: Add PnL render function in app.js**

```js
    const renderPaperPanel = (prefix, paper) => {
      if (!paper) return;
      const stats = paper.stats || {};
      const bal = $(prefix + "-paper-bal");
      const pnl = $(prefix + "-paper-pnl");
      const wl = $(prefix + "-paper-wl");
      const wr = $(prefix + "-paper-wr");
      const count = $(prefix + "-paper-count");
      const pos = $(prefix + "-paper-pos");
      const open = $(prefix + "-paper-open");
      const status = $(prefix + "-paper-status");

      if (bal) bal.textContent = "$" + fmt.num(paper.balance, 2);
      if (pnl) {
        const v = stats.pnl || 0;
        pnl.textContent = `${v >= 0 ? "+" : ""}$${fmt.num(v, 2)} (${fmt.num(stats.pnl_pct || 0, 1)}%)`;
        pnl.style.color = v > 0 ? "var(--green)" : v < 0 ? "var(--red)" : "var(--dim)";
      }
      if (wl) wl.textContent = `${stats.wins || 0} / ${stats.losses || 0}`;
      if (wr) wr.textContent = stats.win_rate ? stats.win_rate + "%" : "--";
      if (count) count.textContent = stats.total_trades || 0;

      if (paper.position) {
        const p = paper.position;
        if (pos) pos.textContent = `${p.direction.toUpperCase()} ${fmt.num(p.qty, 4)} ${paper.asset} @ $${fmt.num(p.entry_price, 2)} | TP1 $${fmt.num(p.entry_price + (p.direction === "long" ? 1 : -1) * p.range_height * 1.5, 2)} trail $${fmt.num(p.trail_stop || 0, 2)}`;
        if (open) open.style.display = "block";
      } else {
        if (open) open.style.display = "none";
      }

      if (status) {
        status.textContent = paper.enabled ? (paper.range_ready ? "active" : "building range") : "paused";
        status.style.color = paper.enabled ? "var(--green)" : "var(--dim)";
      }
    };
```

- [ ] **Step 4: Call renderPaperPanel from render/renderSol**

In `render(s)` (line ~3715), add:

```js
    renderPaperPanel("eth", s.paper_eth);
```

In `renderSol(s)` (line ~1408), add:

```js
    renderPaperPanel("sol", s.paper_sol);
```

- [ ] **Step 5: Test — verify PnL panel shows balance and updates**

Restart dashboard, open browser. Verify:
- "Paper Bot" section appears below reserves
- Shows $100.00 balance, 0/0 W/L
- Status shows "building range" then "active" after 2 hours (or immediately in prev_day mode)

- [ ] **Step 6: Commit**

```bash
git add static/index.html static/app.js
git commit -m "feat: PnL panel for paper bot on ETH and SOL cards"
```

---

## Task 7: Frontend — Toggle controls + API wiring

**Files:**
- Modify: `static/app.js`

**Interfaces:**
- Consumes: DOM elements from Task 6 (toggle buttons)
- Produces: `POST /api/paper/control` calls to backend

- [ ] **Step 1: Add control functions**

```js
    const postPaperControl = async (asset, body) => {
      try {
        await fetch("/api/paper/control", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ asset, ...body }),
        });
      } catch (e) { console.error("paper control failed", e); }
    };
```

- [ ] **Step 2: Bind ETH toggle click handlers**

Add after the `renderPaperPanel` function:

```js
    // ETH paper toggles
    const ethRmOrb = $("eth-range-mode");
    const ethRmPd = $("eth-range-mode-pd");
    const ethPaperOn = $("eth-paper-on");
    if (ethRmOrb) ethRmOrb.addEventListener("click", () => {
      postPaperControl("ETH", { range_mode: "orb" });
      ethRmOrb.classList.add("on");
      ethRmPd && ethRmPd.classList.remove("on");
    });
    if (ethRmPd) ethRmPd.addEventListener("click", () => {
      postPaperControl("ETH", { range_mode: "prev_day" });
      ethRmPd.classList.add("on");
      ethRmOrb && ethRmOrb.classList.remove("on");
    });
    if (ethPaperOn) ethPaperOn.addEventListener("click", () => {
      const isOn = ethPaperOn.classList.contains("on");
      postPaperControl("ETH", { enabled: !isOn });
      ethPaperOn.classList.toggle("on");
      ethPaperOn.textContent = isOn ? "Paper OFF" : "Paper ON";
    });

    // SOL paper toggles
    const solRmOrb = $("sol-range-mode");
    const solRmPd = $("sol-range-mode-pd");
    const solPaperOn = $("sol-paper-on");
    if (solRmOrb) solRmOrb.addEventListener("click", () => {
      postPaperControl("SOL", { range_mode: "orb" });
      solRmOrb.classList.add("on");
      solRmPd && solRmPd.classList.remove("on");
    });
    if (solRmPd) solRmPd.addEventListener("click", () => {
      postPaperControl("SOL", { range_mode: "prev_day" });
      solRmPd.classList.add("on");
      solRmOrb && solRmOrb.classList.remove("on");
    });
    if (solPaperOn) solPaperOn.addEventListener("click", () => {
      const isOn = solPaperOn.classList.contains("on");
      postPaperControl("SOL", { enabled: !isOn });
      solPaperOn.classList.toggle("on");
      solPaperOn.textContent = isOn ? "Paper OFF" : "Paper ON";
    });
```

- [ ] **Step 3: Test — click toggles, verify backend responds**

Open browser DevTools network tab. Click "Prev Day" button. Verify:
- `POST /api/paper/control` fires with `{"asset":"ETH","range_mode":"prev_day"}`
- Response shows updated state with `range_mode: "prev_day"`
- Range lines on chart update

Click "Paper OFF". Verify:
- `POST /api/paper/control` fires with `{"asset":"ETH","enabled":false}`
- Status tag changes to "paused"
- No new trades taken

- [ ] **Step 4: Commit**

```bash
git add static/app.js
git commit -m "feat: paper bot range mode and on/off toggle controls"
```

---

## Task 8: CSS styling

**Files:**
- Modify: `static/style.css`

**Interfaces:**
- Consumes: DOM classes from Tasks 4-7 (`.paper-panel`, `.paper-stats`, `.paper-toggle`, etc.)
- Produces: Styled elements matching the dashboard's dark theme

- [ ] **Step 1: Add paper panel styles**

Add at the end of `style.css`:

```css
/* Paper trading bot panel */
.paper-panel {
  margin-top: 10px; padding: 10px 12px;
  border: 1px solid var(--line); border-radius: 10px;
  background: var(--panel2);
}
.paper-head {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 8px;
}
.paper-toggles { display: flex; gap: 4px; }
.paper-toggle {
  font-size: 10px; padding: 3px 8px; border-radius: 6px;
  border: 1px solid var(--line); background: transparent;
  color: var(--dim); cursor: pointer; transition: all .15s;
}
.paper-toggle:hover { border-color: var(--cyan); color: var(--cyan); }
.paper-toggle.on {
  color: var(--cyan); border-color: rgba(34,211,238,.45);
  background: rgba(34,211,238,.08);
}
.paper-stats {
  display: flex; flex-wrap: wrap; gap: 14px;
}
.paper-stat { min-width: 60px; }
.paper-stat-label {
  display: block; font-size: 9px; text-transform: uppercase;
  letter-spacing: .5px; color: var(--dim); margin-bottom: 2px;
}
.paper-stat-val {
  font-size: 13px; font-weight: 600; color: var(--text);
  font-family: 'JetBrains Mono', monospace;
}
.paper-open {
  margin-top: 6px; padding: 5px 8px;
  border-radius: 6px; background: rgba(34,211,238,.06);
  border: 1px solid rgba(34,211,238,.15);
  font-size: 11px; color: var(--text);
  font-family: 'JetBrains Mono', monospace;
}
.paper-open-label { color: var(--dim); font-size: 10px; }
```

- [ ] **Step 2: Test — verify all elements are styled correctly**

Restart dashboard, check:
- Paper panel has dark background with border
- Toggle buttons have hover effect and active state
- Stats are monospace, properly spaced
- Open position bar has subtle cyan background

- [ ] **Step 3: Commit**

```bash
git add static/style.css
git commit -m "feat: paper bot panel styling"
```

---

## Task 9: Integration test and verify

**Files:**
- All files from Tasks 1-8

**Interfaces:**
- Full system test — end-to-end paper trading flow

- [ ] **Step 1: Restart dashboard and verify all components**

```bash
Stop-Process -Name python -Force -ErrorAction SilentlyContinue
Start-Sleep 2
Start-Process python -ArgumentList "dashboard.py --broadcast" -WindowStyle Hidden
Start-Sleep 20
```

- [ ] **Step 2: Verify backend state via API**

```bash
curl -s http://127.0.0.1:8080/api/state | python -c "
import sys, json
d = json.load(sys.stdin)
pe = d.get('paper_eth', {})
ps = d.get('paper_sol', {})
print('ETH balance:', pe.get('balance'))
print('ETH range_ready:', pe.get('range_ready'))
print('ETH range_high:', pe.get('range_high'))
print('ETH position:', pe.get('position'))
print('SOL balance:', ps.get('balance'))
print('SOL range_ready:', ps.get('range_ready'))
"
```

Expected: Both assets show balance ~100, range_ready True (after 2h in ORB mode, or immediately in prev_day mode).

- [ ] **Step 3: Test control endpoint**

```bash
curl -s -X POST http://127.0.0.1:8080/api/paper/control -H "Content-Type: application/json" -d '{"asset":"ETH","range_mode":"prev_day"}' | python -c "
import sys, json
d = json.load(sys.stdin)
print('range_mode:', d.get('range_mode'))
print('range_ready:', d.get('range_ready'))
"
```

Expected: `range_mode: prev_day`, `range_ready: True`.

- [ ] **Step 4: Verify frontend renders correctly**

Open `http://127.0.0.1:8080` in browser:
- ETH chart shows range lines (cyan + amber dashed)
- SOL chart shows range lines
- Paper Bot panel shows balance, PnL, W/L
- Toggle buttons work (ORB / Prev Day / Paper ON/OFF)
- No console errors

- [ ] **Step 5: Verify paper_trades.json exists and is valid**

```bash
python -c "
import json
with open('paper_trades.json') as f:
    d = json.load(f)
print('meta:', d.get('meta'))
print('eth balance:', d.get('eth', {}).get('balance'))
print('sol balance:', d.get('sol', {}).get('balance'))
print('eth trades:', len(d.get('eth', {}).get('trades', [])))
print('sol trades:', len(d.get('sol', {}).get('trades', [])))
"
```

Expected: Valid JSON with meta, eth, sol keys. Trades array may be empty initially.

- [ ] **Step 6: Final commit**

```bash
git add -A
git commit -m "feat: paper trading bot — daily range breakout strategy with ML-ready logs"
```
