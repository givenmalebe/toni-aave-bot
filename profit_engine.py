#!/usr/bin/env python3
"""Profit-maximizing policy helpers for the TONI dashboard.

Pure functions + small stateful learning store. No RPC, no signing.
"""
from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

SAST_OFFSET = 2 * 3600  # UTC+2, no DST

# Long-tail symbols where public competition is thinner.
LONG_TAIL = {"EURC", "RLUSD", "FRAX", "GDOLLAR", "GHO", "cbBTC", "weETH", "wstETH"}

# ETH long-tail debt band: only target positions inside [min, max].
# Below the band gas/effort outweighs edge; above it, top-firm races are
# unwinnable and each failed attempt burns gas.
MIN_LIQ_DEBT_USD = float(os.environ.get("MIN_LIQ_DEBT_USD", "10000"))
MAX_LIQ_DEBT_USD = float(os.environ.get("MAX_LIQ_DEBT_USD", "500000"))

# Near-miss learning persistence
_NEAR_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "near_miss_learning.json")


def sast_hour(ts: Optional[float] = None) -> int:
    t = int(ts if ts is not None else time.time())
    return time.gmtime(t + SAST_OFFSET).tm_hour


def dynamic_min_liq_profit_usd(gas_gwei: Optional[float], base: float = 10.0) -> float:
    """Raise liq floor when gas is hot; relax when cheap."""
    g = float(gas_gwei or 0.0)
    if g <= 0.5:
        return max(3.0, base * 0.4)
    if g <= 5:
        return base
    if g <= 20:
        return base * 1.5
    if g <= 50:
        return base * 2.5
    return base * 4.0


def liq_debt_band_skip(debt_usd: Any,
                       min_usd: Optional[float] = None,
                       max_usd: Optional[float] = None) -> Tuple[bool, str]:
    """ETH long-tail band gate: target only debts in [min_usd, max_usd].

    Unknown/invalid debt passes through — the +EV profit floor still applies.
    """
    if debt_usd is None:
        return False, ""
    try:
        d = float(debt_usd)
    except (TypeError, ValueError):
        return False, ""
    if d <= 0:
        return False, ""
    lo = float(min_usd) if min_usd is not None else MIN_LIQ_DEBT_USD
    hi = float(max_usd) if max_usd is not None else MAX_LIQ_DEBT_USD
    if d < lo:
        return True, f"debt ${d:,.0f} < band min ${lo:,.0f}"
    if d > hi:
        return True, f"debt ${d:,.0f} > band max ${hi:,.0f}"
    return False, ""


def dynamic_min_arb_profit_usd(gas_gwei: Optional[float], eth_usd: Optional[float],
                               base: float = 5.0) -> float:
    """Arb floor ≈ base + estimated gas cost in USD (500k gas heuristic)."""
    g = float(gas_gwei or 1.0)
    eth = float(eth_usd or 2000.0)
    gas_usd = (500_000 * g * 1e-9) * eth
    if g <= 0.5:
        return max(1.0, base * 0.5 + gas_usd)
    return max(base, base * 0.75 + gas_usd)


def eth_arb_plus_ev(net_usd, min_usd: float) -> bool:
    """Live ETH arb gate: net after gas/fees/slip > 0 and at/above the floor."""
    try:
        net = float(net_usd)
        floor = float(min_usd or 0)
    except (TypeError, ValueError):
        return False
    return net > 0 and net >= floor


def is_peak_hour(hours: Dict[Any, int], now_h: Optional[int] = None,
                 top_n: int = 6) -> bool:
    """True when current SAST hour is among the historically busiest."""
    if not hours:
        return False
    h = now_h if now_h is not None else sast_hour()
    ranked = sorted(((int(k), int(v)) for k, v in hours.items()),
                    key=lambda kv: -kv[1])
    top = {hh for hh, _ in ranked[:top_n] if _ > 0}
    return h in top


def sweep_sleep_sec(base: float = 120.0, peak: bool = False,
                    price_moved: bool = False) -> float:
    """Faster cadence on price moves / peak SAST hours (still CPU-friendly)."""
    if price_moved:
        return 25.0
    if peak:
        return max(45.0, base * 0.45)
    return base


def arb_sleep_sec(base: float = 90.0, peak: bool = False) -> float:
    # Dash scans are expensive — keep peak from thrashing CPU.
    return 55.0 if peak else base


def prices_sleep_sec(base: float = 25.0, peak: bool = False) -> float:
    return 15.0 if peak else base


