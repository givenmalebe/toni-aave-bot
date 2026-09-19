# ETH Live-Feed "Amount Liquidated" Column Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one "amount" column to the ETH competitor live-feed table showing the USD principal liquidated (debt covered, with collateral-seized fallback).

**Architecture:** A pure-ish helper `liq_amount_usd(ev)` in `aave-v4-liquidation-bot/liquidation_bot.py` resolves token addresses via `ADDR_BY_SYM` and prices via existing `token_decimals`/`asset_price_usd`/`amount_usd`. The dashboard records `amt_usd` + `amt_basis` per competitor event at ingest time; the existing feed API passes them through untouched. The web UI renders one new column.

**Tech Stack:** Python 3 (asyncio dashboard), existing `liquidation_bot` helpers, vanilla JS feed renderer in `static/app.js`.

## Global Constraints

- Amount = **debt repaid in USD** (`debt_to_cover`); fallback **collateral seized USD** (`coll_seized`) only when debt side is 0 or unpricable; `None` when neither side prices.
- New column header goes immediately **after the `pair` column** in `#comp-table`.
- `amt_basis` is `"debt"` | `"coll"`. When `"coll"`, the cell shows an `≈` prefix. `None` amount renders `—`.
- No re-pricing of already-recorded rows; values are computed once at record time.
- Follow existing code style: no new comments beyond what neighboring code already uses.

---

### Task 1: `liq_amount_usd` helper + unit tests

**Files:**
- Modify: `aave-v4-liquidation-bot/liquidation_bot.py` (insert after `liq_event_profit`, which ends at line 855)
- Test: `tests/test_liq_amount_usd.py` (new)

**Interfaces:**
- Consumes: `lb.amount_usd(addr, amount:int) -> float`, `lb.ADDR_BY_SYM` (dict `str->str`, uppercase symbol key), `ev` fields `debt_to_cover`/`debt_restored`, `coll_seized`/`coll_to_liq`, `debt_addr`/`debt_sym`, `coll_addr`/`coll_sym`.
- Produces: `lb.liq_amount_usd(ev: dict) -> tuple[float | None, str | None]` returning `(rounded_usd, "debt" | "coll")` or `(None, None)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_liq_amount_usd.py`:

```python
"""liq_amount_usd: USD principal liquidated = debt repaid, coll fallback."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "aave-v4-liquidation-bot"))

import pytest

import liquidation_bot as lb

USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
USDT = "0xdac17f958d2ee523a2206206994597c13d831ec7"
WETH = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"


@pytest.fixture(autouse=True)
def fake_prices(monkeypatch):
    def fake_decimals(addr):
        return {USDC: 6, USDT: 6, WETH: 18}.get((addr or "").lower(), 18)

    def fake_price(addr):
        return {
            USDC: 1.0, USDT: 1.0, WETH: 3500.0,
        }.get((addr or "").lower(), 0.0)

    monkeypatch.setattr(lb, "token_decimals", fake_decimals)
    monkeypatch.setattr(lb, "asset_price_usd", fake_price)


def test_debt_priced_primary_basis():
    ev = {"debt_addr": USDC, "debt_to_cover": int(100_000_000)}
    amt, basis = lb.liq_amount_usd(ev)
    assert amt == 100.0
    assert basis == "debt"


def test_debt_unpriced_falls_back_to_collateral():
    ev = {"debt_sym": "ZZZZ", "debt_to_cover": 123,
          "coll_sym": "USDC", "coll_seized": int(105 * 1e6)}
    amt, basis = lb.liq_amount_usd(ev)
    assert amt == 105.0
    assert basis == "coll"


def test_zero_debt_falls_back_to_collateral():
    ev = {"debt_addr": USDC, "debt_to_cover": 0,
          "coll_addr": USDC, "coll_seized": int(105 * 1e6)}
    amt, basis = lb.liq_amount_usd(ev)
    assert amt == 105.0
    assert basis == "coll"


def test_weth_debt_priced():
    ev = {"debt_addr": WETH, "debt_to_cover": int(1 * 1e18)}
    amt, basis = lb.liq_amount_usd(ev)
    assert amt == 3500.0
    assert basis == "debt"


def test_nothing_priceable_returns_none():
    ev = {"coll_sym": "ZZZZ", "debt_sym": "XYZ9",
          "coll_to_liq": 123, "debt_to_cover": 456}
    assert lb.liq_amount_usd(ev) == (None, None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_liq_amount_usd.py -v`
Expected: FAIL with `AttributeError: module 'liquidation_bot' has no attribute 'liq_amount_usd'`

- [ ] **Step 3: Write the minimal implementation**

Insert into `aave-v4-liquidation-bot/liquidation_bot.py` immediately after the end of `liq_event_profit` (after line 855):

