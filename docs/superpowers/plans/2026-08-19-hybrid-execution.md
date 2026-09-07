# Hybrid Execution Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade ETH liquidation bot from amateur to professional-grade execution with three-phase strategy: front-run, backrun fallback, skip.

**Architecture:** Five new modules (`gas_bidder.py`, `backrun.py`, `hybrid_executor.py`, `alchemy_relay.py`, `execution_tracker.py`) that orchestrate competitive gas bidding, mempool monitoring, and adaptive execution. Integration via `HybridExecutor.execute()` replacing the current `_broadcast_liquidation` path.

**Tech Stack:** Python 3.10+, asyncio, aiohttp, existing Flashbots/Alchemy infrastructure

## Global Constraints

- Python 3.10+, asyncio-based (existing codebase pattern)
- No new dependencies beyond `aiohttp` (already used)
- Must not break existing sweep loops — hybrid execution is an upgrade, not a replacement
- All execution must be gated behind a manual toggle (dashboard) for safety
- Maximum gas cost per attempt: 0.01 ETH (configurable)
- Profit floor: never bid if estimated profit < $10

---

## File Structure

| File | Responsibility |
|------|---------------|
| `gas_bidder.py` | Competitor tracking, bid calculation, EIP-1559 optimization |
| `backrun.py` | Mempool monitoring, price simulation, backrun execution |
| `hybrid_executor.py` | Orchestrates three-phase strategy (front-run → backrun → skip) |
| `alchemy_relay.py` | Private mempool, Flashbots Protect, relay fallback |
| `execution_tracker.py` | Logs attempts, outcomes, adapts bids |
| `tests/test_gas_bidder.py` | Unit tests for gas bidding engine |
| `tests/test_backrun.py` | Unit tests for backrun engine |
| `tests/test_hybrid_executor.py` | Unit tests for hybrid executor |
| `tests/test_alchemy_relay.py` | Unit tests for Alchemy relay |
| `tests/test_execution_tracker.py` | Unit tests for execution tracker |
| `dashboard.py` | Add hybrid execution toggle and status display |
| `static/app.js` | Add execution status display |
| `static/index.html` | Add execution status element |

---

### Task 1: Gas Bidding Engine Core

**Files:**
- Create: `gas_bidder.py`
- Test: `tests/test_gas_bidder.py`

**Interfaces:**
- Consumes: None (standalone module)
- Produces: `GasBiddingEngine` class with `calculate_bid()`, `track_competitor()`, `get_competitor_p95()`

- [ ] **Step 1: Create gas_bidder.py with GasBid dataclass and GasBiddingEngine class**

```python
"""Gas bidding engine — competitive gas bids based on competitor analysis."""

import logging
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("gas_bidder")


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
        max_gas_cost_eth: float = 0.01,
    ):
        self.aggressive_factor = aggressive_factor
        self.profit_scale_cap = profit_scale_cap
        self.min_profit_usd = min_profit_usd
        self.max_gas_cost_eth = max_gas_cost_eth
        self._competitor_window: list[CompetitorTx] = []
        self._window_size: int = 100

    def track_competitor(self, tx: CompetitorTx) -> None:
        """Add a competitor tx to the rolling window."""
        self._competitor_window.append(tx)
        if len(self._competitor_window) > self._window_size:
            self._competitor_window = self._competitor_window[-self._window_size:]

    def get_competitor_p95(self) -> float:
        """Get the 95th percentile gas price from competitors."""
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
        
        # Final bid
        max_fee = base_bid * profit_multiplier
        priority_fee = base_bid * 0.1 * profit_multiplier  # 10% of base
        
        # Check gas cost doesn't exceed cap
        gas_cost_eth = (max_fee * gas_limit * 1e-9)
        gas_cost_usd = gas_cost_eth * eth_usd
        if gas_cost_usd > self.max_gas_cost_eth * eth_usd:
            # Cap the bid
            max_fee = (self.max_gas_cost_eth * eth_usd / eth_usd) / (gas_limit * 1e-9)
            priority_fee = max_fee * 0.1

        effective_gas_price = max_fee + priority_fee
        
        return GasBid(
            max_fee_per_gas=max_fee,
            max_priority_fee_per_gas=priority_fee,
            gas_limit=int(gas_limit * 1.2),  # 20% buffer
            effective_gas_price=effective_gas_price,
        )
```

- [ ] **Step 2: Write tests for GasBiddingEngine**

