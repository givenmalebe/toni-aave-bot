# Paper Trading Bot — Daily Range Breakout Strategy

**Date:** 2026-08-19  
**Status:** Approved — ready for implementation  
**Asset scope:** ETH/USD and SOL/USD  
**Starting capital:** $100 per asset (paper)  

---

## 1. Goal

Build a paper trading bot that trades both ETH and SOL using the Daily Range Breakout strategy on 5-minute candles. The bot runs on real Binance market data, executes simulated trades, and logs every trade with ML-ready features — laying the groundwork for a future real-money deployment backed by machine learning.

---

## 2. Architecture

### 2.1 Backend: `PaperTrader` class (in `dashboard.py`)

One instance per asset (`PaperTrader("ETH")`, `PaperTrader("SOL")`). Each instance:

- Receives 5m candle arrays from the existing `/api/klines` fetch pipeline
- Manages its own state: balance, open position, daily range, trade history
- Computes entry/exit signals on every candle update
- Appends every trade to `paper_trades.json`
- Exposes state to the frontend via `self.state["paper_eth"]` / `self.state["paper_sol"]`

### 2.2 Frontend: Chart overlay + PnL panel

- Range lines rendered on the existing Lightweight Charts candlestick chart
- Trade entry/exit markers placed on candles
- Compact PnL strip below the chart
- Toggle controls for range mode and bot on/off

### 2.3 Persistent log: `paper_trades.json`

Append-only JSON file. Structure designed for future ML training data ingestion.

---

## 3. Strategy Rules

### 3.1 Range Definition

Two modes, user-toggleable via UI button:

**ORB mode (default — Opening Range Breakout):**
- At UTC 00:00, collect the first 24 × 5m candles (2 hours: 00:00–02:00 UTC)
- Range high = highest high across those 24 candles
- Range low = lowest low across those 24 candles
- Range height = range_high - range_low
- If dashboard starts mid-day, backfill from today's existing 5m candles up to 24
- Range resets at next UTC 00:00

**Prev Day mode:**
- Range high = yesterday's actual high (highest high of all 288 5m candles)
- Range low = yesterday's actual low (lowest low of all 288 5m candles)
- Available immediately at UTC 00:00 with no 2-hour wait

### 3.2 Entry Signals

- **Long entry:** 5m candle closes above range_high → buy at close price
- **Short entry:** 5m candle closes below range_low → sell at close price
- Only one position at a time per asset (no stacking)
- **Cooldown:** No new trade for 3 candles (15 minutes) after closing a position — prevents whipsaw re-entries

### 3.3 Position Sizing

- **All-in:** Use entire balance for each trade
- Qty = balance / entry_price (fractional shares — e.g. 0.0317 ETH)

### 3.4 Exit Rules — Scale-out (2 legs)

**Leg 1 (50% of position):**
- Long TP1 = entry_price + (range_height × 1.5)
- Short TP1 = entry_price - (range_height × 1.5)
- On hit: close 50% of position, record partial PnL, update balance

**Leg 2 (remaining 50%):**
- Trail with 2 × ATR(14) from best price since entry
- Long trail_stop = highest_close_since_entry - (2 × ATR14)
- Short trail_stop = lowest_close_since_entry + (2 × ATR14)
- Updates every candle. On hit: close remainder, record final PnL

**Emergency stop loss:**
- Hard SL at opposite side of range
- Long SL = range_low
- Short SL = range_high
- If hit before any TP, close full position at SL price

### 3.5 ATR Calculation

- ATR(14) computed from 5m candles
- Uses existing Binance kline data (high, low, close)
- Calculated on the last 14 candles at the moment of entry

---

## 4. Chart Visualization

### 4.1 Range Lines

- Two horizontal lines using `addLineSeries()`:
  - **Range High:** cyan dashed (`#22d3ee`, lineStyle: 2)
  - **Range Low:** amber dashed (`#f59e0b`, lineStyle: 2)
- Semi-transparent fill zone between the lines (shaded rectangle)
- Lines extend from range start time to right edge of chart
- Old range lines fade to 30% opacity when new range resets

### 4.2 Trade Markers

Using candlestick series `.setMarkers()` API:
- **Entry long:** green upward arrow (▲) below candle, text: "LONG @ $3,245"
- **Entry short:** red downward arrow (▼) above candle, text: "SHORT @ $3,180"
- **TP1 hit:** cyan diamond (◆) on candle, text: "TP1 +$2.28"
- **TP2 / trail close:** cyan cross (✕) on candle, text: "TRAIL +$3.03"
- **Stop loss:** red cross (✕) on candle, text: "SL -$4.12"

### 4.3 PnL Panel

Compact strip below the candlestick chart:

```
balance $98.23 | PnL $-1.77 (-1.8%) | W 3 L 2 | 60% | trades 5 | open: LONG 0.031 ETH @ $3,245 | TP1 $3,320 trail $3,195
```

- Color-coded: green positive, red negative
- Updates in real-time as candles arrive
- ETH and SOL each have their own PnL panel in their respective Market Context cards

