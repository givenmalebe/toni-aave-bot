"""SOL fee calibration + arm-immediately guardrails.

Live Jito tip-floor percentiles, adaptive tip choice for uncontested
long-tail liquidations, dynamic priority fees, and a rolling 24h loss
circuit breaker. $0 infra: public tip-floor API only.
"""
import os
import time
import collections

TIP_FLOOR_URL = os.environ.get(
    "JITO_TIP_FLOOR_URL",
    "https://bundles.jito.wtf/api/v1/tip_floor")

# Conservative default before first successful fetch (SOL lamports).
_DEFAULT_TIP_LAMPORTS = int(
    float(os.environ.get("SOL_JITO_TIP_FLOOR_DEFAULT_SOL", "0.0001") or 0.0001)
    * 1e9)

# Never spend more than this fraction of pre-tip profit on the tip.
TIP_SHARE_CAP = min(0.5, max(0.05, float(
    os.environ.get("SOL_TIP_SHARE_CAP", "0.30") or 0.30)))
# Absolute per-bundle cap (lamports).
TIP_ABS_CAP_LAMPORTS = int(float(
    os.environ.get("SOL_JITO_TIP_CAP_SOL", "0.005") or 0.005) * 1e9)
# Priority fee bounds (micro-lamports per CU).
PRIO_FEE_FLOOR = int(os.environ.get("SOL_PRIO_FEE_FLOOR", "1000") or 1000)
PRIO_FEE_CAP = int(os.environ.get("SOL_PRIO_FEE_CAP", "200000") or 200000)
# Circuit breaker: pause SOL execution at this much realized loss / 24h.
LOSS_PAUSE_LAMPORTS = int(float(
    os.environ.get("SOL_DAILY_LOSS_PAUSE_SOL", "0.05") or 0.05) * 1e9)
# Refuse sends below this bot-wallet float (rent + fee buffer).
FLOAT_FLOOR_SOL = float(os.environ.get("SOL_FLOAT_FLOOR", "0.2") or 0.2)


def percentile(sorted_vals, p):
    if not sorted_vals:
        return 0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * p
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    return int(sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f))


class TipFloor:
    """Rolling Jito tip percentiles in lamports."""

    def __init__(self):
        self.pcts = {"p25": _DEFAULT_TIP_LAMPORTS,
                     "p50": _DEFAULT_TIP_LAMPORTS,
                     "p75": _DEFAULT_TIP_LAMPORTS,
                     "p95": _DEFAULT_TIP_LAMPORTS}
        self.updated = 0.0
        self.ever_fetched = False

    def update(self, data) -> bool:
        """data = parsed tip_floor JSON. Returns True when applied."""
        rows = (data or {}).get("data") or []
        if not rows:
            return False
        row = rows[0] if isinstance(rows[0], dict) else {}

        def _lam(key):
            try:
                return int(float(row.get(key) or 0) * 1e9)
            except (TypeError, ValueError):
                return 0

        vals = [_lam("landed_tips_25th_percentile"),
                _lam("landed_tips_50th_percentile"),
                _lam("landed_tips_75th_percentile"),
                _lam("landed_tips_95th_percentile")]
        if not any(vals):
            return False
        self.pcts = {"p25": vals[0], "p50": vals[1],
                     "p75": vals[2], "p95": vals[3]}
        self.updated = time.time()
        self.ever_fetched = True
        return True

    def stale(self, max_age_s: float = 600.0) -> bool:
        return not self.ever_fetched or (time.time() - self.updated) > max_age_s


def adaptive_tip_lamports(floor: TipFloor, pre_tip_usd: float,
                          sol_px: float, contested: bool = False) -> int:
    """P50 normally; P75 under observed competition; capped by profit share
    and absolute cap. Long-tail liqs are usually uncontested — we pay to
    land reliably, not to win bidding wars."""
    pct = floor.pcts
    base = pct["p75"] if contested else pct["p50"]
    px = max(float(sol_px or 0.0), 1.0)
    share_cap = int((max(pre_tip_usd, 0.0) * TIP_SHARE_CAP / px) * 1e9)
    tip = min(base, share_cap, TIP_ABS_CAP_LAMPORTS)
    # A profitable opportunity must still afford a landable tip.
    if pre_tip_usd > 0 and tip < pct["p25"]:
        tip = min(pct["p25"], TIP_ABS_CAP_LAMPORTS)
    return max(tip, 0)


def priority_fee_lamports(recent_cu_prices, headroom_usd: float,
                          sol_px: float) -> int:
    """Percentile of recent block CU prices (µlam/CU), bounded by profit."""
    vals = sorted(int(v) for v in (recent_cu_prices or []) if v)
    base = percentile(vals, 0.75) if vals else PRIO_FEE_FLOOR
    px = max(float(sol_px or 0.0), 1.0)
    cap_by_profit = int((max(headroom_usd, 0.0) / px) * 1e9 * 1e6 / 1_400_000)
    return max(PRIO_FEE_FLOOR, min(base, PRIO_FEE_CAP, cap_by_profit))


class LossBreaker:
    """Rolling 24h realized-loss circuit breaker.

    Counts estimated tip burn from failed live attempts; pauses execution
    at LOSS_PAUSE_LAMPORTS until manually reset.
    """

    WINDOW_S = 86400

    def __init__(self, threshold_lamports: int = None):
        self.threshold = int(threshold_lamports or LOSS_PAUSE_LAMPORTS)
        self.events: collections.deque = collections.deque()
        self.manual_hold = False

    def record_loss(self, lamports: int, ts: float | None = None) -> None:
        lam = int(lamports or 0)
        if lam <= 0:
            return
        now = ts if ts is not None else time.time()
        self.events.append((now, lam))
        self._prune(now)

    def _prune(self, now: float) -> None:
        while self.events and (now - self.events[0][0]) > self.WINDOW_S:
            self.events.popleft()

    @property
    def lost_lamports(self) -> int:
        self._prune(time.time())
        return sum(l for _, l in self.events)

    @property
    def paused(self) -> bool:
        return self.manual_hold or self.lost_lamports >= self.threshold

    def reset(self) -> None:
        self.events.clear()
        self.manual_hold = False

    def snapshot(self) -> dict:
        return {"lost_sol": round(self.lost_lamports / 1e9, 6),
                "threshold_sol": round(self.threshold / 1e9, 4),
                "paused": self.paused,
                "events_24h": len(self.events)}