```python
import pytest
from gas_bidder import GasBiddingEngine, CompetitorTx, GasBid


def test_empty_competitors_uses_current_gas():
    engine = GasBiddingEngine()
    opp = {"net_usd": 100, "gas_limit": 1_500_000}
    bid = engine.calculate_bid(opp, current_gas_gwei=20, eth_usd=3000)
    assert bid is not None
    assert bid.max_fee_per_gas == pytest.approx(24.0, rel=0.01)  # 20 * 1.2


def test_competitor_data_increases_bid():
    engine = GasBiddingEngine()
    # Add competitor at 30 gwei
    engine.track_competitor(CompetitorTx(
        block_number=100,
        max_fee_per_gas=30.0,
        max_priority_fee_per_gas=3.0,
        success=True,
    ))
    opp = {"net_usd": 100, "gas_limit": 1_500_000}
    bid = engine.calculate_bid(opp, current_gas_gwei=20, eth_usd=3000)
    assert bid is not None
    # Should bid 15% above competitor p95 (30 * 1.15 = 34.5)
    assert bid.max_fee_per_gas == pytest.approx(34.5, rel=0.01)


def test_low_profit_returns_none():
    engine = GasBiddingEngine(min_profit_usd=10)
    opp = {"net_usd": 5, "gas_limit": 1_500_000}
    bid = engine.calculate_bid(opp, current_gas_gwei=20, eth_usd=3000)
    assert bid is None


def test_high_profit_scales_bid():
    engine = GasBiddingEngine()
    engine.track_competitor(CompetitorTx(
        block_number=100, max_fee_per_gas=30.0,
        max_priority_fee_per_gas=3.0, success=True,
    ))
    # $200 profit should scale bid by 4x (200/50)
    opp = {"net_usd": 200, "gas_limit": 1_500_000}
    bid = engine.calculate_bid(opp, current_gas_gwei=20, eth_usd=3000)
    assert bid is not None
    # 34.5 * 4 = 138 gwei (capped by profit_scale_cap=2.0 → 34.5 * 2 = 69)
    assert bid.max_fee_per_gas == pytest.approx(69.0, rel=0.01)


def test_gas_cost_cap():
    engine = GasBiddingEngine(max_gas_cost_eth=0.01)
    opp = {"net_usd": 1000, "gas_limit": 1_500_000}
    bid = engine.calculate_bid(opp, current_gas_gwei=20, eth_usd=3000)
    assert bid is not None
    # Gas cost should not exceed 0.01 ETH
    gas_cost_eth = bid.max_fee_per_gas * bid.gas_limit * 1e-9
    assert gas_cost_eth <= 0.01


def test_competitor_p95():
    engine = GasBiddingEngine()
    for i in range(20):
        engine.track_competitor(CompetitorTx(
            block_number=i, max_fee_per_gas=float(i),
            max_priority_fee_per_gas=1.0, success=True,
        ))
    p95 = engine.get_competitor_p95()
    # 95th percentile of 0..19 = 18
    assert p95 == pytest.approx(18.0, rel=0.01)
```

- [ ] **Step 3: Run tests**

Run: `cd C:\Users\Surf\Documents\toni-aave-bot && python -m pytest tests/test_gas_bidder.py -v`
Expected: 6 PASS

- [ ] **Step 4: Commit**

```bash
git add gas_bidder.py tests/test_gas_bidder.py
git commit -m "feat: gas bidding engine with competitor tracking"
```

---

### Task 2: Execution Tracker

**Files:**
- Create: `execution_tracker.py`
- Test: `tests/test_execution_tracker.py`

**Interfaces:**
- Consumes: `GasBid` from `gas_bidder.py`
- Produces: `ExecutionTracker` class with `log_attempt()`, `get_adapted_bid()`, `should_skip()`

- [ ] **Step 1: Create execution_tracker.py**

```python
"""Execution tracker — logs attempts, outcomes, adapts bids."""

import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Optional

log = logging.getLogger("execution_tracker")

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
EXECUTION_LOG = os.path.join(DATA_DIR, "execution_log.jsonl")


@dataclass
class ExecutionAttempt:
    timestamp: float
    block_number: int
    opportunity_id: str
    phase: str  # "front_run" | "backrun" | "skip"
    gas_bid_max_fee: float
    gas_bid_priority_fee: float
    competitor_gas: float
    outcome: str  # "success" | "fail" | "skip"
    profit_usd: float
    gas_cost_usd: float


class ExecutionTracker:
    """Track execution attempts and adapt bidding strategy."""

    def __init__(
        self,
        skip_cooldown_blocks: int = 3,
        bid_increase_factor: float = 1.25,
        pause_threshold: int = 5,
        pause_blocks: int = 10,
    ):
        self.skip_cooldown_blocks = skip_cooldown_blocks
        self.bid_increase_factor = bid_increase_factor
        self.pause_threshold = pause_threshold
        self.pause_blocks = pause_blocks
        self._attempts: list[ExecutionAttempt] = []
        self._consecutive_fails: dict[str, int] = {}  # opportunity_id -> count
        self._paused_until: dict[str, int] = {}  # opportunity_id -> block_number

    def log_attempt(self, attempt: ExecutionAttempt) -> None:
        """Log an execution attempt."""
        self._attempts.append(attempt)
        
        # Update consecutive fails
        oid = attempt.opportunity_id
        if attempt.outcome == "fail":
            self._consecutive_fails[oid] = self._consecutive_fails.get(oid, 0) + 1
        else:
            self._consecutive_fails[oid] = 0
        
        # Check if we should pause
        if self._consecutive_fails.get(oid, 0) >= self.pause_threshold:
            self._paused_until[oid] = attempt.block_number + self.pause_blocks
            log.warning("Pausing opportunity %s until block %d", oid, self._paused_until[oid])
        
        # Persist to file
        self._persist(attempt)

    def should_skip(self, opportunity_id: str, current_block: int) -> bool:
        """Check if we should skip this opportunity (cooldown or paused)."""
        # Check pause
        if opportunity_id in self._paused_until:
            if current_block < self._paused_until[opportunity_id]:
                return True
            else:
                del self._paused_until[opportunity_id]
        
        # Check recent failures
        recent_fails = sum(
            1 for a in reversed(self._attempts)
            if a.opportunity_id == opportunity_id
            and a.outcome == "fail"
            and current_block - a.block_number <= self.skip_cooldown_blocks
        )
        return recent_fails >= 1

    def get_adapted_bid(
        self,
        base_bid_max_fee: float,
        base_bid_priority_fee: float,
        opportunity_id: str,
    ) -> tuple[float, float]:
        """Adapt bid based on consecutive failures."""
        fails = self._consecutive_fails.get(opportunity_id, 0)
        if fails >= 2:
            # Increase bid after 2+ failures
            multiplier = self.bid_increase_factor ** (fails - 1)
            return (base_bid_max_fee * multiplier, base_bid_priority_fee * multiplier)
        return (base_bid_max_fee, base_bid_priority_fee)

    def get_stats(self, opportunity_id: Optional[str] = None) -> dict:
        """Get execution statistics."""
        attempts = self._attempts
        if opportunity_id:
            attempts = [a for a in attempts if a.opportunity_id == opportunity_id]
        
        total = len(attempts)
        successes = sum(1 for a in attempts if a.outcome == "success")
        fails = sum(1 for a in attempts if a.outcome == "fail")
        skips = sum(1 for a in attempts if a.outcome == "skip")
        
        return {
            "total": total,
            "successes": successes,
            "fails": fails,
            "skips": skips,
            "success_rate": successes / total if total else 0,
        }

    def _persist(self, attempt: ExecutionAttempt) -> None:
        """Persist attempt to JSONL file."""
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(EXECUTION_LOG, "a") as f:
            f.write(json.dumps(asdict(attempt)) + "\n")
```