def is_edge_opp(opp: Dict[str, Any]) -> bool:
    if opp.get("edge"):
        return True
    for k in ("coll_sym", "debt_sym", "collateral_symbol", "debt_symbol"):
        sym = str(opp.get(k) or "").upper()
        if sym in LONG_TAIL:
            return True
    return False


def rank_liq_opps(opps: Iterable[Dict[str, Any]], edge_bias: bool = True
                  ) -> List[Dict[str, Any]]:
    """Prefer long-tail, then uncontested, then highest profit.

    Contested books stay in the list — we never drop a +EV race.
    """
    rows = list(opps)

    def key(o):
        edge = 1 if (edge_bias and is_edge_opp(o)) else 0
        race = 1 if (o.get("contested") or o.get("recent_competitor")
                     or o.get("race")) else 0
        return (-edge, race, -(float(o.get("profit_usd") or 0)))

    return sorted(rows, key=key)


def contested_users_from_mempool(spoke_txs: Iterable[Dict[str, Any]],
                                 liq_sel: str = "0xc2fa746c") -> Set[str]:
    """Users already targeted by pending liquidationCall txs."""
    out: Set[str] = set()
    for t in spoke_txs or []:
        name = (t.get("name") or "").lower()
        inp = (t.get("input") or t.get("selector") or "")
        if "liquidation" not in name and not str(inp).startswith(liq_sel):
            continue
        for a in t.get("args") or []:
            s = str(a)
            if "user=" in s:
                u = s.split("user=", 1)[1].strip().lower()
                if u.startswith("0x") and len(u) >= 42:
                    out.add(u[:42])
            elif s.startswith("0x") and len(s) >= 42 and s[2:4] != "00":
                # heuristic: 3rd arg often user
                pass
    return out


def recent_competitor_users(competitors: Iterable[Dict[str, Any]],
                            max_age_sec: int = 180) -> Set[str]:
    now = int(time.time())
    out: Set[str] = set()
    for c in competitors or []:
        ts = int(c.get("ts") or 0)
        if ts and now - ts > max_age_sec:
            continue
        u = str(c.get("user") or "").lower()
        if u.startswith("0x"):
            out.add(u)
            out.add(u[:10])
        short = str(c.get("user_short") or "").lower()
        if short.startswith("0x"):
            out.add(short)
    return out


def race_label(user: str, contested: Set[str], recent: Set[str]) -> str:
    """Annotate competition. Empty string = uncontested."""
    u = (user or "").lower()
    short = u[:10]
    if u in contested or short in contested:
        return "mempool-contested"
    if u in recent or short in recent:
        return "recent-competitor"
    return ""


def race_prio_mult(why: str, base: float = 1.0) -> float:
    """Bump builder tip/priority gas when racing; identity if uncontested."""
    if why == "mempool-contested":
        return float(base) * 1.4
    if why == "recent-competitor":
        return float(base) * 1.25
    return float(base)


def should_skip_user(user: str, contested: Set[str], recent: Set[str],
                     net_usd: Optional[float] = None,
                     min_usd: Optional[float] = None) -> Tuple[bool, str]:
    """Do not yield races.

    Mempool-contested / recent-competitor is an annotation, not a skip.
    Still *prefer* uncontested via rank_liq_opps. Skip only when a net
    is provided and it is below the +EV floor.
    """
    why = race_label(user, contested, recent)
    if net_usd is not None and min_usd is not None:
        try:
            if float(net_usd) < float(min_usd):
                return True, "below-floor"
        except (TypeError, ValueError):
            return True, "below-floor"
    return False, why


def arb_plan_stale(quoted_block: Optional[int], now_block: Optional[int],
                   max_blocks: int = 1) -> bool:
    if not quoted_block or not now_block:
        return False
    return (now_block - quoted_block) > max_blocks


def gas_aware_borrow_weth(raw_borrow: int, flash_weth_depth: int,
                          gas_gwei: float, eth_usd: float,
                          profit_weth: int) -> int:
    """Shrink borrow when gas eats margin; never exceed pool depth*0.3."""
    cap = max(0, int(flash_weth_depth * 0.3))
    borrow = min(int(raw_borrow), cap) if cap else int(raw_borrow)
    # gas cost in wei: gas_units * gwei * 1e9
    gas_eth_wei = int(500_000 * float(gas_gwei or 1.0) * 1e9)
    if profit_weth <= gas_eth_wei * 2:
        borrow = borrow // 2
    return max(borrow, 10**15)  # dust floor 0.001 WETH


