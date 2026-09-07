# Design: Block-Triggered Pre-compute Layer

## Goal

Same-block liquidation submission on both ETH and SOL. Pre-compute calldata for all hot positions on every block, so when sweep detects an opportunity, only signing + submission (~85ms) remains.

## Approach

**Block-Triggered Pre-compute** — subscribe to new blocks via WebSocket, refresh calldata cache for all watchlist positions on every block.

## Architecture

Two new modules:
- `precompute_eth.py` — ETH pre-computation (calldata, swap paths, gas)
- `precompute_sol.py` — SOL pre-computation (instructions, Jupiter routes, Jito tips)

Integration points:
- ETH: `sweep_users()` calls `precompute_eth.get(user)` instead of computing on-the-fly
- SOL: `scan_solend()` calls `precompute_sol.get(obligation)` instead of computing on-the-fly

## ETH Pre-compute Cache

### Cache Structure

```python
eth_cache = {
    "0x14bc...": {
        "protocol": "morpho",
        "calldata": "0x...",           # encoded liq_args
        "selector": "0xd8eabcb8",      # liquidate selector
        "swap_path": "0x...",           # encoded Uni V3 path
        "gas_limit": 1500000,
        "estimated_profit_usd": 42.0,
        "flash_amount": "1000000000",   # wei
        "debt_token": "0xA0b86991...",
        "coll_token": "0xC02aaA39...",
        "updated_block": 25791500,
        "live_ok": True,
    }
}
```

### What Gets Pre-computed

| Item | Current Latency | Pre-computed |
|------|----------------|--------------|
| Uni V3 pool lookup (`getPool()`) | ~100ms | Cached at block N |
| Calldata encoding (`liq_args`) | ~50ms | Pre-built |
| Gas estimation | ~80ms | Pre-computed |
| Flash amount calculation | ~20ms | Pre-computed |
| Profit estimation | ~30ms | Pre-computed |
| **Total saved** | **~280ms** | |

### What Stays Real-Time

| Item | Latency | Why |
|------|---------|-----|
| Nonce | ~20ms | Changes every tx |
| Blockhash | ~5ms | Changes every block |
| Gas price | ~10ms | Changes every block |
| Signing | ~20ms | Needs keystore |
| Bundle spray | ~30ms | Needs latest block |
| **Total real-time** | **~85ms** | |

### ETH Block Listener

Subscribe to new blocks via WebSocket (`eth_subscribe` with `newHeads`). On each block:
1. Get latest block number
2. For each position in watchlist:
   - If position is hot (hf < 1.05) or was hot in last N blocks:
     - Resolve Uni V3 swap path (cache pool addresses)
     - Encode calldata (liq_args)
     - Estimate gas
     - Calculate flash amount
     - Estimate profit
     - Store in cache
3. Evict stale entries (not refreshed in 3+ blocks)

Block time: ~12s. Cache refreshes every 12s.

## SOL Pre-compute Cache

### Cache Structure

```python
sol_cache = {
    "obligation_address": {
        "kind": "liq",
        "repay_reserve": "...",
        "withdraw_reserve": "...",
        "repay_mint": "...",
        "withdraw_mint": "...",
        "debt_amount": 844200000,        # lamports
        "compute_units": 400000,
        "priority_fee_ul": 50000,
        "jito_tip_lamports": 50000,
        "instruction_sequence": [...],   # pre-built Solend ixs
        "account_metas": [...],          # pre-resolved accounts
        "jupiter_route": "...",          # cached swap route
        "updated_slot": 440333000,
        "estimated_profit_usd": 42.0,
    }
}
```

### What Gets Pre-computed

| Item | Current Latency | Pre-computed |
|------|----------------|--------------|
| Instruction building | ~30ms | Pre-built |
| Account meta resolution | ~20ms | Pre-resolved |
| Compute unit calculation | ~10ms | Pre-computed |
| Jupiter route caching | ~150ms | Cached route |
| **Total saved** | **~210ms** | |

### What Stays Real-Time

| Item | Latency | Why |
|------|---------|-----|
| Blockhash | ~5ms | Changes every slot |
| Jupiter exact quote | ~100ms | Needs current price |
| Simulation | ~200ms | Validates on-chain |
| Jito tip calc | ~10ms | Depends on profit |
| **Total real-time** | **~315ms** | |

### SOL Block Listener

Subscribe to slot updates via WebSocket. On each slot:
1. Get latest slot
2. For each obligation in watchlist:
   - If obligation is hot (hf < 1.05):
     - Build instruction sequence (refresh, borrow, liquidate, repay)
     - Resolve account metas
     - Calculate compute units
     - Cache Jupiter swap route
     - Store in cache
3. Evict stale entries

Slot time: ~400ms. Cache refreshes every 400ms.

## Integration with Existing Code

### ETH Changes

- `liquidation_bot.py`: `build_plan()` checks `precompute_eth.get(user)` first
- `mev_liquidation.py`: `build_full_plan()` uses cached calldata if available
- `eth_lending/executor.py`: Skip `plan_aave_like()` / `plan_comet()` / `plan_morpho()` if cached

### SOL Changes

- `sol_scanner.py`: `build_liq_plan()` checks `precompute_sol.get(obligation)` first
- `_live_send_liq_flash()`: Use cached instruction sequence + Jupiter route

### Dashboard Changes

- Show cache status (hit rate, last refresh, positions cached)
- Show pre-computed profit estimates vs actual profit

## Error Handling

- If cache miss → fall back to on-the-fly computation (existing path)
- If WebSocket disconnects → reconnect with exponential backoff
- If RPC fails during pre-compute → skip that position, log warning
- If cached calldata is stale (>3 blocks old) → evict and re-compute

## Testing

1. Unit tests for cache get/set/evict logic
2. Integration test: verify pre-computed calldata matches on-the-fly computation
3. Latency test: measure time from "opportunity detected" to "bundle submitted" with and without pre-compute
4. Live test: deploy to dashboard, monitor cache hit rate and submission latency