- [ ] **Step 2: Write tests for ExecutionTracker**

```python
import pytest
from execution_tracker import ExecutionTracker, ExecutionAttempt


def make_attempt(opportunity_id="0xabc", block=100, outcome="fail"):
    return ExecutionAttempt(
        timestamp=1000.0,
        block_number=block,
        opportunity_id=opportunity_id,
        phase="front_run",
        gas_bid_max_fee=30.0,
        gas_bid_priority_fee=3.0,
        competitor_gas=25.0,
        outcome=outcome,
        profit_usd=50.0,
        gas_cost_usd=5.0,
    )


def test_should_skip_after_failure():
    tracker = ExecutionTracker(skip_cooldown_blocks=3)
    tracker.log_attempt(make_attempt(block=100, outcome="fail"))
    assert tracker.should_skip("0xabc", current_block=101) is True
    assert tracker.should_skip("0xabc", current_block=103) is False


def test_should_not_skip_different_opportunity():
    tracker = ExecutionTracker(skip_cooldown_blocks=3)
    tracker.log_attempt(make_attempt(opportunity_id="0xabc", block=100, outcome="fail"))
    assert tracker.should_skip("0xdef", current_block=101) is False


def test_bid_adaptation_after_failures():
    tracker = ExecutionTracker(bid_increase_factor=1.25)
    # 2 failures should increase bid
    tracker.log_attempt(make_attempt(block=100, outcome="fail"))
    tracker.log_attempt(make_attempt(block=101, outcome="fail"))
    max_fee, priority_fee = tracker.get_adapted_bid(30.0, 3.0, "0xabc")
    # 2 fails: multiplier = 1.25^(2-1) = 1.25
    assert max_fee == pytest.approx(37.5, rel=0.01)


def test_bid_no_adaptation_on_success():
    tracker = ExecutionTracker()
    tracker.log_attempt(make_attempt(block=100, outcome="success"))
    max_fee, priority_fee = tracker.get_adapted_bid(30.0, 3.0, "0xabc")
    assert max_fee == 30.0


def test_pause_after_threshold():
    tracker = ExecutionTracker(pause_threshold=3, pause_blocks=10)
    for i in range(3):
        tracker.log_attempt(make_attempt(block=100 + i, outcome="fail"))
    assert tracker.should_skip("0xabc", current_block=105) is True
    assert tracker.should_skip("0xabc", current_block=112) is False


def test_stats():
    tracker = ExecutionTracker()
    tracker.log_attempt(make_attempt(outcome="success"))
    tracker.log_attempt(make_attempt(outcome="fail"))
    tracker.log_attempt(make_attempt(outcome="skip"))
    stats = tracker.get_stats()
    assert stats["total"] == 3
    assert stats["success_rate"] == pytest.approx(1 / 3, rel=0.01)
```

- [ ] **Step 3: Run tests**

Run: `cd C:\Users\Surf\Documents\toni-aave-bot && python -m pytest tests/test_execution_tracker.py -v`
Expected: 6 PASS

- [ ] **Step 4: Commit**

```bash
git add execution_tracker.py tests/test_execution_tracker.py
git commit -m "feat: execution tracker with bid adaptation"
```

---

### Task 3: Alchemy Relay

**Files:**
- Create: `alchemy_relay.py`
- Test: `tests/test_alchemy_relay.py`

**Interfaces:**
- Consumes: Alchemy API key, Flashbots relay endpoint
- Produces: `AlchemyRelay` class with `send_bundle()`, `send_private_tx()`

- [ ] **Step 1: Create alchemy_relay.py**

