# SOL Race Mode — Learn to Race Pro Bots (design)

Date: 2026-09-06
Status: implemented (140 tests passing). User delegated design decisions; this
spec reflects what was built.

## Problem

The SOL liquidation pipeline refuses every contested pro-race at the submit layer:

1. `dashboard.py:1399-1408` — `sol_edge_bias` skips contested non-edge opps.
2. `dashboard.py:1409-1417` + `sol_scanner.py:8144-8150` — `is_contested_obligation`
   gate. Bug: both callers pass `current_slot=None`, and `record_competitor_liq`
   stores `max(prev, slot)`, so once an obligation is contested it stays contested
   forever for the process lifetime (`SOL_CONTESTED_SLOTS=50` is dead).
3. `dashboard.py:1418-1428` — tip-erasure gate trips by design during races because
   `_dynamic_jito_lamports` bids `competitor_p95 * 1.15`.
4. `dashboard.py:3931-3933` — single-shot fire: a skipped contested opp consumes the
   only submission slot of the sweep cycle.

Meanwhile the SOL profit-brain (profit_brain.py, residual MLP) cannot learn anything
about races:

- SOL features are macro-only (`features_from_sol_state`, 16 dims). No HF, debt size,
  collateral, protocol, competitor-count, tip p95.
- SOL brain only ever sees `stage="skip"` labels from contested races
  (`_sol_record` → `learn_sol_broadcast` labels skip as act=0). It has zero
  won/lost race outcomes feed.
- No decision-time opponent outcome is recorded (ETH has `learn_competitor`; SOL
  has nothing).

## Goal

Let the bot actually race pro bots *in a bounded, data-rich way* so the SOL MLP learns
real race dynamics from real won/lost outcomes. When funding + arm + sim gates allow,
contested safe fires go out; when the daily tuition cap is spent, racing stops and the
thin/edge path continues untouched.

## Design

### 1. Race mode config (env)

- `SOL_RACE_MODE` ("0" default in code; set "1" in `.env`).
- `SOL_RACE_TUITION_DAY_SOL` (0.10) — max tip $ spent racing per UTC day.
- `SOL_RACE_TIP_CAP_SOL` (0.002) — max tip per single race bundle.
- `SOL_RACE_MAX_FIRES_PER_CYCLE` (3) — sweep-loop race fires per cycle.
- `SOL_CONTESTED_SLOTS` (existing 50) — contest window actually enforced.

Dashboard state: `race_mode`, `race_tuition_spent_sol` (reset daily), day key,
`race_last` (recent outcomes), `race_inflight` (in-flight race ledger).

### 2. Fix the permanent contested gate

- `is_contested_obligation(obl, current_slot=None)`:
  - if `current_slot is None` → return True (conservative, unchanged behavior).
  - else window = `int(current_slot) - int(rec) <= SOL_CONTESTED_SLOTS`.
- `record_competitor_liq(k, slot)` unchanged (stores max slot).
- Callers pass a real current slot:
  - `dashboard.py:1410` — get `current_slot` from the recent sol state
    (sol intel loop already stores a slot value; fall back to slot listener).
  - `sol_scanner.py:8145` — same.
- Behavior note: this gate now *expires*; an obligation returns to raceable after 50
  slots of no contest.

### 3. Loosen fire gates in race mode (tuition-aware)

In `_sol_maybe_submit` (dashboard.py) when `race_mode` and wallet/arm/sim pass:

- Edge-bias contested skip: **skip** only if race tuition is exhausted (`tuition
  spent >= cap`) or tip exceeds `_race_tip_lam` cap. Otherwise allow.
- `is_contested_obligation`: allow when outside the (now real) 50-slot window.
- Tip-erasure gate: allow if `tip_usd < profit` OR `tip_lam <= cap` (still refuse a
  tip that erases net entirely and blows the cap).
- All race-mode fires: increment `race_tuition_spent_sol` by `tip_usd`; record
  `stage="race"` → dashboard shows RACE pill.

### 4. Sweep loop: iterate actionables

Replace `fire = next((o for o in opps if o.get("actionable")), None)` (dashboard.py
:3931-3933 and :3640-3644) with a loop over `opps` until the first *submitted* fire,
capped at `SOL_RACE_MAX_FIRES_PER_CYCLE` submissions per cycle. A dropped/skipped race
no longer swallows the thin/edge shot.

### 5. SOL brain: per-position features + real race outcomes