```python
def liq_amount_usd(ev: dict) -> tuple[float | None, str | None]:
    """USD principal liquidated: debt covered, collateral-seized fallback."""
    debt_amt = ev.get("debt_to_cover") or ev.get("debt_restored") or 0
    coll_amt = ev.get("coll_seized") or ev.get("coll_to_liq") or 0
    debt_addr = ev.get("debt_addr") or ADDR_BY_SYM.get(
        str(ev.get("debt_sym") or "").upper())
    if debt_amt and debt_addr:
        debt_usd = amount_usd(debt_addr, debt_amt)
        if debt_usd > 0:
            return round(debt_usd, 2), "debt"
    coll_addr = ev.get("coll_addr") or ADDR_BY_SYM.get(
        str(ev.get("coll_sym") or "").upper())
    if coll_amt and coll_addr:
        coll_usd = amount_usd(coll_addr, coll_amt)
        if coll_usd > 0:
            return round(coll_usd, 2), "coll"
    return None, None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_liq_amount_usd.py -v`
Expected: all 5 PASS. Also run `python -m pytest tests/test_liq_event_profit.py -v` to confirm no regression.

- [ ] **Step 5: Commit**

```bash
git add aave-v4-liquidation-bot/liquidation_bot.py tests/test_liq_amount_usd.py
git commit -m "feat: liq_amount_usd helper for feed amount column"
```

---

### Task 2: Record `amt_usd` + `amt_basis` on competitor events

**Files:**
- Modify: `dashboard.py:3482-3512` (the `rec = {...}` dict in `_record_competitor_event`)

**Interfaces:**
- Consumes: `lb.liq_amount_usd(ev)` from Task 1.
- Produces: feed rows now carry `amt_usd: float | None`, `amt_basis: "debt" | "coll" | None`, `coll_seized: str`. These flow unchanged through the existing API/feed path.

- [ ] **Step 1: Write the failing test**

There is currently no unit test for `_record_competitor_event` (it is coupled to RPC). This task is verified by code review + the pre-existing feed integration flow; the TDD gate is instead enforced at Task 1's helper and Task 3's UI. Proceed directly.

- [ ] **Step 2: Implement the recording change**

In `dashboard.py`, inside `_record_competitor_event` right before the `rec = {` block (after the `edge = ...` line at 3481), add:

```python
        amt, amt_basis = None, None
        try:
            amt, amt_basis = lb.liq_amount_usd(parsed)
        except Exception:
            amt, amt_basis = None, None
```

Then in the `rec` dict, after the `"debt_to_cover"` line (3493), add:

```python
            "coll_seized": str(parsed.get("coll_seized") or 0),
            "amt_usd": amt,
            "amt_basis": amt_basis,
```

- [ ] **Step 3: Run the feed integration flow to verify**

Restart the dashboard (`python dashboard.py --host 127.0.0.1 --port 8081 --broadcast`), open the ETH feed tab, and confirm competitor rows now include `amt_usd`/`amt_basis`/`coll_seized` by inspecting the state JSON. Expected: rows with a decodable debt (e.g. the Morpho/Aave rows that had `our est`) now show an amount; undecodable rows show `amt_usd: null`.

- [ ] **Step 4: Commit**

```bash
git add dashboard.py
git commit -m "feat: record liq amount + basis on competitor feed rows"
```

---

### Task 3: UI — amount column in `#comp-table`

**Files:**
- Modify: `static/index.html:355-361` (thead of `#comp-table`)
- Modify: `static/app.js:2331-2364` (`renderCompFeed` row template)

**Interfaces:**
- Consumes: feed row fields `amt_usd`, `amt_basis` from Task 2; `fmt.usd` from `static/app.js`.
- Produces: a working `amount` column rendering `$X.XX`, `≈$X.XX` for collateral fallback, `—` when null.

- [ ] **Step 1: Add the column header**

In `static/index.html`, inside the `#comp-table` thead, after the `<th>pair</th>` line, add:

```html
<th title="debt covered in USD; ≈ = collateral-seized fallback">amount</th>
```

- [ ] **Step 2: Render the amount cell**

In `static/app.js` `renderCompFeed`, in the row template (after the `<td><b>${pair}</b></td>` line at 2355), add:

```js
        <td title="${c.amt_basis === "coll" ? "collateral-seized fallback" : "debt covered in USD"}">${c.amt_usd != null ? (c.amt_basis === "coll" ? "≈" : "") + fmt.usd(c.amt_usd) : `<span class="dim">—</span>`}</td>
```

- [ ] **Step 3: Verify in browser**

Open `http://127.0.0.1:8081/`, ETH live-feed tab. Expected: an `amount` column sits between `pair` and `liquidator`. Rows where debt is priced show `$X.XX`; collateral-fallback rows show `≈$X.XX`; undecodable rows show `—`. Column order is `age | proto | pair | amount | liquidator | user | gas paid | our est | our net | flags | tx`.

- [ ] **Step 4: Commit**

```bash
git add static/index.html static/app.js
git commit -m "feat: show amount liquidated in ETH competitor feed"
```

---

## Self-Review Checklist

- **Spec coverage:** helper + basis (`Task 1`), record time wiring (`Task 2`), header + render (`Task 3`), tests (`Task 1`), out-of-scope not planned. — single plan scope confirmed.
- **Placeholder scan:** no TBD/TODO; every code step carries full code.
- **Type consistency:** `liq_amount_usd` returns `(float|None, str|None)` everywhere; fields named `amt_usd` / `amt_basis` / `coll_seized` consistently in Task 2, and consumed with the same names in Task 3.