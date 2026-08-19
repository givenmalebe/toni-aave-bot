# Hybrid Execution Engine Design Spec

**Date:** 2026-08-19
**Goal:** Upgrade ETH liquidation bot from amateur to professional-grade execution with three-phase strategy: front-run, backrun fallback, skip.

## Problem Statement

The ETH bot has never landed a real liquidation. Root cause: gas bids are too low — competitors consistently outbid us. The bot has a complete scan → estimate → rank → plan pipeline, but loses every race due to insufficient gas bidding.

## Architecture

### Three-Phase Execution Strategy

```
┌─────────────────────────────────────────────────────────────┐
│                    Hybrid Execution Engine                    │
├─────────────────────────────────────────────────────────────┤
│  Phase 1: Front-Run                                         │
│  ├─ Competitive gas bidding (15% above competitor)          │
│  ├─ Flashbots eth_sendBundle targeting block+1              │
│  └─ Alchemy private relay fallback                          │
├─────────────────────────────────────────────────────────────┤
│  Phase 2: Backrun Fallback                                  │
│  ├─ Detect if front-run failed (check block+1)             │
│  ├─ Simulate competitor tx via debug_traceCall              │
│  └─ Build price-impact backrun via Uni V3                   │
├─────────────────────────────────────────────────────────────┤
│  Phase 3: Skip & Learn                                     │
│  ├─ Cooldown 3 blocks on failed position                   │
│  ├─ Log failure with competitor gas + our bid               │
│  └─ Adapt bid (+25%) after 3 consecutive skips             │
└─────────────────────────────────────────────────────────────┘
```

### Component Overview

| Component | Responsibility | Files |
|-----------|---------------|-------|
| GasBiddingEngine | Competitor tracking, bid calculation, EIP-1559 optimization | `gas_bidder.py` (new) |
| BackrunEngine | Mempool monitoring, price simulation, backrun execution | `backrun.py` (new) |
| HybridExecutor | Orchestrates three-phase strategy | `hybrid_executor.py` (new) |
| AlchemyRelay | Private mempool, Flashbots Protect, relay fallback | `alchemy_relay.py` (new) |
| ExecutionTracker | Logs attempts, outcomes, adapts bids | `execution_tracker.py` (new) |

## Detailed Design

### 1. GasBiddingEngine (`gas_bidder.py`)

**Purpose:** Calculate competitive gas bids based on competitor analysis.

**Competitor Tracking:**
- Scan last 100 blocks for landed `LiquidationCall` events
- Extract `maxFeePerGas` and `maxPriorityFeePerGas` from each tx
- Store in rolling window: `{block_number, gas_price, priority_fee, success}`
- Calculate: `competitor_p95_gas = percentile(gas_prices, 95)`

**Bid Calculation:**
```python
def calculate_bid(opportunity: dict, competitor_p95: float) -> GasBid:
    # Never bid below dynamic floor
    floor = dynamic_min_liq_profit_usd(gas_gwei)
    
    # Bid 15% above competitor's 95th percentile
    base_bid = competitor_p95 * 1.15
    
    # Scale by opportunity profit (higher profit = more aggressive bid)
    profit_multiplier = min(opportunity['net_usd'] / 50, 2.0)  # cap at 2x
    
    # Final bid
    max_fee = base_bid * profit_multiplier
    priority_fee = competitor_p95 * 0.1 * profit_multiplier  # 10% of base
    
    return GasBid(
        max_fee_per_gas=max_fee,
        max_priority_fee_per_gas=priority_fee,
        gas_limit=opportunity['gas_limit']
    )
```

**EIP-1559 Optimization:**
- `maxFeePerGas`: 15% above competitor's max
- `maxPriorityFeePerGas`: 10% of maxFee (ensures priority auction win)
- `gasLimit`: 120% of estimated gas (buffer for state changes)

### 2. BackrunEngine (`backrun.py`)

**Purpose:** When front-run fails, backrun the winner's liquidation.

**Detection:**
- After submitting front-run bundle, wait 1 block
- Check if our tx landed in `block+1`
- If not, check if competitor's tx landed
- If competitor landed, switch to backrun mode