- `features_from_sol_state` 16 → 24 dims. New features (position-level, passed via the
  broadcast record / opp):
  - hf (clamped 0..1.1), log1p(debt_usd)/10, collateral-sym long-tail bit,
    protocol one-hot (solend/kamino/marginfi), competitor count in window on this
    obligation (0..8 clamped /8), tip p95 (x 1e9 lamports / 1e6), edge bit, race-mode
    and race-held bit.
- Add `SOL_FEAT_VERSION` (constant) to the SOL twin persistence blob; on load, if
  version mismatch → reset weights/replay (fresh warmup). Current SOL state
  (`acc=1.0`, 3049 steps) is overfit on skip-only labels; the reset is a fix.
- `learn_sol_broadcast`/`learn_sol_race`: pass position features + stage-derived label:
  - `won` (our bundle confirmed landed, no competitor landed first)
  - `lost` (competitor landed on the obligation while race inflight)
  - `no-contest` (no outcome within TTL) → label act=0.5, profit=0 (soft).
- Persist each race outcome row to `data/sol_race_outcomes.jsonl` (rebuildable
  training set).

### 6. Outcome labeling pipeline

- In-flight race ledger: `race_inflight[obl] = {sent_ts, tip_sol, plan, features,
  sig}`.
- Confirmation path (`_live_send_*` → `_jito_confirm_bundle`/`_confirm_sol_tx`):
  on landed → `learn_sol_race(...) act=1.0, profit=net`; pop ledger.
- Landing watcher (`watch_solend_landing` competitor observation): if the obligation
  is in `race_inflight` and it isn't our own sig → `act=0.0`, profit=0, pop ledger.
- A periodic reap pops entries older than the presign TTL (120s) as no-contest.

### 7. UI

- dashboard state: `race_mode`, `race_tuition_spent_sol` / cap, `race_last` outcomes,
  `race_inflight` count.
- `static/app.js`: add RACE pill (yellow) for `stage==="race"` rows and a tuition
  meter in the SOL header (spent/cap SOL).

### 8. Testing

- Contested-window expiry: `is_contested_obligation` false when outside window.
- Race-mode permit: contested, non-edge opp fires when race mode on + under tuition.
- Tuition cap: contested opp skipped once cap spent.
- Feature vector length 24 + version-reset on mismatch.
- Outcome labels: confirm-landed → won label; competitor-before-us → lost label.
- Full suite stays green (race mode defaults OFF in code).

### 8. Semantics refinement during build

- Env overrides added to `.env`: `SOL_RACE_MODE=1`, `SOL_RACE_TUITION_DAY_SOL=0.10`,
  `SOL_RACE_TIP_CAP_SOL=0.002`, `SOL_RACE_MAX_FIRES_PER_CYCLE=3`,
  `SOL_CONTESTED_SLOTS=50`.
- `current_slot` is threaded from `plan["_slot"]` (presign slot) falling back to the
  dashboard's cached `sol["slot"]` (refresh every 35s), then passed through
  `submit_sol_plan(..., current_slot=...)` so both fire-time gates see the same slot.
- `learn_sol_broadcast` treats *simulated contested* attempts as neutral (act=0.5,
  profit=0) so the pre-funding sim-only period can't teach the model false confidence
  about races.

### 9. Adaptive outbidding bandit (bidding layer)

Built on top of race mode so the bot *outbids* competitors instead of guessing.

- **Two armed multipliers** (`calm` / `contested`), persisted to
  `data/sol_tip_bandit.json` so pressure learned from prior days survives
  restarts.
- **Bid formula**: `min(max(min_formula, comp_p95 * 1.15 * mult), pre - floor)`.
  The multiplier is applied *inside* `_dynamic_jito_lamports`, the single
  choke-point used by every plan builder and the arb path, so no re-signing is
  needed — the tip tx is rebuilt fresh at every fire from the plan value.
- **Arm selection**: competitor tips present (`comp_tip_p95() > 0`) = we are in
  a bidding contest → `contested` arm; otherwise `calm`. Contested state from
  the mempool is *not* carried into `_dynamic_jito_lamports` (pressure strings
  are "hot/elevated/busy"), so competitor presence is the honest contest proxy.
- **Learning** (`update_tip_mult`): won → `mult *= 0.9` (floor 0.7); lost →
  `mult *= 1.25` (cap 3.0). Trained from `_sol_race_adjudicate` using the real
  outcome (`won`/`lost`/`no-contest`) and the tip actually paid. Escalation
  means the bot keeps raising its offer until it beats the competition or hits
  the gross - floor cap / race tuition cap.