```python
"""Alchemy relay — private mempool, Flashbots Protect, relay fallback."""

import json
import logging
import os
from typing import Optional

import aiohttp

log = logging.getLogger("alchemy_relay")

# Endpoints
FLASHBOTS_RELAY = "https://relay.flashbots.net"
ALCHEMY_PRIVATE = "https://eth-mainnet.g.alchemy.com/v2"


class AlchemyRelay:
    """Send transactions via Alchemy private relay and Flashbots."""

    def __init__(
        self,
        alchemy_api_key: Optional[str] = None,
        flashbots_relay: str = FLASHBOTS_RELAY,
    ):
        self.alchemy_api_key = alchemy_api_key or os.getenv("ALCHEMY_API_KEY", "")
        self.flashbots_relay = flashbots_relay
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def send_bundle(
        self,
        signed_txs: list[str],
        block_number: int,
        signer_address: str,
        signature: str,
    ) -> dict:
        """Send bundle via Flashbots relay."""
        session = await self._get_session()
        
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_sendBundle",
            "params": [
                {
                    "txs": signed_txs,
                    "blockNumber": hex(block_number),
                    "minTimestamp": 0,
                    "maxTimestamp": 0,
                }
            ],
        }
        
        headers = {
            "Content-Type": "application/json",
            "X-Flashbots-Signature": f"{signer_address}:{signature}",
        }
        
        try:
            async with session.post(
                self.flashbots_relay,
                json=body,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                result = await resp.json()
                if "error" in result:
                    log.warning("Flashbots bundle failed: %s", result["error"])
                    return {"success": False, "error": result["error"]}
                log.info("Flashbots bundle submitted for block %d", block_number)
                return {"success": True, "result": result.get("result")}
        except Exception as e:
            log.warning("Flashbots relay error: %s", e)
            return {"success": False, "error": str(e)}

    async def send_private_tx(
        self,
        signed_tx: str,
    ) -> dict:
        """Send private transaction via Alchemy."""
        if not self.alchemy_api_key:
            return {"success": False, "error": "No Alchemy API key"}
        
        session = await self._get_session()
        url = f"{ALCHEMY_PRIVATE}/{self.alchemy_api_key}"
        
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_sendRawPrivateTransaction",
            "params": [{"signedTransaction": signed_tx}],
        }
        
        try:
            async with session.post(
                url,
                json=body,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                result = await resp.json()
                if "error" in result:
                    log.warning("Alchemy private tx failed: %s", result["error"])
                    return {"success": False, "error": result["error"]}
                log.info("Alchemy private tx submitted")
                return {"success": True, "result": result.get("result")}
        except Exception as e:
            log.warning("Alchemy relay error: %s", e)
            return {"success": False, "error": str(e)}

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
```

- [ ] **Step 2: Write tests for AlchemyRelay**

```python
import pytest
from unittest.mock import AsyncMock, MagicMock
from alchemy_relay import AlchemyRelay


@pytest.mark.asyncio
async def test_send_bundle_success():
    relay = AlchemyRelay(alchemy_api_key="test_key")
    
    mock_resp = AsyncMock()
    mock_resp.json = AsyncMock(return_value={"result": "0x123"})
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    
    mock_session = AsyncMock()
    mock_session.post = MagicMock(return_value=mock_resp)
    mock_session.closed = False
    relay._session = mock_session
    
    result = await relay.send_bundle(
        signed_txs=["0xabc"],
        block_number=100,
        signer_address="0xsigner",
        signature="0xsig",
    )
    assert result["success"] is True


@pytest.mark.asyncio
async def test_send_bundle_flashbots_error():
    relay = AlchemyRelay()
    
    mock_resp = AsyncMock()
    mock_resp.json = AsyncMock(return_value={"error": {"message": "failed"}})
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    
    mock_session = AsyncMock()
    mock_session.post = MagicMock(return_value=mock_resp)
    mock_session.closed = False
    relay._session = mock_session
    
    result = await relay.send_bundle(
        signed_txs=["0xabc"],
        block_number=100,
        signer_address="0xsigner",
        signature="0xsig",
    )
    assert result["success"] is False


@pytest.mark.asyncio
async def test_send_private_tx_no_key():
    relay = AlchemyRelay(alchemy_api_key="")
    result = await relay.send_private_tx("0xabc")
    assert result["success"] is False
    assert "No Alchemy API key" in result["error"]
```

- [ ] **Step 3: Run tests**

Run: `cd C:\Users\Surf\Documents\toni-aave-bot && python -m pytest tests/test_alchemy_relay.py -v`
Expected: 3 PASS

- [ ] **Step 4: Commit**

```bash
git add alchemy_relay.py tests/test_alchemy_relay.py
git commit -m "feat: Alchemy relay with Flashbots and private tx support"
```

---

### Task 4: Backrun Engine

**Files:**
- Create: `backrun.py`
- Test: `tests/test_backrun.py`

**Interfaces:**
- Consumes: AlchemyRelay from `alchemy_relay.py`
- Produces: `BackrunEngine` class with `detect_competitor_tx()`, `simulate_price_impact()`, `build_backrun()`

- [ ] **Step 1: Create backrun.py**

