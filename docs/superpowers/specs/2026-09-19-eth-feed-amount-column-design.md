# ETH Live-Feed "Amount Liquidated" Column

## Goal

Add one column to the ETH competitor live-feed table (`#comp-table`) showing the
USD size of each confirmed liquidation so the operator can see who is moving
real money versus dust.

## Definition

The amount = **debt repaid in USD** (the principal the liquidator covered).

- **Primary basis:** USD value of `debt_to_cover` for the debt token, priced via
  the existing `lb.amount_usd(addr, amount)` (Chainlink feeds + stable fallback).
- **Fallback basis:** when the debt side is 0 or unpricable (e.g. Compound
  collateral-only `liquidateCollateral` events), fall back to the USD value of
  the collateral seized (`coll_seized`).
- **None:** neither side can be priced → cell renders `—` (`fmtHonestUsd`).
- Prices are live/current feeds, consistent with the existing "our est" column.

## Data model

`dashboard.py:_record_competitor_event` currently stores `debt_to_cover` (raw)
and `coll_usd` on each feed row. Change it to also store:

- `coll_seized`: raw collateral units (parsed event already carries it; today it
  is dropped).
- `amt_usd`: the displayed amount, computed by a new helper
  `liq_amount_usd(parsed) -> (amt_usd | None, basis)` where `basis` is
  `"debt"` or `"coll"`.
- `amt_basis`: `"debt"` | `"coll"`.

`amt_usd` is computed once at record time and carried through the API/feed as-is.

## UI

- `static/index.html`: add `<th title="debt covered in USD; ≈ = collateral-seized fallback">amount</th>`
  right after the `pair` column.
- `static/app.js` `renderCompFeed`: render `fmt.usd(c.amt_usd)`; prefix `≈` when
  `amt_basis === "coll"`; `—` when null. Tooltip shows the basis.

## Testing

Extract `liq_amount_usd(ev) -> (amt_usd | None, basis)` into
`aave-v4-liquidation-bot/liquidation_bot.py`, resolving addresses through the
existing `ADDR_BY_SYM` map and pricing through the existing `token_decimals` /
`asset_price_usd` / `amount_usd` helpers. Add a unit test in `tests/` beside
`test_liq_event_profit.py`, using the same `fake_prices` monkeypatch fixture,
covering:

1. Debt priced → amount = debt USD, basis `"debt"`.
2. Debt 0 / unpricable, collateral priced → amount = coll USD, basis `"coll"`.
3. Neither priced → `(None, None)`.

## Out of scope

- Historical re-pricing of already-recorded feed rows (rows record values at
  ingest time only).
- Layout/density changes beyond one column.