def load_near_miss() -> Dict[str, Any]:
    if not os.path.exists(_NEAR_PATH):
        return {"routes": {}, "updated": 0}
    try:
        with open(_NEAR_PATH) as f:
            return json.load(f)
    except Exception:
        return {"routes": {}, "updated": 0}


def record_near_miss(mid: str, fee: float, profit_weth: float,
                     gas_gwei: float) -> None:
    data = load_near_miss()
    routes = data.setdefault("routes", {})
    key = f"{mid}|{fee}"
    rec = routes.get(key) or {"n": 0, "best": -1e9, "sum": 0.0, "last_gas": 0}
    rec["n"] += 1
    rec["sum"] += float(profit_weth)
    rec["best"] = max(rec["best"], float(profit_weth))
    rec["last_gas"] = float(gas_gwei or 0)
    rec["updated"] = int(time.time())
    routes[key] = rec
    data["updated"] = int(time.time())
    # keep top 80 by n
    if len(routes) > 80:
        keep = sorted(routes.items(), key=lambda kv: -kv[1].get("n", 0))[:80]
        data["routes"] = dict(keep)
    try:
        with open(_NEAR_PATH, "w") as f:
            json.dump(data, f, indent=2)
    except OSError:
        pass


def near_miss_hints(limit: int = 8) -> List[Dict[str, Any]]:
    routes = load_near_miss().get("routes") or {}
    rows = []
    for k, v in routes.items():
        mid, fee = (k.split("|", 1) + ["?"])[:2]
        rows.append({
            "mid": mid,
            "fee": fee,
            "n": v.get("n", 0),
            "best_weth": round(float(v.get("best", 0)), 6),
            "avg_weth": round(float(v.get("sum", 0)) / max(1, int(v.get("n", 1))), 6),
        })
    rows.sort(key=lambda r: (-r["n"], -r["best_weth"]))
    return rows[:limit]


def sponsor_target_eth(gas_gwei: Optional[float], liq_gas: int = 1_000_000,
                       buffer: float = 0.02) -> float:
    """Minimal sponsor float: one liq gas + buffer."""
    g = float(gas_gwei or 20.0)
    need = liq_gas * g * 1e-9
    return round(max(0.03, need + buffer), 4)


def should_sweep_bot(bot_eth: float, keep: float = 0.005,
                     min_sweep: float = 0.02) -> Tuple[bool, float]:
    """Leave dust for nonce/gas; sweep excess to cold."""
    excess = float(bot_eth or 0) - keep
    if excess >= min_sweep:
        return True, round(excess, 6)
    return False, 0.0


def rank_arb_opps(opps: Iterable[Dict[str, Any]], gas_gwei: float,
                  eth_usd: float) -> List[Dict[str, Any]]:
    """Rank by net USD after estimated gas; annotate gas/net/gap fields."""
    g = float(gas_gwei or 1.0)
    eth = float(eth_usd or 0.0)
    gas_eth = 500_000 * g * 1e-9
    gas_usd = gas_eth * eth if eth else 0.0
    rows = []
    for o in opps or []:
        r = dict(o)
        profit_weth = float(r.get("profit_weth") or 0)
        profit_usd = float(r.get("profit_usd") or (profit_weth * eth if eth else 0))
        borrow = float(r.get("borrow_weth") or 0) or 1e-12
        g_usd = r.get("gas_usd")
        if g_usd is None:
            g_usd = gas_usd
            r["gas_usd"] = round(float(g_usd), 2)
        if r.get("net_usd") is None:
            r["net_usd"] = round(profit_usd - float(g_usd or 0), 2)
        net_usd = float(r.get("net_usd") or 0)
        r["roi_bps"] = round(profit_weth / borrow * 10_000, 1)
        if r.get("gap_usd") is None:
            r["gap_usd"] = round(min(0.0, net_usd), 2)
        rows.append(r)
    rows.sort(key=lambda x: (-(x.get("net_usd") or -1e18),
                             -(x.get("profit_usd") or 0)))
    return rows


def prefer_learned_mids(hints: List[Dict[str, Any]], limit: int = 5) -> Set[str]:
    return {str(h.get("mid") or "").upper() for h in (hints or [])[:limit]}


# --------------------------------------------------------------------------- profit performance
_PERF_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "profit_performance.json")


def load_perf() -> Dict[str, Any]:
    if not os.path.exists(_PERF_PATH):
        return {
            "started": int(time.time()),
            "baseline_equity_usd": None,
            "ledger": [],
            "equity_hist": [],
            "updated": 0,
        }
    try:
        with open(_PERF_PATH) as f:
            return json.load(f)
    except Exception:
        return {
            "started": int(time.time()),
            "baseline_equity_usd": None,
            "ledger": [],
            "equity_hist": [],
            "updated": 0,
        }