```python
"""Backrun engine — detect competitor txs, simulate price impact, build backruns."""

import logging
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("backrun")


@dataclass
class CompetitorLanding:
    tx_hash: str
    block_number: int
    user: str
    protocol: str
    profit_usd: float


@dataclass
class BackrunOpportunity:
    competitor_tx: str
    price_impact: float  # percentage
    estimated_profit_usd: float
    swap_path: str  # uni v3 path


class BackrunEngine:
    """Detect competitor liquidations and build backrun opportunities."""

    def __init__(self):
        self._recent_competitor_txs: list[CompetitorLanding] = []

    def detect_competitor_tx(
        self,
        block_txs: list[dict],
        our_tx_hash: Optional[str],
    ) -> Optional[CompetitorLanding]:
        """Check if a competitor landed a liquidation in the block."""
        for tx in block_txs:
            # Skip our own tx
            if tx.get("hash") == our_tx_hash:
                continue
            
            # Check if it's a liquidation (selector match)
            input_data = tx.get("input", "")
            liq_selectors = ["0xc2fa746c", "0x9b4c6d1f"]  # Aave V3/V4
            for sel in liq_selectors:
                if input_data.startswith(sel):
                    landing = CompetitorLanding(
                        tx_hash=tx["hash"],
                        block_number=tx.get("blockNumber", 0),
                        user="",
                        protocol="aave",
                        profit_usd=0,
                    )
                    self._recent_competitor_txs.append(landing)
                    return landing
        
        return None

    def simulate_price_impact(
        self,
        competitor_tx: CompetitorLanding,
        token_in: str,
        token_out: str,
        amount: int,
    ) -> float:
        """Simulate price impact of competitor's liquidation.
        
        Returns estimated price impact as a percentage (0.01 = 1%).
        """
        # For now, use a simple heuristic based on position size
        # In production, this would use debug_traceCall
        # Larger positions = larger price impact
        impact = min(amount / 1_000_000, 0.05)  # cap at 5%
        return impact

    def build_backrun(
        self,
        competitor_tx: CompetitorLanding,
        price_impact: float,
        estimated_profit_usd: float,
        gas_limit: int = 500_000,
    ) -> Optional[BackrunOpportunity]:
        """Build a backrun opportunity from a competitor's liquidation."""
        if estimated_profit_usd < 5:  # minimum $5 profit
            return None
        
        return BackrunOpportunity(
            competitor_tx=competitor_tx.tx_hash,
            price_impact=price_impact,
            estimated_profit_usd=estimated_profit_usd,
            swap_path="",  # would be populated by Uni V3 path resolver
        )

    def get_recent_competitors(self, count: int = 10) -> list[CompetitorLanding]:
        """Get recent competitor landings."""
        return self._recent_competitor_txs[-count:]
```

- [ ] **Step 2: Write tests for BackrunEngine**

```python
import pytest
from backrun import BackrunEngine, CompetitorLanding


def test_detect_competitor_liquidation():
    engine = BackrunEngine()
    txs = [
        {"hash": "0xcomp", "input": "0xc2fa746c000000", "blockNumber": 100},
        {"hash": "0xother", "input": "0x12345678", "blockNumber": 100},
    ]
    result = engine.detect_competitor_tx(txs, our_tx_hash="0xours")
    assert result is not None
    assert result.tx_hash == "0xcomp"


def test_skip_own_tx():
    engine = BackrunEngine()
    txs = [
        {"hash": "0xours", "input": "0xc2fa746c000000", "blockNumber": 100},
    ]
    result = engine.detect_competitor_tx(txs, our_tx_hash="0xours")
    assert result is None


def test_no_competitor():
    engine = BackrunEngine()
    txs = [
        {"hash": "0xother", "input": "0x12345678", "blockNumber": 100},
    ]
    result = engine.detect_competitor_tx(txs, our_tx_hash="0xours")
    assert result is None


def test_build_backrun_minimum_profit():
    engine = BackrunEngine()
    landing = CompetitorLanding(
        tx_hash="0xcomp", block_number=100,
        user="0xuser", protocol="aave", profit_usd=50,
    )
    result = engine.build_backrun(landing, price_impact=0.02, estimated_profit_usd=3)
    assert result is None  # below $5 minimum


def test_build_backrun_viable():
    engine = BackrunEngine()
    landing = CompetitorLanding(
        tx_hash="0xcomp", block_number=100,
        user="0xuser", protocol="aave", profit_usd=50,
    )
    result = engine.build_backrun(landing, price_impact=0.02, estimated_profit_usd=15)
    assert result is not None
    assert result.competitor_tx == "0xcomp"
```

- [ ] **Step 3: Run tests**

Run: `cd C:\Users\Surf\Documents\toni-aave-bot && python -m pytest tests/test_backrun.py -v`
Expected: 5 PASS

- [ ] **Step 4: Commit**

```bash
git add backrun.py tests/test_backrun.py
git commit -m "feat: backrun engine with competitor detection"
```

---

### Task 5: Hybrid Executor

**Files:**
- Create: `hybrid_executor.py`
- Test: `tests/test_hybrid_executor.py`

**Interfaces:**
- Consumes: GasBiddingEngine, BackrunEngine, AlchemyRelay, ExecutionTracker
- Produces: `HybridExecutor` class with `execute()`, `check_front_run_result()`

- [ ] **Step 1: Create hybrid_executor.py**