### 4.4 UI Controls

Added to the Market Context card hero section:
- **Range mode toggle:** `[ORB (2h)] [Prev Day]` — next to existing TF buttons
- **Paper bot toggle:** `[Paper ON] / [Paper OFF]` — pauses trading without losing state
- When OFF: range lines still display, but no new trades are taken

---

## 5. Persistent Log — `paper_trades.json`

### 5.1 File Structure

```json
{
  "meta": {
    "started": "2026-08-19T00:00:00Z",
    "version": 1,
    "strategy": "daily_range_breakout",
    "timeframe": "5m"
  },
  "eth": {
    "balance": 98.23,
    "starting_balance": 100.00,
    "trades": [ ... ],
    "stats": {
      "total_trades": 5,
      "wins": 3,
      "losses": 2,
      "pnl": -1.77,
      "pnl_pct": -1.77,
      "max_drawdown": 3.2,
      "avg_win": 3.85,
      "avg_loss": -4.62,
      "win_rate": 0.60,
      "profit_factor": 1.25,
      "sharpe_approx": 0.42
    }
  },
  "sol": {
    "balance": 102.15,
    "starting_balance": 100.00,
    "trades": [ ... ],
    "stats": { ... }
  }
}
```

### 5.2 Per-Trade Record Schema

```json
{
  "id": "eth_1724006400_0",
  "asset": "ETH",
  "direction": "long",
  "entry_ts": 1724006400,
  "entry_price": 3245.50,
  "range_high": 3260.00,
  "range_low": 3210.00,
  "range_height": 50.00,
  "range_mode": "orb",
  "atr_at_entry": 28.5,
  "qty": 0.0308,
  "leg1_exit_ts": 1724007300,
  "leg1_exit_ts": 1724007300,
  "leg1_exit_price": 3320.50,
  "leg1_pnl": 2.28,
  "leg2_exit_ts": 1724008200,
  "leg2_exit_price": 3345.00,
  "leg2_pnl": 3.03,
  "total_pnl": 5.31,
  "pnl_pct": 5.31,
  "exit_reason": "trail_stop",
  "candles_held": 14,
  "market_regime": "trending_up",
  "volatility_regime": "expanding",
  "volume_at_entry": 1250,
  "rsi_at_entry": 58.2,
  "price_vs_range": 0.3,
  "hour_bucket": 2,
  "consecutive_wins": 2,
  "consecutive_losses": 0
}
```

### 5.3 ML Feature Definitions

| Feature | Type | Description |
|---------|------|-------------|
| `range_height` | float | Height of the daily range in USD |
| `range_mode` | string | "orb" or "prev_day" |
| `atr_at_entry` | float | ATR(14) at time of entry |
| `volume_at_entry` | float | Volume on entry candle |
| `rsi_at_entry` | float | RSI(14) at time of entry |
| `price_vs_range` | float | 0.0 = at range_low, 1.0 = at range_high (where entry happened) |
| `hour_bucket` | int | 0-5, which 4h window (0=00-04, 1=04-08, ...) |
| `market_regime` | string | "trending_up" / "trending_down" / "ranging" (from 1h 20/50 EMA) |
| `volatility_regime` | string | "expanding" / "normal" / "compressed" (ATR14 vs 50-period ATR avg) |
| `consecutive_wins` | int | Current win streak |
| `consecutive_losses` | int | Current loss streak |
| `candles_held` | int | How many 5m candles the position was open |
| `exit_reason` | string | "tp1" / "trail_stop" / "stop_loss" |

---

## 6. ML Readiness

The JSON schema is designed so a future ML pipeline can:

1. **Ingest** `paper_trades.json` → pandas DataFrame
2. **Feature columns** are pre-computed per trade (no feature engineering needed at training time)
3. **Labels:** `total_pnl`, `pnl_pct`, win/loss binary, risk-reward achieved
4. **Backward compatible:** Adding new fields later won't break existing records — use `trade.get("field", default)`

**Future models (not built now):**
- Signal filter: predict probability of profitable breakout → skip or trade
- Exit optimizer: predict optimal TP multiplier based on volatility regime
- Position sizing Kelly: given win rate and edge, compute optimal fraction

---

## 7. Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `dashboard.py` | Modify | Add `PaperTrader` class, integrate into `snapshot()`, `klines_api()` |
| `static/app.js` | Modify | Range lines, markers, PnL panel, toggle controls |
| `static/index.html` | Modify | New DOM elements for PnL strip, range toggle, paper toggle |
| `static/style.css` | Modify | Styles for range lines, markers, PnL panel |
| `paper_trades.json` | Create | Persistent trade log (auto-created on first trade) |

---

## 8. Constraints

- Paper trades only — no real money, no wallet interaction
- All fills at candle close price (no slippage modeled in v1)
- One position per asset at a time
- Bot runs only while dashboard server is running (no standalone process)
- Existing candlestick chart and all other dashboard features remain unchanged
- Range lines and markers overlay on top of existing candles without replacing them