**Price Simulation:**
- Use `debug_traceCall` on competitor's tx to simulate execution
- Calculate price impact: `price_before - price_after` for affected tokens
- Estimate backrun profit: `price_impact * position_size - gas_cost`

**Backrun Execution:**
- Build Uni V3 swap that profits from price impact
- Create Flashbots bundle: `[competitor_tx, our_backrun]`
- Submit via `eth_sendBundle` with `blockNumber: block+2`

### 3. HybridExecutor (`hybrid_executor.py`)

**Purpose:** Orchestrate three-phase strategy.

**State Machine:**
```
IDLE → FRONT_RUN → (SUCCESS | FAIL) → BACKRUN → (SUCCESS | FAIL) → SKIP → IDLE
```

**Flow:**
1. **FRONT_RUN:** Submit aggressive bundle via Flashbots
2. **SUCCESS:** Log win, update competitor data, move to next
3. **FAIL:** Check if competitor landed → switch to BACKRUN
4. **BACKRUN:** Submit backrun bundle
5. **SUCCESS:** Log backrun profit, move to next
6. **FAIL:** Log failure, cooldown 3 blocks, adapt bid

### 4. AlchemyRelay (`alchemy_relay.py`)

**Purpose:** Leverage Alchemy's private mempool features.

**Flashbots Protect:**
- Send bundles via Flashbots relay for private inclusion
- Use `eth_sendBundle` with `X-Flashbots-Signature` header
- Target `block+1` for immediate inclusion

**Private Transaction Relay:**
- Use `eth_sendRawPrivateTransaction` for direct inclusion
- Bypass public mempool entirely
- Faster confirmation than Flashbots

**Mempool Monitoring:**
- Use `debug_traceCall` to simulate pending txs
- Detect pending liquidations before they land
- Build backrun opportunities from pending txs

### 5. ExecutionTracker (`execution_tracker.py`)

**Purpose:** Log attempts, outcomes, adapt bids.

**Data Structure:**
```python
@dataclass
class ExecutionAttempt:
    timestamp: float
    block_number: int
    opportunity: dict
    phase: str  # "front_run" | "backrun" | "skip"
    gas_bid: GasBid
    competitor_gas: float
    outcome: str  # "success" | "fail" | "skip"
    profit_usd: float
    gas_cost_usd: float
```

**Adaptation Logic:**
- Track success rate per phase (front-run vs backrun)
- After 3 consecutive skips on same position: increase bid by 25%
- After 5 consecutive failures: pause position for 10 blocks
- Update competitor_p95 calculation based on recent outcomes

## Integration Points

### With Existing Code

1. **`dashboard.py` → `_broadcast_liquidation`**: Replace with `HybridExecutor.execute()`
2. **`mev_liquidation.py` → `build_full_plan`**: Keep as-is, feeds into executor
3. **`precompute_eth.py`**: Keep as-is, provides cached calldata
4. **`profit_engine.py`**: Keep as-is, provides profit estimates
5. **`live_liquidator.py`**: Replace gas logic with `GasBiddingEngine`

### New Dependencies

- No new Python packages (aiohttp, websockets already used)
- Alchemy API key (user provides)
- Flashbots relay endpoint (public)

## Testing Strategy

1. **Unit tests:** Each component in isolation with mocked RPC
2. **Integration test:** Full pipeline with simulated blocks
3. **Paper trading:** Run alongside live bot for 24 hours
4. **Live test:** Single opportunity with minimum gas

## Risk Mitigation

1. **Gas cost cap:** Maximum 0.01 ETH per attempt (configurable)
2. **Profit floor:** Never bid if estimated profit < $10
3. **Cooldown:** 3 blocks between attempts on same position
4. **Circuit breaker:** Stop after 5 consecutive failures
5. **Manual override:** Dashboard toggle to disable auto-execution

## Success Metrics

1. **Win rate:** >20% of attempted liquidations (vs current 0%)
2. **Gas efficiency:** Average gas cost < 30% of profit
3. **Backrun rate:** >10% of failed front-runs convert to backrun wins
4. **Latency:** Bundle submission within 2 seconds of opportunity detection
