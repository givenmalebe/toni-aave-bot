# SOL Feed "Collateral Seized" Column

## Goal

Add one column to the SOL Opportunities feed table (`#sol-opps-table` — the
"feed N/M in feed · HF<1 +EV" table) showing, per opportunity, the USD value of
the collateral the bot would seize, so the operator can see each candidate's
trade size at a glance without reading the dim `coll/debt·repay` sub-line.

## Definition

The amount = **collateral seized in USD**, exactly the value the scorer's own
model computes for the selected user debt (repay amount × liability-premium, or
the protocol-fee-discounted form on Solend/Drift).

- **Value source:** the existing `seized_usd` field already computed by all
  three SOL scorers and carried on every opportunity row:
  - Solend + Drift plan: `sol_scanner.py` `seized = repay * (1 + bonus * (1 - proto))`
  - Kamino: `sol_lending/kamino.py` `seized = repay * (1.0 + bonus)`
  - MarginFi: `sol_lending/marginfi.py` `seized = repay * (1.0 + bonus)`
- **None/zero:** `seized_usd` null or ≤ 0 → cell renders `—` (dim). Zero means
  no repayable debt, i.e. no trade.

## Data model

No backend change. `seized_usd` (float, rounded to 4dp) already flows through
the API (see `sol_scanner.py` opportunity JSON building) into
`sol.opportunities`. The row renderer reads `o.seized_usd`.

## UI

- `static/index.html` `#sol-opps-table` header: add
  `<th title="collateral seized USD">seized $</th>` right after
  `coll → debt`.
- `static/app.js` `renderSolOpps`: add one cell after the `coll → debt` cell
  rendering `fmt.usd(o.seized_usd)`, or dim `—` when null / ≤ 0. Leave the
  existing dim `coll / debt · repay` sub-line untouched.
- Resulting order: `user | proto | hf | coll → debt | seized $ | bonus | net $ | flags`.

## Testing

Front-end-only change; `seized_usd` is already produced by existing unit-tested
scorers. Verify manually against the live API: confirm
`/api/state` `sol.opportunities` rows carry `seized_usd`, then hard-refresh the
dashboard (Ctrl+F5, no-cache headers are absent) and confirm the column renders
`$X.XX` for sized opportunities and `—` for nulls.

## Out of scope

- SOL watch list and SOL "Competitor Liquidations" tables — unchanged.
- Any backend change or re-scoring; the column reuses the current sweep values.