```python
"""Hybrid executor — orchestrates three-phase execution strategy."""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from gas_bidder import GasBiddingEngine, GasBid
from backrun import BackrunEngine, BackrunOpportunity
from alchemy_relay import AlchemyRelay
from execution_tracker import ExecutionTracker, ExecutionAttempt

log = logging.getLogger("hybrid_executor")


class ExecutionPhase(Enum):
    IDLE = "idle"
    FRONT_RUN = "front_run"
    BACKRUN = "backrun"
    SKIP = "skip"


@dataclass
class ExecutionContext:
    opportunity: dict
    current_block: int
    signed_txs: list[str]
    tx_hash: str
    signer_address: str
    signature: str


class HybridExecutor:
    """Orchestrate three-phase execution: front-run → backrun → skip."""

    def __init__(
        self,
        gas_bidder: GasBiddingEngine,
        backrun_engine: BackrunEngine,
        relay: AlchemyRelay,
        tracker: ExecutionTracker,
        enabled: bool = True,
    ):
        self.gas_bidder = gas_bidder
        self.backrun_engine = backrun_engine
        self.relay = relay
        self.tracker = tracker
        self.enabled = enabled
        self._phase = ExecutionPhase.IDLE
        self._pending_context: Optional[ExecutionContext] = None

    async def execute(
        self,
        opportunity: dict,
        current_block: int,
        current_gas_gwei: float,
        eth_usd: float,
    ) -> dict:
        """Execute a liquidation opportunity with three-phase strategy."""
        if not self.enabled:
            return {"outcome": "disabled", "phase": "none"}
        
        opp_id = opportunity.get("user", "unknown")
        
        # Check if we should skip
        if self.tracker.should_skip(opp_id, current_block):
            log.info("Skipping %s (cooldown/paused)", opp_id)
            return {"outcome": "skip", "phase": "skip"}
        
        # Calculate gas bid
        bid = self.gas_bidder.calculate_bid(opportunity, current_gas_gwei, eth_usd)
        if bid is None:
            return {"outcome": "skip", "phase": "skip", "reason": "profit_too_low"}
        
        # Adapt bid based on history
        max_fee, priority_fee = self.tracker.get_adapted_bid(
            bid.max_fee_per_gas, bid.max_priority_fee_per_gas, opp_id
        )
        bid.max_fee_per_gas = max_fee
        bid.max_priority_fee_per_gas = priority_fee
        
        # Phase 1: Front-run
        self._phase = ExecutionPhase.FRONT_RUN
        log.info("Front-running %s with bid %.1f gwei", opp_id, bid.max_fee_per_gas)
        
        # Build context for backrun fallback
        self._pending_context = ExecutionContext(
            opportunity=opportunity,
            current_block=current_block,
            signed_txs=[],  # would be populated by live_liquidator
            tx_hash="",
            signer_address="",
            signature="",
        )
        
        return {
            "outcome": "front_run_submitted",
            "phase": "front_run",
            "bid": {
                "max_fee_per_gas": bid.max_fee_per_gas,
                "max_priority_fee_per_gas": bid.max_priority_fee_per_gas,
                "gas_limit": bid.gas_limit,
            },
        }

    async def check_front_run_result(
        self,
        our_tx_hash: str,
        block_txs: list[dict],
        current_block: int,
    ) -> dict:
        """Check if front-run succeeded, fallback to backrun if not."""
        if self._phase != ExecutionPhase.FRONT_RUN:
            return {"outcome": "not_in_front_run"}
        
        # Check if our tx landed
        our_landed = any(tx.get("hash") == our_tx_hash for tx in block_txs)
        
        if our_landed:
            # Success!
            self.tracker.log_attempt(ExecutionAttempt(
                timestamp=0,
                block_number=current_block,
                opportunity_id=self._pending_context.opportunity.get("user", ""),
                phase="front_run",
                gas_bid_max_fee=0,
                gas_bid_priority_fee=0,
                competitor_gas=0,
                outcome="success",
                profit_usd=0,
                gas_cost_usd=0,
            ))
            self._phase = ExecutionPhase.IDLE
            self._pending_context = None
            return {"outcome": "success", "phase": "front_run"}
        
        # Front-run failed, check if competitor landed
        competitor = self.backrun_engine.detect_competitor_tx(
            block_txs, our_tx_hash
        )
        
        if competitor:
            # Phase 2: Backrun
            self._phase = ExecutionPhase.BACKRUN
            log.info("Front-run failed, switching to backrun for %s", competitor.tx_hash)
            return {
                "outcome": "switching_to_backrun",
                "phase": "backrun",
                "competitor_tx": competitor.tx_hash,
            }
        
        # No competitor either, skip
        self.tracker.log_attempt(ExecutionAttempt(
            timestamp=0,
            block_number=current_block,
            opportunity_id=self._pending_context.opportunity.get("user", ""),
            phase="front_run",
            gas_bid_max_fee=0,
            gas_bid_priority_fee=0,
            competitor_gas=0,
            outcome="fail",
            profit_usd=0,
            gas_cost_usd=0,
        ))
        self._phase = ExecutionPhase.IDLE
        self._pending_context = None
        return {"outcome": "fail", "phase": "front_run"}

    @property
    def phase(self) -> ExecutionPhase:
        return self._phase
```

- [ ] **Step 2: Write tests for HybridExecutor**

