"""Solana mainnet probes for the TONI SOL dashboard twin.

Primary lending target: Solend (Save) — public markets/reserves API.
Arb: Jupiter lite aggregator (multi-DEX routes) with size grid + priority-fee net.
Honest feeds only: empty lists when nothing liquidatable; no fake MEV.
"""
from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import requests

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

SOL_RPCS = [
    os.environ.get("SOLANA_RPC") or "",
    "https://api.mainnet-beta.solana.com",
    "https://solana-rpc.publicnode.com",
]
SOL_RPCS = [u for u in SOL_RPCS if u]

SOLEND_MARKETS = "https://api.solend.fi/v1/markets/configs"
SOLEND_RESERVES = "https://api.solend.fi/v1/reserves"
SOLEND_OBLIGATION = "https://api.solend.fi/v1/obligation"
JUP_QUOTE = "https://lite-api.jup.ag/swap/v1/quote"

# Canonical mints
MINT_SOL = "So11111111111111111111111111111111111111112"
MINT_USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
MINT_USDT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
MINT_MSOL = "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So"
MINT_JITOSOL = "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn"
MINT_BONK = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
MINT_RAY = "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R"
MINT_WIF = "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm"
MINT_PYTH = "HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3"

# decimals for size grids
DECIMALS = {
    MINT_SOL: 9, MINT_USDC: 6, MINT_USDT: 6,
    MINT_MSOL: 9, MINT_JITOSOL: 9, MINT_BONK: 5,
    MINT_RAY: 6, MINT_WIF: 6, MINT_PYTH: 6,
}
SYM = {
    MINT_SOL: "SOL", MINT_USDC: "USDC", MINT_USDT: "USDT",
    MINT_MSOL: "mSOL", MINT_JITOSOL: "JitoSOL", MINT_BONK: "BONK",
    MINT_RAY: "RAY", MINT_WIF: "WIF", MINT_PYTH: "PYTH",
}

WATCH_SYMS = ("SOL", "USDC", "USDT", "mSOL", "stSOL", "jitoSOL", "JITOSOL",
              "ETH", "BTC", "cbBTC", "wstETH", "BONK")

PROTOCOL = "Solend"
PROTOCOL_NOTE = "Solend (Save) main market — not Aave V4"

# Optional managed wallets (base58 pubkeys via env)
DEFAULT_SOL_FUNDER = "4BHGQ9CXhajxDq5b3jvKfimsXEsFHoHUi2qg21qYVnGy"
_HERE = os.path.dirname(os.path.abspath(__file__))


def _load_env_file(path: str) -> None:
    if not os.path.isfile(path):
        return
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
    except OSError:
        pass


_load_env_file(os.path.join(_HERE, ".env"))
_load_env_file(os.path.join(_HERE, "solana", ".env"))


def current_wallets() -> dict[str, str]:
    """Live pubkeys: env wins, then on-disk keypair files."""
    sponsor = (os.environ.get("SOL_SPONSOR") or "").strip()
    bot = (os.environ.get("SOL_BOT") or "").strip()
    if not sponsor:
        sponsor = _pubkey_from_keypair_file(SPONSOR_KEY_PATH) or ""
    if not bot:
        bot = (_pubkey_from_keypair_file(BOT_KEY_PATH)
               or _pubkey_from_keypair_file(
                   os.environ.get("SOL_KEYPAIR", "")) or "")
    return {
        "funder": (os.environ.get("SOL_FUNDER") or DEFAULT_SOL_FUNDER).strip(),
        "sponsor": sponsor,
        "bot": bot,
    }

# Solend / Save lending program (mainnet)
SOLEND_PROGRAM = "So1endDq2YkqhipRh3WViPa8hdiSpxWy6z3Z6tMCpAo"
# SPL Token Lending obligation account size (Solend main) — common filter
OBLIGATION_DATA_SIZES = (1300, 916, 1456, 1824)
# lendingMarket pubkey offset in obligation (Token Lending layout)
# version(1) + lastUpdate(slot u64 + stale u8=9) + lendingMarket(32) ≈ offset 10
OBLIGATION_MARKET_OFFSET = 10

# Round-trip pairs (input mint used for size grid; mid is hop mint)
ARB_PAIRS = [
    (MINT_SOL, MINT_USDC, "SOL-USDC"),
    (MINT_SOL, MINT_USDT, "SOL-USDT"),
    (MINT_SOL, MINT_MSOL, "SOL-mSOL"),
    (MINT_SOL, MINT_JITOSOL, "SOL-JitoSOL"),
    (MINT_USDC, MINT_USDT, "USDC-USDT"),
    (MINT_SOL, MINT_BONK, "SOL-BONK"),
    (MINT_SOL, MINT_RAY, "SOL-RAY"),
    (MINT_SOL, MINT_WIF, "SOL-WIF"),
    (MINT_SOL, MINT_PYTH, "SOL-PYTH"),
]

# Size grid in *native* units of the input mint (not always lamports)
SIZE_GRID = {
    MINT_SOL: [50_000_000, 200_000_000, 1_000_000_000],       # 0.05 / 0.2 / 1 SOL
    MINT_USDC: [50_000_000, 200_000_000],                    # 50 / 200 USDC
}

# Suggested fund amounts (mainnet SOL)
SPONSOR_TARGET_SOL = 0.08   # Jito tips + prio for bundles
BOT_TARGET_SOL = 0.25       # CU fees + small inventory

KEYS_DIR = os.path.join(_HERE, "solana", "keys")
SPONSOR_KEY_PATH = os.path.join(KEYS_DIR, "sponsor.json")
BOT_KEY_PATH = os.path.join(KEYS_DIR, "bot.json")


