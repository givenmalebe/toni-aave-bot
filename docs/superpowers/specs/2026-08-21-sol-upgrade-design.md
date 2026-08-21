# SOL Bot Upgrade — Win the Long Tail (Solend + Kamino)

Date: 2026-08-21
Status: Approved (design sections approved in conversation)

## Context

Wallet funding (~$50 ≈ 0.55 SOL) lands tomorrow. Decision: **arm immediately** once funded.
Research verdict (2026 landscape): pros dominate contested slots (Jito tips 50–70% of profit,
Geyser/ShredStream <50ms detection) but **skip small obligations** where tips exceed profit.
Our lane: uncontested long-tail liquidations on Solend + Kamino main markets, won by being
present with correctly-priced bundles, not by outbidding pros.

Existing assets already built:
- Full Jito bundle submission (`_jito_send_bundle`, 5 regional block engines)
- Solend flash-borrow liquidation (`_live_send_liq_flash`) and Kamino liquidate-and-redeem
  (`_live_send_kamino`) execution paths
- Dynamic tip share (15% of pre-tip), competitor tip tracking, Jupiter swap routing
- Event feed (`feeds/sol_feed.py`) with sharding/health — but Solend-only subscriptions
- `_sol_hot_kick` event set by feed — but **no consumer**

## Gaps addressed

1. Tips mispriced: base default 0.00001 SOL vs market norm 0.001–0.01; no live data.
2. Fast trigger missing: kick set but never consumed; detection-to-bundle latency = scan cycle.
3. Feed watches Solend only; Kamino obligations polled every scan cycle instead.
4. No arm-immediately guardrails: a bug could drain 0.55 SOL fast.

## Design

### 1. Tip & fee calibration — new module `sol_fees.py`
- Poll `https://bundles.jito.wtf/api/v1/tip_floor` every 60s → rolling P25/P50/P75/P95 lamports.
- Adaptive tip: P50 normally; escalate to P75 when competitor-tip tracker shows recent liq
  activity in our markets; hard cap P95. Never exceed 30% of pre-tip profit nor an absolute
  per-bundle cap (default 0.005 SOL).
- Dynamic priority fee: percentile of recent block CU prices (existing tracker), floor
  1_000 µlam/CU, capped by profit headroom.
- Fallbacks: stale values on API failure; conservative 0.0001 SOL default before first fetch.

### 2. Fast trigger path (feed → signed bundle ~1s)
- `feeds/sol_feed.py`: add `KAMINO_PROGRAM`; shard subscriptions across both programs.
- `feeds/registry.py`: Solana pubkey set built from both protocols' precompute caches.
- New dashboard loop `_sol_hot_executor()` consumes `_sol_hot_kick`:
  debounce 250ms → fresh plan per kicked obligation → free `simulateBundle` →
  profit floor + guardrails → send via existing `_live_send_liq_flash` / `_live_send_kamino`.
  Sim-fail or unfunded → drop silently, count in shadow stats.
- Scan loop stays as safety net.

### 3. Guardrails (arm-immediately safety)
- Per-attempt cap: tip+prio ≤ 30% of pre-tip profit AND ≤ 0.005 SOL absolute.
- Daily loss circuit breaker: realized losses ≥ −0.05 SOL / rolling 24h → auto-pause SOL
  execution to sim_only, red UI pill, manual re-arm required.
- Float floor: bot wallet < 0.2 SOL → refuse bundle sends.
- Profit floor: min net $1.50/attempt initially (env-tunable); edge_bias stays on.
- Every bundle simulated before send (failed sims are free).

## Non-goals
- Paid infra (Geyser/ShredStream/gRPC, Astralane/Lil-JIT relays) — $0 constraint stands.
- New protocols beyond Solend + Kamino main markets.
- ETH-side changes.

## Testing
- Unit: tip percentile math, escalation, circuit breaker state machine, Kamino feed
  sharding, kick debounce/coalescing (fake WS/RPC per repo conventions).
- Integration: fake block engine asserts sendBundle payload shape; sim-fail never sends.
- Live smoke on funding day: fund → wallet pills → first real bundles on Jito explorer →
  breaker counters observable.