```python
import pytest
from unittest.mock import AsyncMock, MagicMock
from hybrid_executor import HybridExecutor, ExecutionPhase
from gas_bidder import GasBiddingEngine, GasBid
from backrun import BackrunEngine
from alchemy_relay import AlchemyRelay
from execution_tracker import ExecutionTracker


def make_executor(enabled=True):
    gas_bidder = GasBiddingEngine(min_profit_usd=10)
    backrun = BackrunEngine()
    relay = AsyncMock(spec=AlchemyRelay)
    tracker = ExecutionTracker()
    return HybridExecutor(gas_bidder, backrun, relay, tracker, enabled=enabled)


@pytest.mark.asyncio
async def test_execute_disabled():
    executor = make_executor(enabled=False)
    result = await executor.execute(
        opportunity={"user": "0xabc", "net_usd": 100, "gas_limit": 1_500_000},
        current_block=100,
        current_gas_gwei=20,
        eth_usd=3000,
    )
    assert result["outcome"] == "disabled"


@pytest.mark.asyncio
async def test_execute_skip_low_profit():
    executor = make_executor()
    result = await executor.execute(
        opportunity={"user": "0xabc", "net_usd": 5, "gas_limit": 1_500_000},
        current_block=100,
        current_gas_gwei=20,
        eth_usd=3000,
    )
    assert result["outcome"] == "skip"


@pytest.mark.asyncio
async def test_execute_front_run():
    executor = make_executor()
    result = await executor.execute(
        opportunity={"user": "0xabc", "net_usd": 100, "gas_limit": 1_500_000},
        current_block=100,
        current_gas_gwei=20,
        eth_usd=3000,
    )
    assert result["outcome"] == "front_run_submitted"
    assert executor.phase == ExecutionPhase.FRONT_RUN


@pytest.mark.asyncio
async def test_check_front_run_success():
    executor = make_executor()
    await executor.execute(
        opportunity={"user": "0xabc", "net_usd": 100, "gas_limit": 1_500_000},
        current_block=100,
        current_gas_gwei=20,
        eth_usd=3000,
    )
    result = await executor.check_front_run_result(
        our_tx_hash="0xours",
        block_txs=[{"hash": "0xours"}],
        current_block=101,
    )
    assert result["outcome"] == "success"
    assert executor.phase == ExecutionPhase.IDLE
```

- [ ] **Step 3: Run tests**

Run: `cd C:\Users\Surf\Documents\toni-aave-bot && python -m pytest tests/test_hybrid_executor.py -v`
Expected: 4 PASS

- [ ] **Step 4: Commit**

```bash
git add hybrid_executor.py tests/test_hybrid_executor.py
git commit -m "feat: hybrid executor with three-phase strategy"
```

---

### Task 6: Integration with Dashboard

**Files:**
- Modify: `dashboard.py` (add toggle and status)
- Modify: `static/app.js` (add execution status display)
- Modify: `static/index.html` (add execution status element)
- Modify: `static/style.css` (add execution status styles)

**Interfaces:**
- Consumes: HybridExecutor, ExecutionTracker
- Produces: Dashboard toggle for hybrid execution, status display

- [ ] **Step 1: Add hybrid execution toggle to dashboard.py**

In `dashboard.py`, add after the existing bot controls:

```python
# Hybrid execution toggle
self.hybrid_enabled = False  # default off for safety
self.hybrid_executor = None  # initialized in start_loops
```

In `snapshot()`, add to state:

```python
state["hybrid_execution"] = {
    "enabled": self.hybrid_enabled,
    "phase": self.hybrid_executor.phase.value if self.hybrid_executor else "idle",
    "stats": self.hybrid_executor.tracker.get_stats() if self.hybrid_executor else {},
}
```

In `start_loops()`, add initialization:

```python
from gas_bidder import GasBiddingEngine
from backrun import BackrunEngine
from alchemy_relay import AlchemyRelay
from execution_tracker import ExecutionTracker
from hybrid_executor import HybridExecutor

gas_bidder = GasBiddingEngine()
backrun = BackrunEngine()
relay = AlchemyRelay()
tracker = ExecutionTracker()
self.hybrid_executor = HybridExecutor(
    gas_bidder, backrun, relay, tracker, enabled=self.hybrid_enabled
)
```

- [ ] **Step 2: Add API endpoint for toggle**

In `dashboard.py`, add route:

```python
@routes.post("/api/hybrid/toggle")
async def hybrid_toggle(request):
    data = await request.json()
    enabled = data.get("enabled", False)
    self.hybrid_enabled = enabled
    if self.hybrid_executor:
        self.hybrid_executor.enabled = enabled
    return web.json_response({"enabled": enabled})
```

- [ ] **Step 3: Add HTML element for execution status**

In `static/index.html`, add inside the ETH Bot card:

```html
<div class="section-head">Hybrid Execution</div>
<div class="hybrid-status">
  <label class="toggle">
    <input type="checkbox" id="hybrid-toggle" onchange="toggleHybrid(this.checked)">
    <span class="toggle-slider"></span>
  </label>
  <span class="status-text" id="hybrid-phase">IDLE</span>
  <span class="stat" id="hybrid-success-rate">0%</span>
</div>
```

- [ ] **Step 4: Add JS for toggle and status**

In `static/app.js`, add:

```javascript
function toggleHybrid(enabled) {
  fetch("/api/hybrid/toggle", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({enabled}),
  });
}

// In render() function:
const hybrid = s.hybrid_execution || {};
const hybridPhase = $("hybrid-phase");
const hybridSuccessRate = $("hybrid-success-rate");
if (hybridPhase) hybridPhase.textContent = (hybrid.phase || "idle").toUpperCase();
if (hybridSuccessRate) {
  const stats = hybrid.stats || {};
  const rate = stats.success_rate || 0;
  hybridSuccessRate.textContent = `${(rate * 100).toFixed(1)}%`;
}
```

- [ ] **Step 5: Add CSS for execution status**

In `static/style.css`, add:

```css
.hybrid-status {
  display: flex; align-items: center; gap: 12px;
  padding: 8px 0; font-size: 12px;
}
.hybrid-status .toggle { /* toggle switch styles */ }
.hybrid-status .status-text {
  font-family: var(--mono); font-weight: 600;
  color: var(--green);
}
.hybrid-status .stat {
  color: var(--dim); font-family: var(--mono);
}
```

