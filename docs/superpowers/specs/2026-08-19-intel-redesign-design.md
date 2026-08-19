# Learning / Intel Section Redesign — Liquidation Focus

## Overview

Refine the Learning / Intel section to focus on liquidations and trading. Split the current single card into two side-by-side cards: **Liquidation Intel** (primary) and **Trading Intel** (secondary). Both ETH and SOL tabs get this layout.

## Approach

Reuse the existing `intel_loop` and `intel_collector` pipeline. Enrich the snapshot with new liquidation-specific fields. No new backend modules — just expand what's already collected.

---

## Layout

```
[ Liquidation Intel (span-3) ] [ Trading Intel (span-3) ]
```

Both cards are full-height, side-by-side in the existing grid.

---

## Liquidation Intel Card

### Hero Stats Row

| Stat | Label | Source |
|------|-------|--------|
| `liq-volume` | Total Volume | `intel.liq_intel.volume_24h` formatted as `$XXk` |
| `liq-count` | Liq Count | `intel.liq_intel.count_24h` |
| `liq-avg` | Avg Size | `intel.liq_intel.avg_size` formatted as `$XXk` |
| `liq-gas` | Gas/Liq | `intel.liq_intel.gas_per_liq` formatted as `$X.XX` |

### Protocol Breakdown Bar

Horizontal segmented bar showing per-protocol liquidation counts:
- Aave V3 (cyan)
- Compound V3 (green)
- Morpho (violet)
- Spark (amber)

Widths proportional to count. Below the bar, text labels with counts.

### Health Factor Distribution Chart

Bar chart (LightweightCharts bar series) with 4 buckets:
- `<1.0` (red) — already liquidatable
- `1.0–1.05` (amber) — critical
- `1.05–1.1` (green) — near threshold
- `>1.1` (dim) — healthy

Data from `intel.liq_intel.health_dist`.

### Competitor Activity

| Stat | Label | Source |
|------|-------|--------|
| `liq-comp-searchers` | Searchers | `intel.liq_intel.competitors.searchers` |
| `liq-comp-rate` | Success Rate | `intel.liq_intel.competitors.success_rate` as % |
| `liq-comp-missed` | Missed | `intel.liq_intel.competitors.missed` |

### Volume History Chart

Line chart (LightweightCharts line series) showing liq volume over last 24h.
- One data point per 5m candle (max 288 points)
- Cyan fill, rolling window
- Data from `intel.liq_intel.volume_history`

---

## Trading Intel Card

### Brain / Policy Panel

Keep existing brain knobs panel unchanged:
- Model name, confidence (color-coded), accuracy EMA, loss EMA
- Replay buffer size, min_liq_mult, cadence_mult, prefer_edge
- Advice text

### Paper Trading Performance

New subsection showing paper bot stats:
- Balance (from `paper_eth.balance` / `paper_sol.balance`)
- PnL (color-coded: green positive, red negative)
- W/L record
- Win %
- Trade count

### Charts (Keep Existing)

| Chart | Source |
|-------|--------|
| Hours activity (24h) | `intel.hours` bar chart |
| Weekday activity (7d) | `intel.dows` bar chart |
| Act P trend line | `intel.brain.act_prob` rolling history |

---

## Backend Data Model

### New fields in `state["intel"]`

```python
"liq_intel": {
    "volume_24h": 0.0,
    "count_24h": 0,
    "avg_size": 0.0,
    "gas_per_liq": 0.0,
    "protocols": {
        "aave_v3": {"count": 0, "volume": 0.0},
        "compound_v3": {"count": 0, "volume": 0.0},
        "morpho": {"count": 0, "volume": 0.0},
        "spark": {"count": 0, "volume": 0.0}
    },
    "health_dist": {
        "<1.0": 0, "1.0-1.05": 0, "1.05-1.1": 0, ">1.1": 0
    },
    "competitors": {
        "searchers": 0,
        "success_rate": 0.0,
        "missed": 0
    },
    "volume_history": []
}
```

### Collection logic (in `intel_loop`)

1. **Protocol breakdown:** From `spoke_txs()` results — each liquidation call has a `proto` field from `_LENDING_ADDRS`. Aggregate counts and amounts per protocol.

2. **Gas per liq:** From `tx.gas_used * tx.gas_price / 1e18 * eth_price` for liquidation-classified transactions.

3. **Health factors:** Derived from on-chain health factor data where available (Aave V3 pools expose `getUserAccountData`). For protocols without direct health factor access, estimate from collateral/debt ratios.

4. **Competitor activity:** From `watch_txs()` — count unique searcher addresses in liquidation transactions. Success rate from ratio of confirmed vs attempted liquidations. Missed = liq opportunities seen in mempool but not taken.

5. **Volume history:** Rolling 24h array of `{ts, volume}` objects. Appended each `intel_loop` tick (every 75s). Max 288 entries.

---

## Files Changed

| File | Change |
|------|--------|
| `static/index.html` | Replace single `card-intel` with two side-by-side cards; add new element IDs |
| `static/app.js` | Add `updateLiqIntel(s)` and `updateTradingIntel(s)` functions; initialize new charts; wire into `render()` |
| `static/style.css` | Add styles for `.liq-intel-*` classes |
| `dashboard.py` | Extend `intel_loop` to compute `liq_intel` fields; add to snapshot |
| `intel_collector.py` | Add protocol-specific liquidation aggregation functions |

---

## SOL Tab

Same layout as ETH, with `sol-` prefixed element IDs. SOL intel uses the same `liq_intel` data shape but sourced from `sol_intel_loop` and Solend-specific data.

---

## Open Questions

None — all design decisions finalized.