def save_perf(data: Dict[str, Any]) -> None:
    data["updated"] = int(time.time())
    try:
        with open(_PERF_PATH, "w") as f:
            json.dump(data, f, indent=2)
    except OSError:
        pass


def wallet_equity_usd(funds: Dict[str, Any], eth_usd: float) -> Dict[str, float]:
    """Sum managed wallet balances into ETH + USD equity."""
    eth = float(eth_usd or 0)
    tot_eth = tot_stable = 0.0
    by = {}
    for label, bal in (funds or {}).items():
        if not isinstance(bal, dict):
            continue
        e = float(bal.get("eth") or 0) + float(bal.get("weth") or 0)
        s = float(bal.get("usdc") or 0) + float(bal.get("usdt") or 0)
        usd = e * eth + s
        by[label] = round(usd, 2)
        tot_eth += e
        tot_stable += s
    return {
        "equity_eth": round(tot_eth, 6),
        "equity_stable": round(tot_stable, 2),
        "equity_usd": round(tot_eth * eth + tot_stable, 2),
        "by_wallet": by,
    }


def record_perf_event(kind: str, stage: str, profit_usd: Optional[float],
                      detail: str = "") -> Dict[str, Any]:
    data = load_perf()
    stage_l = (stage or "").lower()
    profit = float(profit_usd or 0)
    realized = stage_l in ("sent", "ok", "cast-ok")
    simulated = stage_l in ("simulated", "dry-run", "sim")
    skipped = stage_l.startswith("skip") or stage_l in ("refuse", "cooldown")
    entry = {
        "ts": int(time.time()),
        "kind": kind,
        "stage": stage_l,
        "profit_usd": round(profit, 2),
        "realized": realized,
        "simulated": simulated,
        "skipped": skipped,
        "detail": (detail or "")[:160],
    }
    ledger = data.setdefault("ledger", [])
    ledger.insert(0, entry)
    data["ledger"] = ledger[:200]
    save_perf(data)
    return entry