- [ ] **Step 6: Verify dashboard starts**

Run: `python dashboard.py`
Expected: Dashboard starts, hybrid execution toggle visible, status shows IDLE

- [ ] **Step 7: Commit**

```bash
git add dashboard.py static/app.js static/index.html static/style.css
git commit -m "feat: dashboard hybrid execution toggle and status"
```

---

### Task 7: Integration Tests

**Files:**
- Create: `tests/test_hybrid_integration.py`

**Interfaces:**
- Consumes: All five new modules
- Produces: Verification that the full pipeline works end-to-end

- [ ] **Step 1: Write integration test**

```python
"""Integration test: verify hybrid execution pipeline works end-to-end."""

import pytest
from gas_bidder import GasBiddingEngine, CompetitorTx
from backrun import BackrunEngine
from alchemy_relay import AlchemyRelay
from execution_tracker import ExecutionTracker, ExecutionAttempt
from hybrid_executor import HybridExecutor, ExecutionPhase


def test_full_pipeline_front_run():
    """Test: opportunity → gas bid → front-run → success."""
    # Setup
    gas_bidder = GasBiddingEngine(min_profit_usd=10)
    backrun = BackrunEngine()
    relay = AlchemyRelay()
    tracker = ExecutionTracker()
    executor = HybridExecutor(gas_bidder, backrun, relay, tracker)
    
    # Add competitor data
    gas_bidder.track_competitor(CompetitorTx(
        block_number=100, max_fee_per_gas=30.0,
        max_priority_fee_per_gas=3.0, success=True,
    ))
    
    # Execute
    import asyncio
    result = asyncio.get_event_loop().run_until_complete(executor.execute(
        opportunity={"user": "0xabc", "net_usd": 100, "gas_limit": 1_500_000},
        current_block=101,
        current_gas_gwei=20,
        eth_usd=3000,
    ))
    
    assert result["outcome"] == "front_run_submitted"
    assert result["phase"] == "front_run"
    assert result["bid"]["max_fee_per_gas"] > 30  # above competitor


def test_full_pipeline_backrun_fallback():
    """Test: front-run fails → competitor lands → switch to backrun."""
    gas_bidder = GasBiddingEngine(min_profit_usd=10)
    backrun = BackrunEngine()
    relay = AlchemyRelay()
    tracker = ExecutionTracker()
    executor = HybridExecutor(gas_bidder, backrun, relay, tracker)
    
    import asyncio
    
    # Phase 1: Front-run
    asyncio.get_event_loop().run_until_complete(executor.execute(
        opportunity={"user": "0xabc", "net_usd": 100, "gas_limit": 1_500_000},
        current_block=101,
        current_gas_gwei=20,
        eth_usd=3000,
    ))
    
    # Phase 2: Check result — our tx not in block, competitor is
    result = asyncio.get_event_loop().run_until_complete(executor.check_front_run_result(
        our_tx_hash="0xours",
        block_txs=[{"hash": "0xcomp", "input": "0xc2fa746c000000", "blockNumber": 102}],
        current_block=102,
    ))
    
    assert result["outcome"] == "switching_to_backrun"
    assert executor.phase == ExecutionPhase.BACKRUN


def test_full_pipeline_skip():
    """Test: opportunity below profit floor → skip."""
    gas_bidder = GasBiddingEngine(min_profit_usd=10)
    backrun = BackrunEngine()
    relay = AlchemyRelay()
    tracker = ExecutionTracker()
    executor = HybridExecutor(gas_bidder, backrun, relay, tracker)
    
    import asyncio
    result = asyncio.get_event_loop().run_until_complete(executor.execute(
        opportunity={"user": "0xabc", "net_usd": 5, "gas_limit": 1_500_000},
        current_block=101,
        current_gas_gwei=20,
        eth_usd=3000,
    ))
    
    assert result["outcome"] == "skip"


def test_tracker_adapts_bids():
    """Test: consecutive failures increase bid."""
    tracker = ExecutionTracker(bid_increase_factor=1.25)
    
    # Simulate 3 failures
    for i in range(3):
        tracker.log_attempt(ExecutionAttempt(
            timestamp=1000 + i, block_number=100 + i,
            opportunity_id="0xabc", phase="front_run",
            gas_bid_max_fee=30.0, gas_bid_priority_fee=3.0,
            competitor_gas=25.0, outcome="fail",
            profit_usd=50.0, gas_cost_usd=5.0,
        ))
    
    # Bid should be adapted
    max_fee, priority_fee = tracker.get_adapted_bid(30.0, 3.0, "0xabc")
    assert max_fee > 30.0  # increased due to failures
```

- [ ] **Step 2: Run integration tests**

Run: `cd C:\Users\Surf\Documents\toni-aave-bot && python -m pytest tests/test_hybrid_integration.py -v`
Expected: 4 PASS

- [ ] **Step 3: Run all hybrid tests together**

Run: `cd C:\Users\Surf\Documents\toni-aave-bot && python -m pytest tests/test_gas_bidder.py tests/test_execution_tracker.py tests/test_alchemy_relay.py tests/test_backrun.py tests/test_hybrid_executor.py tests/test_hybrid_integration.py -v`
Expected: All PASS (28 total)

- [ ] **Step 4: Commit**

```bash
git add tests/test_hybrid_integration.py
git commit -m "feat: hybrid execution integration tests"
```
