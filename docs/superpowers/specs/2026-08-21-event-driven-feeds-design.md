# Event-Driven Liquidation Feeds — Design

**Date:** 2026-08-21
**Status:** Approved (Approach A at $0/mo budget)
**Goal:** Replace polling-based detection with event-driven feeds on ETH and SOL using free-tier infrastructure. Honest ceiling ~7/10 now; paid keys later slot into the same interfaces for 8/10.

## Problem

Live observation (2026-08-21) showed:
- ETH full scan pass takes 32.6s covering ~14 blocks; bot is 1–3 min behind head between passes. 344 sweeps → 0 opportunities.
- SOL sweep cycles 5–40s on public RPCs with constant failures (publicnode 403, "All SOL RPCs failed" bursts); landing-watcher only ever sees competitor liquidations after they land (`hf<1=0` every cycle).
- Live bugs: SOL block listener crash loop (`'<' not supported between instances of 'str' and 'float'`), Morpho adapter `api.morpho.org` GraphQL HTTP 400 on every sweep.
- Precompute layers idle: ETH precompute `positions=0, misses=2202`.

## Architecture

Principle: every second-scale loop becomes an event subscription; polling stays as a safety net.

```
ETH (WSS ×2–3 free providers)          SOL (WS pubsub ×4 free providers)
  Chainlink AnswerUpdated ─┐             accountSubscribe obligations ─┐
  LiquidationCall ×4 proto─┤ eth_event_   logsSubscribe Solend/Kamino┤ sol_event_
  newHeads ────────────────┘ feed.py      slot stream (fixes crasher)  ┘ feed.py
            │ dedupe/health                            │ dedupe/health
            ▼                                          ▼
        feed_registry  ──── event → affected positions ────
            │                                          │
            ▼                                          ▼
   existing pipeline: HF recompute (cached balances + fresh price)
   → profit gate → precomputed calldata / _live_send_liq_flash()
   → stealth 7-builder spray / Jito bundle → execution_tracker → dashboard
```

Polling sweeps keep running at relaxed cadence underneath. If all feeds die the bot auto-degrades to today's behavior.

## Components

### New

1. **`eth_event_feed.py`**
   - WSS manager holding 2–3 parallel connections (default: Alchemy free tier, publicnode, dRPC free).
   - Subscriptions per connection: `eth_subscribe` logs for Chainlink aggregator `AnswerUpdated` topics (feeds resolved from lending protocol oracle configs at startup), `LiquidationCall` topics for Aave V3/V4, Spark, Compound, Morpho; `newHeads`.
   - Fan-out dedupe: first event wins; per-provider health score (success/fail counters, exponential bench on repeated failure).
   - Reconnect with jittered backoff; gap repair on reconnect = re-run a bounded `eth_getLogs` sweep over missed block range.
   - Emits normalized events into `feed_registry`.

2. **`sol_event_feed.py`**
   - WS pubsub manager fanned across 4 free providers (api.mainnet-beta.solana.com, publicnode, dRPC, Helius free). Obligations sharded across connections to respect per-connection subscription caps.
   - `accountSubscribe` on watchlist obligation pubkeys (Solend + Kamino); notification triggers decode via existing Solend/Kamino decoders.
   - `logsSubscribe` on Solend + Kamino program IDs (real-time landing intel replacing 28s signature polling as primary signal).
   - Slot stream replaces the crash-looping WS listener in `precompute_sol.py` path.
   - Same health/reconnect/dedupe machinery as ETH side (shared base class).

3. **`feed_registry`** (module-level, shared by both feeds)
   - ETH: map `oracle feed address → [position ids]`, built once at startup from watchlist + reserve/oracle config; positions carry cached balances.
   - SOL: map `obligation pubkey → decode+HF closure`; reserve/oracle cache refreshed by slot stream.
   - Pure functions, unit-testable without network.

### Modified

4. **`dashboard.py`**: start both feeds at boot behind `.env` flags (`ETH_FEED_ENABLED=1`, `SOL_FEED_ENABLED=1`); expose feed status in `/api/state` (`feeds: {eth: {mode: live|degraded|off, providers: [...], events_seen, last_event_ts}, sol: {...}}`); two status cards in UI; event-triggered fires flow through existing broadcast gates unchanged.
5. **`precompute_sol.py`**: fix `'<' str vs float` comparison in listener path (root cause: slot/timestamp values sometimes arrive as strings from JSON); harden types at ingress.
6. **Morpho adapter** (`eth_lending/morpho.py` or equivalent): fix GraphQL query causing HTTP 400 (schema drift — pin field set, add error body logging, graceful skip on API failure so sweep doesn't log errors every cycle).
7. **`.env.example`**: document new vars.

### Reused unchanged

`precompute_eth.py` plan layer (gets fed real positions now), `hybrid_executor` + `gas_bidder` + `backrun` + stealth spray, `sol_scanner._live_send_liq_flash()` builder + Jito sender + dynamic tips, `execution_tracker`, nonce manager, broadcast gates (sim_only/armed/funded).

## Data Flow (hot paths)

**ETH oracle tick (~sub-second):**
1. `AnswerUpdated(feed, price)` arrives → registry lookup → affected positions.
2. Recompute HF locally: cached balances + new price. No RPC calls.
3. HF<1 → pull precomputed liquidation plan from `precompute_eth` layer, substitute price, run profit gate ($10 floor).
4. Sign → bundle target N+1 → stealth spray → tracker log.

**SOL obligation update (~600ms):**
1. Account notification → decode obligation (existing decoders).
2. Local HF vs cached reserve/oracle state.
3. HF<1 & net>floor → `_live_send_liq_flash()` (Jupiter quote at build time is accepted $0-latency cost) → Jito bundle, dynamic tip from real-time competitor tip data.

## Error Handling

- Circuit breaker: provider failing N≥3 times in M=5min benched for 10min, auto-retry.
- Idempotency: dedupe fires by `(chain, position_id, trigger_block)`; ETH nonce manager orders txs; SOL uses fresh blockhash per send.
- Subscription sharding when watchlist exceeds per-connection caps.
- All existing sim_only/armed/funded gates apply to event-triggered fires.

## Testing

1. **Unit** (pytest, `tests/`): registry mapping, HF recompute vs fixtures, dedupe, reconnect/backoff state machine, provider health benching, SOL type-hardening regression test for the str/float crash.
2. **Integration**: Anvil mainnet fork — impersonate underwater position, push oracle update, assert bundle built and submitted to mocked relay. SOL: synthetic account-notification through real builder path (mocked RPC/Jito).
3. **Shadow mode (go/no-go gate)**: feeds live 1–2 weeks unarmed. Metric: % of competitor-landed liquidations we would have fired on within 2s vs polling baseline. Target ≥90% catch + 72h zero crash-loops before deploying contract / funding wallets.

## Non-goals

- Rust rewrite, paid Geyser/gRPC integration (interfaces reserved via provider URL swap), L2 adapters (follow-up), Kamino inventory execution wiring beyond current state, SVR/MEV-Share auction participation.

## Config surface (.env)

```
ETH_FEED_ENABLED=1
ETH_FEED_WSS_URLS=wss://...free1,wss://...free2
SOL_FEED_ENABLED=1
SOL_FEED_WS_URLS=wss://api.mainnet-beta.solana.com,wss://solana-rpc.publicnode.com/websockets,...
FEED_BENCH_THRESHOLD=3
FEED_BENCH_WINDOW_S=300
FEED_BENCH_SECONDS=600
```