def snapshot_performance(funds: Dict[str, Any], eth_usd: float,
                         competitors: Optional[List[Dict[str, Any]]] = None,
                         opportunities: Optional[List[Dict[str, Any]]] = None,
                         arb: Optional[Dict[str, Any]] = None,
                         broadcast: Optional[Dict[str, Any]] = None,
                         ) -> Dict[str, Any]:
    """Build live performance scorecard for the Funds card."""
    data = load_perf()
    eq = wallet_equity_usd(funds, eth_usd)
    now = int(time.time())
    if data.get("baseline_equity_usd") is None and eq["equity_usd"] > 0:
        data["baseline_equity_usd"] = eq["equity_usd"]
        data["started"] = now
    # equity history (throttle to ~once / 30s)
    hist = data.setdefault("equity_hist", [])
    if not hist or now - int(hist[-1][0]) >= 30:
        hist.append([now, eq["equity_usd"]])
        data["equity_hist"] = hist[-120:]
        save_perf(data)

    ledger = data.get("ledger") or []
    day_ago = now - 86400
    day = [e for e in ledger if int(e.get("ts") or 0) >= day_ago]
    session = ledger  # all tracked events this install

    def _sum(rows, pred):
        return round(sum(float(e.get("profit_usd") or 0) for e in rows if pred(e)), 2)

    realized = _sum(session, lambda e: e.get("realized"))
    simulated = _sum(session, lambda e: e.get("simulated") and float(e.get("profit_usd") or 0) > 0)
    wins = sum(1 for e in session if e.get("realized") and float(e.get("profit_usd") or 0) > 0)
    submits = sum(1 for e in session if e.get("realized") or e.get("simulated"))
    skips = sum(1 for e in session if e.get("skipped"))
    errors = sum(1 for e in session if (e.get("stage") or "") in ("error", "fail", "revert"))

    # competitor opportunity cost (missed liquidations we watched)
    missed = 0.0
    miss_n = 0
    for c in competitors or []:
        if not c.get("missed_by_us"):
            continue
        v = c.get("net_est_usd")
        if v is None:
            v = c.get("est_profit_usd")
        if v is None:
            continue
        missed += float(v)
        miss_n += 1
    missed = round(missed, 2)

    best_opp = max([float(o.get("profit_usd") or 0) for o in (opportunities or [])] or [0.0])
    arb_meta = (arb or {}).get("meta") or {}
    arb_best = arb_meta.get("best_net_usd")
    if arb_best is None:
        near = (arb or {}).get("near") or []
        arb_best = near[0].get("net_usd") if near else None

    baseline = data.get("baseline_equity_usd")
    session_pnl = None
    if baseline is not None:
        session_pnl = round(eq["equity_usd"] - float(baseline), 2)

    ready = (broadcast or {}).get("ready") or {}
    capital_ok = eq["equity_usd"] >= 5 or eq["equity_eth"] >= 0.01
    can_trade = bool(ready.get("liq") or ready.get("arb"))

    # grade / verdict
    if not capital_ok:
        grade, verdict = "—", "no capital — fund sponsor/bot to start earning"
    elif not can_trade:
        grade, verdict = "D", "capital idle — deploy LIQ/ARB contracts to unlock profit"
    elif wins > 0 and realized > missed:
        grade, verdict = "A", f"beating searchers — realized ${realized:.2f} vs missed ${missed:.2f}"
    elif wins > 0:
        grade, verdict = "B", f"booked ${realized:.2f} but missing ${missed:.2f} to competitors"
    elif simulated > 0:
        grade, verdict = "C", f"paper edge ${simulated:.2f} — arm LIVE when ready"
    elif best_opp > 0 or (arb_best is not None and arb_best > 0):
        grade, verdict = "C", "opportunities visible — waiting to capture"
    elif miss_n > 0:
        grade, verdict = "D", f"behind searchers — ${missed:.2f} missed on watched users"
    else:
        grade, verdict = "C", "scanning — no liquidatable / actionable arb yet"

    hit_rate = round(100.0 * wins / submits, 1) if submits else 0.0

    return {
        **eq,
        "baseline_usd": baseline,
        "session_pnl_usd": session_pnl,
        "realized_usd": realized,
        "simulated_usd": simulated,
        "day_realized_usd": _sum(day, lambda e: e.get("realized")),
        "day_simulated_usd": _sum(day, lambda e: e.get("simulated") and float(e.get("profit_usd") or 0) > 0),
        "submits": submits,
        "wins": wins,
        "skips": skips,
        "errors": errors,
        "hit_rate_pct": hit_rate,
        "missed_comp_usd": missed,
        "missed_comp_n": miss_n,
        "best_opp_usd": round(best_opp, 2),
        "arb_best_net_usd": arb_best,
        "grade": grade,
        "verdict": verdict,
        "capital_ok": capital_ok,
        "can_trade": can_trade,
        "started": data.get("started"),
        "equity_hist": data.get("equity_hist") or [],
        "ledger": ledger[:12],
        "updated": now,
    }


# --------------------------------------------------------------------------- Solana floors
SOL_LONG_TAIL = {
    "BONK", "WIF", "PYTH", "RAY", "MSOL", "JITOSOL", "CBBTC", "WSTETH", "STSOL",
}


def dynamic_min_sol_arb_usd(prio_ul: Optional[float], sol_usd: Optional[float],
                            base: float = 0.05, cu: int = 400_000,
                            jito_sol: float = 0.00001) -> float:
    """Arb floor = env base + CU priority + Jito tip, in USD."""
    px = max(float(sol_usd or 0.0), 0.0)
    fee_sol = max(int(prio_ul or 0) * int(cu) / 1e15, 0.00002)
    return round(max(float(base), float(base) * 0.5 + (fee_sol + float(jito_sol)) * px), 6)


def dynamic_min_sol_liq_usd(prio_ul: Optional[float], sol_usd: Optional[float],
                            base: float = 0.50, cu: int = 400_000,
                            jito_sol: float = 0.00001) -> float:
    """Liq floor stays low on Solana (cheap CU) but never below fee+tip+base*0.4."""
    px = max(float(sol_usd or 0.0), 0.0)
    fee_sol = max(int(prio_ul or 0) * int(cu) / 1e15, 0.00002)
    cost = (fee_sol + float(jito_sol)) * px
    return round(max(float(base), cost + float(base) * 0.4), 4)


def sol_is_edge_opp(opp: Dict[str, Any]) -> bool:
    if opp.get("edge"):
        return True
    for k in ("coll_sym", "debt_sym", "collateral_sym", "debt_symbol", "symbol"):
        sym = str(opp.get(k) or "").upper().replace("JITOSOL", "JITOSOL")
        if sym in SOL_LONG_TAIL:
            return True
    return False