def _hdr():
    return {"User-Agent": _UA, "Content-Type": "application/json",
            "Accept": "application/json"}


def sol_rpc(method: str, params: list | None = None, timeout: float = 6.0):
    """JSON-RPC against first healthy public Solana endpoint."""
    body = {"jsonrpc": "2.0", "id": 1, "method": method,
            "params": params if params is not None else []}
    last = None
    for url in SOL_RPCS:
        try:
            r = requests.post(url, json=body, headers=_hdr(), timeout=timeout)
            r.raise_for_status()
            out = r.json()
            if "error" in out:
                last = out["error"]
                continue
            return out.get("result"), url
        except Exception as e:  # noqa: BLE001
            last = e
            continue
    raise RuntimeError(f"sol rpc {method} failed: {last}")


def fetch_epoch_and_slot() -> dict[str, Any]:
    out: dict[str, Any] = {
        "ok": False, "slot": None, "epoch": None, "absolute_slot": None,
        "slot_index": None, "slots_in_epoch": None, "rpc": None, "error": None,
    }
    try:
        epoch, url = sol_rpc("getEpochInfo", [])
        slot, _ = sol_rpc("getSlot", [])
        out["ok"] = True
        out["rpc"] = url
        out["slot"] = slot
        if isinstance(epoch, dict):
            out["epoch"] = epoch.get("epoch")
            out["absolute_slot"] = epoch.get("absoluteSlot")
            out["slot_index"] = epoch.get("slotIndex")
            out["slots_in_epoch"] = epoch.get("slotsInEpoch")
            if out["slot"] is None:
                out["slot"] = epoch.get("absoluteSlot")
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def _fee_cls(fee: int) -> str:
    if fee <= 0:
        return "zero"
    if fee < 1_000:
        return "quiet"
    if fee < 5_000:
        return "busy"
    if fee < 50_000:
        return "elevated"
    return "hot"


def _pressure(median: int, p90: int, hot_n: int, n: int) -> str:
    hot_share = (hot_n / n) if n else 0
    if median >= 50_000 or p90 >= 200_000 or hot_share >= 0.25:
        return "hot"
    if median >= 5_000 or p90 >= 25_000 or hot_share >= 0.08:
        return "elevated"
    if median >= 1_000 or p90 >= 5_000:
        return "busy"
    if median > 0:
        return "quiet"
    return "idle"