- **Guards unchanged**: never bid past `pre - floor`; race spend still bounded
  by daily tuition; tip cap per race.

### 10. ETH outbidding (parity, with ML)

Same adaptive-bid pattern ported to Ethereum, where the bid is the EIP-1559
priority-fee auction rather than a Jito tip.

- **Bid bandit** (`gas_bidder.py`): persisted `calm`/`contested` multipliers in
  `data/eth_bid_bandit.json`; escalates `*1.25` on lost races, eases `*0.9` on
  wins, bounds `[0.7, 3.0]`. Applied both in `GasBiddingEngine.calculate_bid`
  (scales `max_priority_fee_per_gas`, including the gas-cost-capped branch) and
  in `build_flash_liq_plan` as `prio_mult = race_prio_mult(why) * eth_bid_mult(contested)`.
- **Inflight ledger** (`dashboard.py`): real funded fires register a
  user-keyed entry with target block + `prio_mult`. The competitor log scanner
  adjudicates: landing on/at our target block → `won`; later → `lost`;
  reaper after 60 blocks → `no-contest`. No-contest never moves the bandit (no
  under-bid signal).
- **ML** (`profit_brain.py`): ETH brain raised to `FEAT_DIM=28` with
  per-position indices 20..27 (debt$, profit, gas relative to state, edge,
  protocol, contested, bid, involvement) and `FEAT_VERSION=1` version-keyed
  reset (stale 20-dim weights reset once). `learn_eth_race` trains won/lost/
  no-contest (act=1/0/0.5) with signed profit and appends real outcomes to
  `data/eth_race_outcomes.jsonl`.
- **Guard rails**: profit-floor, `min_profit_usd`, and `max_gas_cost_eth` all
  still cap the bid; the bandit operates inside those bounds.

### 11. AI Manager (autonomous profit optimizer)

Chat + tool-use layer (`ai_manager.py`) with a curated Power BI panel in the
app.

- **LLM**: OpenRouter `minimax/minimax-m3:free` (`OPENROUTER_API_KEY`), plain
  JSON output contract (`{"action":...}` / `{"reply":...}`) so any model
  works without function-calling support.
- **Tools** (seed): `get_snapshot`, `get_bi`, `list_tools`, `list_skills`,
  `read_config`, `apply_change`, `revert_change`, `list_changes`,
  `create_tool`, `create_skill`, `run_command` (allowlisted: pytest/rg/cat/
  type/dir/etc.), `spawn_agent`, `read_file`, `add_memory`.
- **Safety**: fund-gating env knobs (`FUND_KNOBS`: race tuition, tip caps,
  profit floors, broadcast/armed/keep-live, contract addrs) require human
  approval via the app; every mutation is journaled
  (`data/manager_change_journal.jsonl`) and reversible.
- **Tool/skill authoring**: the manager persists new tools (`data/ai_tools/*.json`)
  and skills (`data/ai_skills/*.json`); created tools only run allowlisted
  commands; created skills are declarative step lists.
- **Agents**: `spawn_agent` runs bounded in-process sub-orchestrators
  (goal + allowed tools + step budget) sharing the tool registry.
- **Persistence**: `data/manager_state.json` (threads, memory, agents).
- **Endpoints**: `POST /api/manager/chat`, `GET /api/manager/status`,
  `GET /api/manager/bi`, `POST /api/manager/agent`,
  `POST /api/manager/approve`.
- **App tab**: AI Manager workspace with chat, Power BI (ETH/SOL race
  win-rate + realized profit charts, competitor counts, model health =
  feat_dim/version/steps/loss), pending approvals, recent changes, and
  sub-agent spawn.

## Out of scope

- No changes to ETH race behavior (untouched).
- No new network/broadcast infra (uses existing Jito + confirm paths).
- No tip-auction modeling; flat cap only.
- MLP architecture/backprop untouched — only feature/label feed.

## Honest expectations

- Racing pro bots burns tip $; most races are lost. Tuition cap bounds the burn.
- The ML signal is real only because labels are real (won/lost via landing),
  unlike today's sent-vs-skip.
- Expected effect: after sufficient tuition-fed outcomes, the brain's
  `p_act`/`exp_net` for contest-rich states reflects true win probability, making
  the thin/edge-only default *better* at knowing when to stay out.