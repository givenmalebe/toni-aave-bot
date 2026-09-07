"""Gas bidding engine — competitive gas bids based on competitor analysis."""

import json
import logging
import os
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("gas_bidder")

# --- Adaptive ETH outbidding bandit ---
# Learned per-segment multipliers on the priority-fee bid. Lost races escalate
# the bid (we under-bid competitors), wins ease it (we over-bid). Persisted so
# learned competitive pressure survives restarts.
HERE = os.path.dirname(os.path.abspath(__file__))
ETH_BID_STATE_PATH = os.path.join(HERE, "data", "eth_bid_bandit.json")
ETH_BID_MIN = 0.7
ETH_BID_MAX = 3.0
ETH_BID_LOSS_STEP = 1.25
ETH_BID_WIN_STEP = 0.9
_ETH_BID = {"calm": 1.0, "contested": 1.0}
_ETH_BID_LOCK = threading.Lock()


def _load_eth_bid() -> None:
    try:
        with open(ETH_BID_STATE_PATH) as f:
            data = json.load(f)
        with _ETH_BID_LOCK:
            for k in ("calm", "contested"):
                v = float(data.get(k, 1.0) or 1.0)
                _ETH_BID[k] = min(max(v, ETH_BID_MIN), ETH_BID_MAX)
    except Exception as e:
        log.debug("eth bid bandit load failed: %s", e)


def _save_eth_bid() -> None:
    try:
        with _ETH_BID_LOCK:
            snapshot = dict(_ETH_BID)
        with open(ETH_BID_STATE_PATH, "w") as f:
            json.dump(snapshot, f)
    except Exception as e:
        log.debug("eth bid bandit save failed: %s", e)


def eth_bid_mult(contested: bool = False) -> float:
    """Learned priority-fee bid multiplier for a contest/calm fire."""
    _load_eth_bid()
    key = "contested" if contested else "calm"
    with _ETH_BID_LOCK:
        return _ETH_BID[key]


def update_eth_bid_mult(contested: bool, won: bool) -> float:
    """Train the ETH bid multiplier from a real race outcome."""
    key = "contested" if contested else "calm"
    _load_eth_bid()
    with _ETH_BID_LOCK:
        cur = _ETH_BID[key]
        if won:
            nxt = max(cur * ETH_BID_WIN_STEP, ETH_BID_MIN)
        else:
            nxt = min(cur * ETH_BID_LOSS_STEP, ETH_BID_MAX)
        _ETH_BID[key] = round(nxt, 4)
    _save_eth_bid()
    return nxt


def eth_bid_stats() -> dict:
    _load_eth_bid()
    with _ETH_BID_LOCK:
        return {"calm": _ETH_BID["calm"], "contested": _ETH_BID["contested"]}


@dataclass
class GasBid:
    max_fee_per_gas: float  # gwei
    max_priority_fee_per_gas: float  # gwei
    gas_limit: int
    effective_gas_price: float  # gwei (computed)


@dataclass
class CompetitorTx:
    block_number: int
    max_fee_per_gas: float  # gwei
    max_priority_fee_per_gas: float  # gwei
    success: bool


class GasBiddingEngine:
    """Calculate competitive gas bids based on competitor analysis."""

    def __init__(
        self,
        aggressive_factor: float = 1.15,
        profit_scale_cap: float = 2.0,
        min_profit_usd: float = 10.0,
        max_gas_cost_eth: float = 0.1,
    ):
        self.aggressive_factor = aggressive_factor
        self.profit_scale_cap = profit_scale_cap
        self.min_profit_usd = min_profit_usd
        self.max_gas_cost_eth = max_gas_cost_eth
        self._lock = threading.Lock()
        self._competitor_window: deque = deque(maxlen=100)
        self._window_size: int = 100

    def track_competitor(self, tx: CompetitorTx) -> None:
        """Add a competitor tx to the rolling window."""
        with self._lock:
            self._competitor_window.append(tx)

    def get_competitor_p95(self) -> float:
        """Get the 95th percentile gas price from competitors."""
        with self._lock:
            if not self._competitor_window:
                return 0.0
            prices = sorted(tx.max_fee_per_gas for tx in self._competitor_window)
        idx = int(len(prices) * 0.95)
        return prices[min(idx, len(prices) - 1)]

    def calculate_bid(
        self,
        opportunity: dict,
        current_gas_gwei: float,
        eth_usd: float,
    ) -> Optional[GasBid]:
        """Calculate a competitive gas bid for an opportunity."""
        net_usd = opportunity.get("net_usd", 0)
        gas_limit = opportunity.get("gas_limit", 1_500_000)

        # Skip if profit too low
        if net_usd < self.min_profit_usd:
            return None

        # Get competitor baseline
        competitor_p95 = self.get_competitor_p95()

        # If no competitor data, use current gas + 20%
        if competitor_p95 == 0:
            base_bid = current_gas_gwei * 1.2
        else:
            # Bid aggressive_factor above competitor's p95
            base_bid = competitor_p95 * self.aggressive_factor

        # Scale by opportunity profit (higher profit = more aggressive)
        profit_multiplier = min(net_usd / 50, self.profit_scale_cap)

        # Learn when we are in a contest and beat competitors harder.
        contested = bool(opportunity.get("contested")
                         or opportunity.get("race_contested"))
        outbid = eth_bid_mult(contested)

        # Final bid
        max_fee = base_bid * profit_multiplier
        priority_fee = max(base_bid * 0.2 * profit_multiplier, 2.0) * outbid
        max_fee = max(max_fee, priority_fee)

        # Apply 20% buffer to gas limit
        buffered_gas_limit = int(gas_limit * 1.2)

        # Check gas cost doesn't exceed cap (using buffered gas limit)
        gas_cost_eth = max_fee * buffered_gas_limit * 1e-9
        gas_cost_usd = gas_cost_eth * eth_usd
        if gas_cost_usd > self.max_gas_cost_eth * eth_usd:
            # Cap the bid based on buffered gas limit
            max_fee = (self.max_gas_cost_eth * eth_usd / eth_usd) / (buffered_gas_limit * 1e-9)
            priority_fee = max_fee * 0.1 * outbid

        effective_gas_price = max_fee + priority_fee
        
        return GasBid(
            max_fee_per_gas=max_fee,
            max_priority_fee_per_gas=priority_fee,
            gas_limit=buffered_gas_limit,
            effective_gas_price=effective_gas_price,
        )