def fetch_priority_fees(limit: int = 80) -> dict[str, Any]:
    """getRecentPrioritizationFees + TPS — Solana landing-pressure twin."""
    out: dict[str, Any] = {
        "ok": False, "samples": 0, "slots": 0, "median": None, "p90": None,
        "p99": None, "max": None, "avg": None, "zero_n": 0, "zero_pct": 0,
        "fees": [], "hot_slots": [], "mix": {}, "error": None, "rpc": None,
        "histogram": [], "pressure": "idle", "tps": None, "nv_tps": None,
    }
    try:
        fees, url = sol_rpc("getRecentPrioritizationFees", [[]], timeout=8)
        out["rpc"] = url
        rows = [x for x in (fees or []) if isinstance(x, dict)]
        vals = sorted(int(x.get("prioritizationFee") or 0) for x in rows)
        if not vals:
            out["ok"] = True
            out["error"] = "empty fee sample"
            return out
        n = len(vals)
        med = vals[n // 2]
        p90 = vals[min(n - 1, int(n * 0.9))]
        p99 = vals[min(n - 1, int(n * 0.99))]
        zero_n = sum(1 for v in vals if v <= 0)
        mix: dict[str, int] = {}
        by_slot: dict[int, int] = {}
        classified = []
        for f in rows:
            fee = int(f.get("prioritizationFee") or 0)
            slot = f.get("slot")
            cls = _fee_cls(fee)
            mix[cls] = mix.get(cls, 0) + 1
            try:
                si = int(slot)
                if fee > by_slot.get(si, -1):
                    by_slot[si] = fee
            except (TypeError, ValueError):
                pass
            classified.append({
                "slot": slot,
                "fee": fee,
                "cls": cls,
                "vs_med": round(fee / med, 2) if med else None,
            })
        classified.sort(key=lambda x: -int(x.get("fee") or 0))
        hot_n = mix.get("hot", 0) + mix.get("elevated", 0)
        out["ok"] = True
        out["samples"] = n
        out["slots"] = len(by_slot)
        out["median"] = med
        out["p90"] = p90
        out["p99"] = p99
        out["max"] = vals[-1]
        out["avg"] = int(sum(vals) / n)
        out["zero_n"] = zero_n
        out["zero_pct"] = round(100.0 * zero_n / n, 1)
        out["mix"] = mix
        out["pressure"] = _pressure(med, p90, hot_n, n)
        out["fees"] = classified[:limit]
        out["hot_slots"] = [
            {"slot": s, "fee": fee, "cls": _fee_cls(fee)}
            for s, fee in sorted(by_slot.items(), key=lambda kv: -kv[1])[:12]
        ]
        buckets = [
            (0, 1, "zero"),
            (1, 100, "<0.1k"),
            (100, 1_000, "0.1–1k"),
            (1_000, 5_000, "1–5k"),
            (5_000, 25_000, "5–25k"),
            (25_000, 100_000, "25–100k"),
            (100_000, 1_000_000, "100k–1M"),
            (1_000_000, None, "≥1M"),
        ]
        hist = []
        for lo, hi, label in buckets:
            if hi is None:
                c = sum(1 for v in vals if v >= lo)
            else:
                c = sum(1 for v in vals if lo <= v < hi)
            hist.append({
                "lo": lo, "hi": hi, "n": c, "label": label,
                "pct": round(100.0 * c / n, 1),
            })
        out["histogram"] = hist
        try:
            samples, _ = sol_rpc("getRecentPerformanceSamples", [4], timeout=6)
            if samples and isinstance(samples[0], dict):
                sec = max(int(samples[0].get("samplePeriodSecs") or 1), 1)
                nt = int(samples[0].get("numTransactions") or 0)
                nv = int(samples[0].get("numNonVoteTransactions")
                         or samples[0].get("numNonVoteTransaction") or 0)
                out["tps"] = round(nt / sec, 1)
                out["nv_tps"] = round(nv / sec, 1)
        except Exception:
            pass
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def fetch_sol_price() -> float | None:
    try:
        r = requests.get(
            "https://api.binance.com/api/v3/ticker/price",
            params={"symbol": "SOLUSDT"}, headers={"User-Agent": _UA},
            timeout=5,
        )
        r.raise_for_status()
        return float(r.json()["price"])
    except Exception:
        return None


def _util_pct(reserve: dict) -> float:
    liq = (reserve or {}).get("liquidity") or {}
    try:
        avail = int(liq.get("availableAmount") or 0)
        borrowed = int(liq.get("borrowedAmountWads") or 0) / (10 ** 18)
        denom = avail + borrowed
        if denom <= 0:
            return 0.0
        return max(0.0, min(100.0, 100.0 * borrowed / denom))
    except Exception:
        return 0.0


def _jup_quote(input_mint: str, output_mint: str, amount: int,
               slippage_bps: int = 50) -> dict | None:
    try:
        r = requests.get(
            JUP_QUOTE,
            params={
                "inputMint": input_mint,
                "outputMint": output_mint,
                "amount": int(amount),
                "slippageBps": slippage_bps,
                "restrictIntermediateTokens": "true",
                "onlyDirectRoutes": "false",
                "maxAccounts": 40,
            },
            headers={"User-Agent": _UA}, timeout=8,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _route_labels(quote: dict) -> list[str]:
    labels = []
    for hop in (quote.get("routePlan") or []):
        lab = ((hop or {}).get("swapInfo") or {}).get("label") or "jup"
        if lab not in labels:
            labels.append(lab)
    return labels or ["jup"]


def _cu_estimate(hops: int) -> int:
    return min(1_400_000, 200_000 + max(int(hops or 2), 2) * 80_000)


# Jito tip receivers (plan metadata only — never submitted from this dashboard)
JITO_TIP_ACCOUNTS = (
    "96gYZGLnJYVFmbjzopPSU6QiECT5fJcpQNqEwGPNL6X",
    "HFqU5x63VTqvQss8hp11i4wVV8bD44PvwucfZ2bU7gRe",
    "Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMzpKw6QNghXLvLkY",
    "ADaUMid9yfUytqMBgopwjb2DTLSokTSzL1zt6iGPaS49",
    "DfXygSm4jCyNCybVYYK6XLfvuE8Ai2XJUTskz1pMnoNy",
    "ADuUkR4vqLUMWXxW9gh6Ds3Pce2sAgdtBvuaYTjRtonj",
    "DttWaMuVvTiduZRnguLF7jNxTgiMBZ1hyAumKUiL2KRL",
    "3AVi9Tg9Uo68tJfuvoKvqKNWKkC5wPdSSdeBnizKZ6jT",
)


def _jito_tip_account(seed: str) -> str:
    if not seed:
        return JITO_TIP_ACCOUNTS[0]
    return JITO_TIP_ACCOUNTS[sum(seed.encode()) % len(JITO_TIP_ACCOUNTS)]


def _prio_cost_usd(median_ul: int | None, sol_px: float,
                   cu_estimate: int = 400_000) -> float:
    """Rough priority-fee cost in USD from microlamports/CU median."""
    med = int(median_ul or 0)
    tip_sol = max(med * cu_estimate / 1e15, 0.00002)  # ≥ ~0.00002 SOL
    jito_tip_sol = float(os.environ.get("SOL_JITO_TIP_SOL", "0.00001") or 0)
    return (tip_sol + jito_tip_sol) * max(sol_px, 0.0)


def build_arb_plan(row: dict, priority_median: int | None = None) -> dict:
    """Dry-run broadcast plan — bot pays CU, sponsor pays Jito tip."""
    tip_sol = float(os.environ.get("SOL_JITO_TIP_SOL", "0.00001") or 0)
    w = current_wallets()
    hops = int(row.get("hops") or 2)
    cu = int(row.get("compute_units") or _cu_estimate(hops))
    bot = w.get("bot") or ""
    sponsor = w.get("sponsor") or ""
    return {
        "kind": "arb",
        "mode": "dry-run",
        "scan_mode": "jup-smart",
        "program": os.environ.get("SOL_ARB_PROGRAM") or "",
        "path": row.get("path"),
        "mid": row.get("mid"),
        "amount_in": row.get("amount_in"),
        "input_mint": row.get("input_mint"),
        "output_mint": row.get("output_mint"),
        "mid_mint": row.get("output_mint"),
        "min_amount_out": row.get("min_amount_out"),
        "expected_net_usd": row.get("net_usd"),
        "score": row.get("score"),
        "priority_fee_ul": priority_median,
        "priority_micros": int(priority_median or 0),
        "compute_units": cu,
        "fee_payer": bot,
        "tip_payer": sponsor,
        "jito_tip_account": _jito_tip_account(bot or sponsor),
        "jito_tip_lamports": int(tip_sol * 1e9) if tip_sol > 0 else 0,
        "jito_bundle": False,  # never spam bundles without key
        "venue": row.get("venue"),
        "dexes": row.get("labels") or [],
        "note": "plan only — Jupiter CPI not wired; fee=bot tip=sponsor",
    }


def build_liq_plan(opp: dict) -> dict:
    tip_sol = float(os.environ.get("SOL_JITO_TIP_SOL", "0.00001") or 0)
    return {
        "kind": "liq",
        "mode": "dry-run",
        "program": os.environ.get("SOL_LIQ_PROGRAM") or "",
        "obligation": opp.get("obligation") or opp.get("user"),
        "repay_mint": opp.get("debt_mint") or "",
        "withdraw_mint": opp.get("coll_mint") or "",
        "debt_amount": opp.get("debt_amount") or 0,
        "expected_profit_usd": opp.get("profit_usd"),
        "jito_tip_lamports": int(tip_sol * 1e9) if tip_sol > 0 else 0,
        "jito_bundle": False,
        "note": "plan only — Solend CPI not wired; HF may be proxy",
    }


def fetch_solend_watchlist() -> dict[str, Any]:
    """Live Solend reserve probe → watchlist twin (stress / util sorted).

    Liquidatable borrower HF is not exposed as a free list by Solend's public
    HTTP API. We keep reserve util as the stress proxy and separately probe
    obligation accounts via RPC (see probe_solend_obligations).
    """
    out: dict[str, Any] = {
        "ok": False, "protocol": PROTOCOL, "market": None,
        "watchlist": [], "opportunities": [], "reserves_n": 0,
        "error": None, "ts": int(time.time()),
        "hf_public": False,
        "hf_note": ("Solend public API has no liquidatable-obligation feed; "
                    "borrower HF requires obligation decode + oracles"),
    }
    try:
        r = requests.get(
            SOLEND_MARKETS,
            params={"scope": "all", "deployment": "production"},
            headers={"User-Agent": _UA}, timeout=18,
        )
        r.raise_for_status()
        markets = r.json()
        main = next((m for m in markets
                     if m.get("isPrimary") or m.get("name") == "main"), None)
        if not main:
            out["error"] = "no primary Solend market"
            return out
        out["market"] = main.get("address")
        picked = []
        for res in main.get("reserves") or []:
            tok = res.get("liquidityToken") or {}
            sym = tok.get("symbol") or ""
            if sym in WATCH_SYMS:
                picked.append({
                    "symbol": sym,
                    "address": res.get("address"),
                    "mint": tok.get("mint"),
                    "decimals": tok.get("decimals"),
                })
        out["reserves_n"] = len(main.get("reserves") or [])
        if not picked:
            out["ok"] = True
            out["error"] = "no watched reserves"
            return out
        ids = ",".join(p["address"] for p in picked if p.get("address"))
        rr = requests.get(
            SOLEND_RESERVES, params={"ids": ids},
            headers={"User-Agent": _UA}, timeout=18,
        )
        rr.raise_for_status()
        by_addr = {}
        for item in (rr.json().get("results") or []):
            res = item.get("reserve") or {}
            mint = ((res.get("liquidity") or {}).get("mintPubkey") or "")
            by_addr[mint] = item

        watch = []
        for p in picked:
            item = by_addr.get(p["mint"] or "")
            if not item:
                continue
            res = item.get("reserve") or {}
            rates = item.get("rates") or {}
            cfg = res.get("config") or {}
            util = _util_pct(res)
            max_u = float(cfg.get("maxUtilizationRate") or 100)
            urg = "hot" if util >= max_u * 0.95 else (
                "elevated" if util >= max_u * 0.8 else "quiet")
            # Fake HF-like score: 1.0 at 0% util → drops toward 0.7 at max util
            hf_proxy = round(1.15 - (util / max(max_u, 1)) * 0.45, 4)
            watch.append({
                "user": (p["mint"] or "")[:8] + "…" + (p["mint"] or "")[-4:]
                if p.get("mint") else p["symbol"],
                "mint": p.get("mint"),
                "symbol": p["symbol"],
                "hf": hf_proxy,
                "collateral_sym": p["symbol"],
                "debt_sym": "borrow",
                "coll_usd": None,
                "debt_usd": None,
                "util_pct": round(util, 2),
                "supply_apy": rates.get("supplyInterest"),
                "borrow_apy": rates.get("borrowInterest"),
                "ltv": cfg.get("loanToValueRatio"),
                "liq_thresh": cfg.get("liquidationThreshold"),
                "liq_bonus": cfg.get("liquidationBonus"),
                "urgency": urg,
                "note": "reserve util (not borrower HF)",
                "proxy": True,
            })
        watch.sort(key=lambda w: (w.get("hf") is None, w.get("hf") or 99))
        out["watchlist"] = watch
        out["ok"] = True
        out["opportunities"] = []
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def _pubkey_bytes(b58: str) -> bytes | None:
    try:
        import base58  # type: ignore
        raw = base58.b58decode(b58)
        return raw if len(raw) == 32 else None
    except Exception:
        return None


def probe_solend_obligations(market: str | None = None,
                             max_accounts: int = 40) -> dict[str, Any]:
    """Best-effort obligation probe via getProgramAccounts.

    Public RPCs often rate-limit / reject GPA. We try short timeouts +
    dataSize filters + optional lendingMarket memcmp. When GPA works we
    optionally hydrate a few accounts via Solend obligation HTTP API
    (ids=…) which *does* return borrowed/deposited when known.

    Returns opportunities only when we can score HF/LTV from API hydrate;
    otherwise surfaces probe meta + empty opps (honest).
    """
    out: dict[str, Any] = {
        "ok": False, "probed": 0, "hydrated": 0, "opportunities": [],
        "sample_pubkeys": [], "error": None, "method": None,
        "note": "", "ts": int(time.time()),
    }
    if not market:
        try:
            wl = fetch_solend_watchlist()
            market = wl.get("market")
        except Exception:
            market = None
    if not market:
        out["error"] = "no Solend market address"
        out["note"] = "cannot filter obligations without lendingMarket"
        return out

    market_raw = _pubkey_bytes(market)
    accounts: list[str] = []
    last_err = None
    method = None

    for size in OBLIGATION_DATA_SIZES[:2]:
        filters: list[dict] = [{"dataSize": size}]
        if market_raw:
            filters.append({
                "memcmp": {
                    "offset": OBLIGATION_MARKET_OFFSET,
                    "bytes": market,
                }
            })
        params = [SOLEND_PROGRAM, {
            "encoding": "base64",
            "dataSlice": {"offset": 0, "length": 0},
            "filters": filters,
            "commitment": "confirmed",
        }]
        try:
            # short timeout — public nodes often hang on GPA
            res, url = sol_rpc("getProgramAccounts", params, timeout=8)
            rows = res or []
            if rows:
                accounts = [r.get("pubkey") for r in rows
                            if isinstance(r, dict) and r.get("pubkey")]
                method = f"gpa dataSize={size} via {url}"
                break
            method = f"gpa dataSize={size} empty via {url}"
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {e}"
            continue

    # Fallback: no memcmp (broader) — still may be blocked
    if not accounts:
        try:
            res, url = sol_rpc(
                "getProgramAccounts",
                [SOLEND_PROGRAM, {
                    "encoding": "base64",
                    "dataSlice": {"offset": 0, "length": 0},
                    "filters": [{"dataSize": OBLIGATION_DATA_SIZES[0]}],
                    "commitment": "confirmed",
                }],
                timeout=8,
            )
            rows = res or []
            if rows:
                accounts = [r.get("pubkey") for r in rows
                            if isinstance(r, dict) and r.get("pubkey")]
                method = f"gpa-loose dataSize={OBLIGATION_DATA_SIZES[0]} via {url}"
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {e}"

    out["probed"] = len(accounts)
    out["sample_pubkeys"] = accounts[:12]
    out["method"] = method

    if not accounts:
        out["ok"] = True  # probe ran; nothing usable
        out["error"] = last_err
        out["note"] = (
            "getProgramAccounts blocked/empty on public RPC — "
            "borrower HF still not publicly listed; util watchlist is the proxy. "
            "Set SOLANA_RPC to a GPA-capable provider for obligation sweep."
        )
        return out

    # Hydrate a capped batch via Solend obligation endpoint
    opps = []
    for i in range(0, min(len(accounts), max_accounts), 8):
        chunk = accounts[i:i + 8]
        try:
            rr = requests.get(
                SOLEND_OBLIGATION,
                params={"ids": ",".join(chunk)},
                headers={"User-Agent": _UA}, timeout=14,
            )
            if rr.status_code != 200:
                continue
            payload = rr.json()
            results = payload if isinstance(payload, list) else (
                payload.get("results") or payload.get("obligations") or [])
            for item in results:
                out["hydrated"] += 1
                parsed = _score_obligation(item)
                if parsed:
                    opps.append(parsed)
        except Exception:
            continue

    opps.sort(key=lambda o: (o.get("hf") is None, o.get("hf") or 99,
                             -(o.get("profit_usd") or 0)))
    # Only surface as liquidatable when HF clearly < 1
    liq = [o for o in opps if o.get("hf") is not None and o["hf"] < 1.0]
    out["opportunities"] = liq[:25]
    out["ok"] = True
    out["note"] = (
        f"probed {len(accounts)} obligation accounts; hydrated {out['hydrated']}; "
        f"liquidatable (HF<1)={len(liq)}. "
        + ("HF from Solend obligation API." if out["hydrated"]
           else "hydrate empty — accounts exist but API returned no HF.")
    )
    return out


def _score_obligation(item: dict) -> dict | None:
    """Best-effort HF / profit proxy from Solend obligation JSON shapes."""
    if not isinstance(item, dict):
        return None
    obl = item.get("obligation") or item
    addr = (item.get("pubkey") or item.get("address")
            or obl.get("address") or "")
    # Various API shapes: borrowedValue / depositedValue (USD strings)
    try:
        borrowed = float(
            obl.get("borrowedValue")
            or item.get("borrowedValue")
            or ((obl.get("info") or {}).get("borrowedValue"))
            or 0
        )
        deposited = float(
            obl.get("depositedValue")
            or item.get("depositedValue")
            or ((obl.get("info") or {}).get("depositedValue"))
            or 0
        )
    except (TypeError, ValueError):
        borrowed, deposited = 0.0, 0.0

    # Some feeds expose unhealthyBorrowValue / borrowLimit
    try:
        borrow_limit = float(
            obl.get("borrowLimit") or item.get("borrowLimit")
            or obl.get("unhealthyBorrowValue") or 0
        )
    except (TypeError, ValueError):
        borrow_limit = 0.0

    hf = None
    if borrow_limit > 0 and borrowed > 0:
        # rough: limit/borrowed ≈ inverse LTV stress; map to HF-like
        hf = round(borrow_limit / borrowed, 4)
    elif deposited > 0 and borrowed > 0:
        # without liquidation threshold, use deposited/borrowed as soft proxy
        hf = round(deposited / borrowed, 4)

    if hf is None and borrowed <= 0:
        return None

    # Profit estimate only when underwater — still honest (bonus unknown)
    profit = None
    if hf is not None and hf < 1.0 and borrowed > 0:
        # assume ~5% liq bonus on repaid slice (conservative placeholder)
        profit = round(min(borrowed, deposited) * 0.05, 4)

    borrows = obl.get("borrows") or item.get("borrows") or []
    deposits = obl.get("deposits") or item.get("deposits") or []
    debt_sym = "?"
    coll_sym = "?"
    debt_mint = ""
    coll_mint = ""
    if borrows and isinstance(borrows[0], dict):
        debt_sym = (borrows[0].get("symbol")
                    or (borrows[0].get("mintAddress") or "")[:4] or "?")
        debt_mint = borrows[0].get("mintAddress") or ""
    if deposits and isinstance(deposits[0], dict):
        coll_sym = (deposits[0].get("symbol")
                    or (deposits[0].get("mintAddress") or "")[:4] or "?")
        coll_mint = deposits[0].get("mintAddress") or ""

    return {
        "user": (addr[:8] + "…" + addr[-4:]) if len(addr) > 12 else addr,
        "obligation": addr,
        "hf": hf,
        "collateral_sym": coll_sym,
        "debt_sym": debt_sym,
        "coll_mint": coll_mint,
        "debt_mint": debt_mint,
        "coll_usd": deposited or None,
        "debt_usd": borrowed or None,
        "debt_amount": 0,
        "profit_usd": profit,
        "urgency": ("hot" if hf is not None and hf < 1.0 else
                    "elevated" if hf is not None and hf < 1.05 else "quiet"),
        "edge": bool(hf is not None and hf < 1.0),
        "note": "obligation hydrate",
        "proxy": False,
    }


def _sizes_for(inp: str, mid: str, amounts_lamports: list[int] | None) -> list[int]:
    if amounts_lamports and inp == MINT_SOL:
        return amounts_lamports
    if inp == MINT_SOL and mid in (MINT_BONK, MINT_RAY, MINT_WIF, MINT_PYTH):
        return [20_000_000, 80_000_000]  # 0.02 / 0.08 SOL memecoins
    return SIZE_GRID.get(inp) or SIZE_GRID[MINT_SOL]


def _roundtrip_row(
    inp: str, mid: str, mid_label: str, amt: int,
    sol_px: float, priority_median: int | None, min_usd: float,
) -> tuple[dict | None, int]:
    """One Jupiter A→B→A. Returns (row, quotes_consumed)."""
    q1 = _jup_quote(inp, mid, amt)
    quotes = 1 if q1 else 0
    if not q1:
        return None, quotes
    mid_out = int(q1.get("outAmount") or 0)
    if mid_out <= 0:
        return None, quotes
    q2 = _jup_quote(mid, inp, mid_out)
    quotes += 1 if q2 else 0
    if not q2:
        return None, quotes
    back = int(q2.get("outAmount") or 0)
    if back <= 0:
        return None, quotes
    labs1 = _route_labels(q1)
    labs2 = _route_labels(q2)
    labels = labs1 + [x for x in labs2 if x not in labs1]
    venue = "→".join((labs1[:2] + labs2[:2])[:4])
    hops = len(q1.get("routePlan") or []) + len(q2.get("routePlan") or [])
    cu = _cu_estimate(hops)
    tip_usd = _prio_cost_usd(priority_median, sol_px, cu)
    dec = DECIMALS.get(inp, 9)
    gross_native = (back - amt) / (10 ** dec)
    if inp == MINT_SOL:
        gross_usd = gross_native * sol_px
        notional = (amt / 1e9) * sol_px
    elif inp in (MINT_USDC, MINT_USDT):
        gross_usd = gross_native
        notional = amt / 1e6
    else:
        gross_usd = gross_native * sol_px
        notional = max(amt / (10 ** dec) * sol_px, 1e-9)
    net_usd = gross_usd - tip_usd
    impact = abs(float(q1.get("priceImpactPct") or 0)) + abs(
        float(q2.get("priceImpactPct") or 0))
    dex_n = max(len(set(labs1 + labs2)), 1)
    score = (
        net_usd
        * (1.0 + 0.08 * max(dex_n - 1, 0))
        / (1.0 + hops * 0.04)
        / (1.0 + min(impact, 0.08) * 8)
    )
    cross = dex_n > 1
    sym_in = SYM.get(inp, "?")
    row = {
        "venue": venue[:48],
        "path": f"{sym_in}→{SYM.get(mid, '?')}→{sym_in}",
        "route": f"{amt / (10 ** dec):.4g} {sym_in}",
        "hops": hops,
        "borrow": f"{amt / (10 ** dec):.4g} {sym_in}",
        "gross_usd": round(gross_usd, 6),
        "gas_usd": round(tip_usd, 6),
        "net_usd": round(net_usd, 6),
        "score": round(score, 8),
        "flags": ("cross" if cross else "jup") + (" multi" if hops > 2 else ""),
        "dex": "jupiter",
        "mid": mid_label,
        "cross_dex": cross,
        "dex_n": dex_n,
        "actionable": net_usd > min_usd,
        "price_impact_pct": impact,
        "amount_in": amt,
        "input_mint": inp,
        "output_mint": mid,
        "min_amount_out": back,
        "labels": labels,
        "compute_units": cu,
    }
    if row["net_usd"] <= 0:
        row["gap_usd"] = round(abs(row["net_usd"]) + tip_usd, 6)
        row["roi"] = round(gross_usd / max(notional, 1e-9), 6)
    return row, quotes


def fetch_jupiter_roundtrips(
    amounts_lamports: list[int] | None = None,
    priority_median: int | None = None,
) -> dict[str, Any]:
    """Multi-pair Jupiter round-trips ranked by net after CU + Jito tip.

    Jupiter aggregators mix Orca / Raydium / Meteora / etc. — treat unique
    routePlan labels as cross-venue hops. Quotes run 4-wide (IO bound).
    """
    out: dict[str, Any] = {
        "ok": False, "opps": [], "near": [], "error": None,
        "ts": int(time.time()), "quotes": 0, "pairs_tried": 0,
        "mode": "jup-smart",
    }
    try:
        sol_px = fetch_sol_price() or 0.0
        min_usd = float(os.environ.get("MIN_SOL_ARB_USD", "0.05") or 0.05)
        jobs: list[tuple] = []
        for inp, mid, mid_label in ARB_PAIRS:
            for amt in _sizes_for(inp, mid, amounts_lamports):
                jobs.append((inp, mid, mid_label, amt))
        out["pairs_tried"] = len(jobs)
        rows_live, rows_near = [], []
        quotes = 0
        workers = min(4, max(1, len(jobs)))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [
                ex.submit(
                    _roundtrip_row, inp, mid, lab, amt,
                    sol_px, priority_median, min_usd,
                )
                for inp, mid, lab, amt in jobs
            ]
            for fut in as_completed(futs):
                try:
                    row, nq = fut.result()
                except Exception:
                    continue
                quotes += nq
                if not row:
                    continue
                if row["net_usd"] > 0:
                    rows_live.append(row)
                else:
                    rows_near.append(row)
        rows_live.sort(
            key=lambda r: (r.get("score") or 0, r.get("net_usd") or -1e9),
            reverse=True)
        rows_near.sort(key=lambda r: r.get("gap_usd") or 1e9)
        out["opps"] = rows_live[:16]
        out["near"] = rows_near[:16]
        out["quotes"] = quotes
        out["ok"] = True
        out["tip_usd"] = round(
            _prio_cost_usd(priority_median, sol_px, _cu_estimate(4)), 6)
        out["priority_median"] = priority_median
        out["sol_px"] = sol_px
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"
    return out


WALLET_META = {
    "funder": {
        "role": "capital",
        "target_sol": None,
        "note": "send SOL from here to sponsor + bot",
    },
    "sponsor": {
        "role": "tips",
        "target_sol": SPONSOR_TARGET_SOL,
        "note": "Jito tip + priority-fee payer",
    },
    "bot": {
        "role": "fee payer",
        "target_sol": BOT_TARGET_SOL,
        "note": "swap CU fees + small inventory",
    },
}


def fund_guide() -> dict[str, Any]:
    w = current_wallets()
    return {
        "from_role": "funder",
        "from_pubkey": w.get("funder") or "",
        "sponsor": w.get("sponsor") or "",
        "bot": w.get("bot") or "",
        "sponsor_target_sol": SPONSOR_TARGET_SOL,
        "bot_target_sol": BOT_TARGET_SOL,
        "sponsor_role": WALLET_META["sponsor"]["note"],
        "bot_role": WALLET_META["bot"]["note"],
        "note": ("From funder send "
                 f"{SPONSOR_TARGET_SOL} SOL to sponsor (tips) and "
                 f"{BOT_TARGET_SOL} SOL to bot (fees)."),
    }


def fetch_wallet_balances(pubkeys: dict[str, str]) -> dict[str, dict]:
    """SOL balance (lamports) for configured pubkeys; skip empties."""
    funds = {}
    wallets = dict(pubkeys or current_wallets())
    if not wallets.get("bot"):
        pk = _pubkey_from_keypair_file(os.environ.get("SOL_KEYPAIR", ""))
        if pk:
            wallets["bot"] = pk
    for name in ("funder", "sponsor", "bot"):
        pk = wallets.get(name) or ""
        meta = WALLET_META.get(name, {})
        if not pk or len(pk) < 32:
            funds[name] = {
                "sol": None, "configured": False,
                "role": meta.get("role"),
                "target_sol": meta.get("target_sol"),
                "note": meta.get("note"),
            }
            continue
        try:
            res, _ = sol_rpc("getBalance", [pk], timeout=6)
            lamports = int((res or {}).get("value") or 0) if isinstance(res, dict) else int(res or 0)
            sol = lamports / 1e9
            target = meta.get("target_sol")
            funds[name] = {
                "sol": sol,
                "lamports": lamports,
                "configured": True,
                "pubkey": pk,
                "role": meta.get("role"),
                "target_sol": target,
                "shortfall_sol": (
                    round(max(0.0, float(target) - sol), 4)
                    if target is not None else None),
                "note": meta.get("note"),
            }
        except Exception as e:  # noqa: BLE001
            funds[name] = {
                "sol": None, "configured": True, "pubkey": pk,
                "role": meta.get("role"),
                "target_sol": meta.get("target_sol"),
                "note": meta.get("note"),
                "error": f"{type(e).__name__}",
            }
    return funds


def _pubkey_from_keypair_file(path: str) -> str | None:
    if not path:
        return None
    path = os.path.expanduser(path)
    if not os.path.isfile(path):
        return None
    try:
        import base58  # type: ignore
        with open(path) as f:
            raw = json.loads(f.read())
        if isinstance(raw, list) and len(raw) >= 64:
            secret = bytes(raw[:64])
            pub = secret[32:]
            return base58.b58encode(pub).decode()
    except Exception:
        return None
    return None


def json_load_safe(text: str):
    return json.loads(text)


def _generate_solana_keypair() -> tuple[list[int], str]:
    """Ed25519 keypair in solana-keygen JSON form (seed[32]+pubkey[32])."""
    from nacl.signing import SigningKey  # type: ignore
    import base58  # type: ignore
    sk = SigningKey.generate()
    seed, pub = bytes(sk), bytes(sk.verify_key)
    return list(seed + pub), base58.b58encode(pub).decode()


def _ensure_keypair_file(path: str) -> str:
    existing = _pubkey_from_keypair_file(path)
    if existing:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return existing
    blob, pk = _generate_solana_keypair()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(blob, f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return pk


def _upsert_env_keys(path: str, updates: dict[str, str]) -> None:
    lines: list[str] = []
    if os.path.isfile(path):
        with open(path) as f:
            lines = f.read().splitlines()
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        raw = line.strip()
        if raw and not raw.startswith("#") and "=" in raw:
            k = raw.split("=", 1)[0].strip()
            if k in updates:
                out.append(f"{k}={updates[k]}")
                seen.add(k)
                continue
        out.append(line)
    for k, v in updates.items():
        if k not in seen:
            out.append(f"{k}={v}")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write("\n".join(out) + "\n")


def ensure_runtime_keypairs() -> dict[str, str]:
    """Create sponsor + bot keypairs once; write pubkeys only to pubkeys.json."""
    os.makedirs(KEYS_DIR, exist_ok=True)
    sponsor_pk = _ensure_keypair_file(SPONSOR_KEY_PATH)
    bot_pk = _ensure_keypair_file(BOT_KEY_PATH)
    funder_pk = (os.environ.get("SOL_FUNDER") or DEFAULT_SOL_FUNDER).strip()
    os.environ.setdefault("SOL_SPONSOR", sponsor_pk)
    os.environ.setdefault("SOL_BOT", bot_pk)
    os.environ.setdefault("SOL_KEYPAIR", BOT_KEY_PATH)
    pub = {
        "funder": funder_pk,
        "sponsor": sponsor_pk,
        "bot": bot_pk,
        "sponsor_role": WALLET_META["sponsor"]["note"],
        "bot_role": WALLET_META["bot"]["note"],
        "sponsor_target_sol": SPONSOR_TARGET_SOL,
        "bot_target_sol": BOT_TARGET_SOL,
        "fund_from": "funder",
        "keypair_paths": {
            "sponsor": "solana/keys/sponsor.json",
            "bot": "solana/keys/bot.json",
        },
        "note": "private keys are 600 JSON files — never commit or print them",
    }
    pub_path = os.path.join(KEYS_DIR, "pubkeys.json")
    with open(pub_path, "w") as f:
        json.dump(pub, f, indent=2)
        f.write("\n")
    env_path = os.path.join(_HERE, "solana", ".env")
    try:
        _upsert_env_keys(env_path, {
            "SOL_FUNDER": funder_pk,
            "SOL_SPONSOR": sponsor_pk,
            "SOL_BOT": bot_pk,
            "SOL_KEYPAIR": BOT_KEY_PATH,
        })
    except OSError:
        pass
    return pub


# Create wallets on import so the dashboard always has fundable addresses
try:
    ensure_runtime_keypairs()
except Exception:
    pass
SOL_WALLETS = current_wallets()


def recent_program_sigs(program_id: str, limit: int = 12) -> list[dict]:
    """Recent signatures for a program — competitor/liq event stubs."""
    try:
        res, _ = sol_rpc(
            "getSignaturesForAddress",
            [program_id, {"limit": limit}],
            timeout=10,
        )
        rows = []
        for s in res or []:
            rows.append({
                "sig": s.get("signature"),
                "slot": s.get("slot"),
                "err": s.get("err"),
                "age": None,
                "pair": "solend",
                "searcher": "—",
                "user": "—",
                "gas_usd": None,
                "est": None,
                "net": None,
                "flags": "revert" if s.get("err") else "ok",
                "tx": (s.get("signature") or "")[:12] + "…",
            })
        return rows
    except Exception:
        return []


def decode_solend_competitors(limit: int = 10) -> dict[str, Any]:
    """Fetch recent Solend program txs and label liquidate-like logs.

    Does not invent PnL — only labels when log messages match liquidate.
    """
    out: dict[str, Any] = {
        "ok": False, "rows": [], "liq_n": 0, "revert_n": 0,
        "error": None, "ts": int(time.time()),
    }
    try:
        sigs = recent_program_sigs(SOLEND_PROGRAM, limit=limit)
        rows = []
        liq_n = revert_n = 0
        for s in sigs:
            sig = s.get("sig")
            if not sig:
                continue
            labeled = dict(s)
            if s.get("err"):
                revert_n += 1
            try:
                tx, _ = sol_rpc(
                    "getTransaction",
                    [sig, {"encoding": "json", "maxSupportedTransactionVersion": 0}],
                    timeout=12,
                )
            except Exception:
                tx = None
            if isinstance(tx, dict):
                meta = tx.get("meta") or {}
                logs = meta.get("logMessages") or []
                joined = "\n".join(logs)
                is_liq = any(
                    k in joined for k in (
                        "LiquidateObligation", "liquidate_obligation",
                        "LiquidateObligationAndRedeemReserveCollateral",
                        "Instruction: Liquidate",
                    )
                )
                fee_payer = None
                msg = (tx.get("transaction") or {}).get("message") or {}
                keys = msg.get("accountKeys") or []
                if keys:
                    k0 = keys[0]
                    fee_payer = k0 if isinstance(k0, str) else (k0 or {}).get("pubkey")
                if is_liq:
                    liq_n += 1
                    labeled["flags"] = ("liq" if not s.get("err")
                                        else "liq revert")
                    labeled["pair"] = "solend-liq"
                    labeled["searcher"] = (
                        (fee_payer[:6] + "…" + fee_payer[-4:])
                        if fee_payer and len(fee_payer) > 12 else (fee_payer or "—")
                    )
                    labeled["user"] = "obligation"
                else:
                    # generic program activity
                    labeled["flags"] = labeled.get("flags") or "ok"
                    if fee_payer:
                        labeled["searcher"] = (
                            fee_payer[:6] + "…" + fee_payer[-4:]
                            if len(fee_payer) > 12 else fee_payer
                        )
            rows.append(labeled)
        out["rows"] = rows
        out["liq_n"] = liq_n
        out["revert_n"] = revert_n
        out["ok"] = True
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"
    return out
