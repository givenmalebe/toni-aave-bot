"""Paper trading bot — Daily Range Breakout strategy on 5m candles."""
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

        # Streak tracking
        self._consecutive_wins = 0
        self._consecutive_losses = 0

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
                pos["trail_stop"] = max(pos["trail_stop"] if pos["trail_stop"] is not None else 0, trail)
                if c <= pos["trail_stop"]:
                    return self._close_leg2(c, ts, "trail_stop")
            else:
                trail = pos["best_price"] + 2 * atr
                pos["trail_stop"] = min(pos["trail_stop"] if pos["trail_stop"] is not None else float("inf"), trail)
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
            self._consecutive_wins += 1
            self._consecutive_losses = 0
        else:
            self._consecutive_losses += 1
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
            "consecutive_wins": self._consecutive_wins,
            "consecutive_losses": self._consecutive_losses,
        }
        self.trades.append(trade)
        self.cooldown_until = ts + 900000  # 15 min cooldown

        result = {"type": reason, "price": price, "ts": ts, "pnl": round(pnl, 4), "trade": trade}
        self.position = None
        return result


    def set_range_mode(self, mode):
        """Switch between 'orb' and 'prev_day'."""
        if mode in ("orb", "prev_day"):
            self.range_mode = mode
            self.range_ready = False
            self.range_candles = []
            self.range_high = None
            self.range_low = None
