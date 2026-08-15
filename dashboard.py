#!/usr/bin/env python3
"""Aave V4 + MEV live Web3 dashboard -- monitors + live broadcast.

Streams everything to a WebSocket frontend:
  - mempool (pending txs, decoded Aave V4 Spoke calls, watch addrs)
  - funds (funder / sponsor / BOT EOA ETH + USDC + USDT + WETH balances)
  - Aave oracle reserve prices + ETH price + gas
  - liquidatable opportunities (HF sweep + flash-liquidation plans)
  - competitor liquidations in confirmed blocks (with our-model profit estimate)
  - DEX arb round-trip scan (mev_bot)
  - intel / learning (readiness, hour/dow stats, dataset size)

With --broadcast (default on):
  - liquidations: sign + Flashbots eth_sendBundle via live_liquidator._submit
  - arb: cast send FlashLoanArbitrage plans when ARB_CONTRACT is set

Wallet funding remains user-initiated in the browser (MetaMask -> sponsor).
"""
import argparse
import asyncio
import json
import os
import socket
import sys
import time
from collections import deque, defaultdict
from concurrent.futures import ThreadPoolExecutor

# Force IPv4 everywhere: publicnode (and several peers) advertise IPv6 AAAA
# records that this host cannot route; Python tries IPv6 first and stalls for
# minutes, while curl's -4 works instantly.
_orig_getaddrinfo = socket.getaddrinfo


def _ipv4_only(host, port, family=0, type=0, proto=0, flags=0):
    return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)


socket.getaddrinfo = _ipv4_only

import requests as _req  # noqa: E402

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def _rpc_requests(url, method, params, timeout=10, retries=2):
    """Drop-in replacement for aave_v4_monitor.rpc using requests (browser UA,
    short timeout, fast failure) instead of urllib (which publicnode throttles
    / which stalls on unreachable IPv6)."""
    last = None
    for _attempt in range(retries):
        try:
            r = _req.post(url,
                          json={"jsonrpc": "2.0", "id": 1,
                                "method": method, "params": params},
                          headers={"Content-Type": "application/json",
                                   "User-Agent": _UA},
                          timeout=timeout)
            r.raise_for_status()
            out = r.json()
            if "error" in out:
                raise RuntimeError(f"RPC error {method}: {out['error']}")
            return out["result"]
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(0.5)
    raise RuntimeError(f"RPC failed {method} on {url}: {last}")


HERE = os.path.dirname(os.path.abspath(__file__))
LQ = os.path.join(os.path.dirname(HERE), "aave-v4-liquidation-bot")
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "aave-v4-monitor"))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "defi-arb"))
sys.path.insert(0, LQ)

import aiohttp  # noqa: E402
import aiohttp.web  # noqa: E402
import liquidation_bot as lb  # noqa: E402
import live_liquidator as ll  # noqa: E402
import mev_liquidation as ml  # noqa: E402
import stealth  # noqa: E402
import broadcast  # noqa: E402
import aave_v4_monitor as avm  # noqa: E402
import intel_collector as ic  # noqa: E402
import intel_analyze as ia  # noqa: E402
import mev_bot  # noqa: E402
import profit_engine as pe  # noqa: E402
import profit_brain as brain  # noqa: E402
import sol_scanner as sols  # noqa: E402

# Route every RPC through the fast requests transport (see _rpc_requests).
avm.rpc = _rpc_requests
lb.rpc = _rpc_requests
mev_bot.rpc = _rpc_requests

# Live broadcast knobs. Prefer env, else contracts.json baked by deploy_both_mainnet.sh.
_CONTRACTS_JSON = os.path.join(HERE, "contracts.json")
_BAKED = {}
if os.path.exists(_CONTRACTS_JSON):
    try:
        import json as _json
        with open(_CONTRACTS_JSON) as _f:
            _BAKED = _json.load(_f) or {}
    except Exception:
        _BAKED = {}
if os.environ.get("LIQ_CONTRACT"):
    ml.CONTRACT = os.environ["LIQ_CONTRACT"]
elif _BAKED.get("LIQ_CONTRACT"):
    ml.CONTRACT = _BAKED["LIQ_CONTRACT"]
ARB_CONTRACT = os.environ.get("ARB_CONTRACT") or _BAKED.get("ARB_CONTRACT", "")
SOL_LIQ_PROGRAM = os.environ.get("SOL_LIQ_PROGRAM") or _BAKED.get("SOL_LIQ_PROGRAM", "")
SOL_ARB_PROGRAM = os.environ.get("SOL_ARB_PROGRAM") or _BAKED.get("SOL_ARB_PROGRAM", "")
MIN_LIQ_PROFIT_USD = float(os.environ.get("MIN_LIQ_PROFIT_USD", "10"))
MIN_ARB_PROFIT_USD = float(os.environ.get("MIN_ARB_PROFIT_USD", "5"))
MIN_SOL_ARB_USD = float(os.environ.get("MIN_SOL_ARB_USD", "0.05"))
LIQ_COOLDOWN_BLOCKS = int(os.environ.get("LIQ_COOLDOWN_BLOCKS", "50"))
EDGE_BIAS = os.environ.get("EDGE_BIAS", "1") != "0"
SIM_ONLY_DEFAULT = os.environ.get("SIM_ONLY", "1") != "0"
COLD_WALLET = os.environ.get("COLD_WALLET", "")  # optional profit sweep destination
SOL_SIM_ONLY_DEFAULT = os.environ.get("SOL_SIM_ONLY", "1") != "0"

# AAVE_RPC env var collapses lb.RPC_CALL to a single endpoint; force the full
# healthy pool so one rate-limited node never blanks a whole loop cycle.
_RPC_POOL = [
    "https://ethereum-rpc.publicnode.com",
    "https://ethereum.publicnode.com",
    "https://rpc.flashbots.net",
    "https://eth.drpc.org",
    "https://1rpc.io/eth",
    "https://eth-mainnet.public.blastapi.io",
    "https://rpc.mevblocker.io",
]
lb.RPC_CALL = list(_RPC_POOL)
lb.RPC_LOG = list(_RPC_POOL)

# Per-RPC failure cooldown so a throttled node is skipped (with backoff) while
# healthy peers take the load, and bursty transient errors don't spam "error".
_RPC_COOLDOWN = {}
_RPC_FAILS = {}


def _healthy_jrpc(urls, method, params):
    now = time.time()
    last = None
    for url in urls:
        if url in _RPC_COOLDOWN and now < _RPC_COOLDOWN[url]:
            continue
        try:
            out = _rpc_requests(url, method, params)
            _RPC_FAILS.pop(url, None)
            _RPC_COOLDOWN.pop(url, None)
            return out
        except Exception as e:  # noqa: BLE001
            last = e
            n = _RPC_FAILS.get(url, 0) + 1
            _RPC_FAILS[url] = n
            _RPC_COOLDOWN[url] = now + min(60 * (2 ** min(n, 3)), 300)
    raise RuntimeError(f"all RPCs failed for {method}: {last}")


lb.jrpc = _healthy_jrpc
avm.jrpc = _healthy_jrpc

# mev_bot calls rpc(RPC, ...) on a single module-level URL (publicnode); route
# every one of its calls through the full health-gated pool instead.
def _mev_rpc(url, method, params):
    return _healthy_jrpc(lb.RPC_CALL, method, params)


mev_bot.rpc = _mev_rpc

# mev_bot's eth_call comes from aave_v4_monitor, which calls rpc(url, ...) with
# the single publicnode URL it was given -- the same node the txpool_content
# refresh throttles. Route eth_call through the pool so scan/build_universe
# quote RPCs skip throttled peers instead of crawling behind the content dump.
def _mev_eth_call(url, to, data):
    return _healthy_jrpc(lb.RPC_CALL, "eth_call",
                         [{"to": to, "data": data}, "latest"])


mev_bot.eth_call = _mev_eth_call
avm.eth_call = _mev_eth_call

# Binance klines cache: symbol/interval/limit -> (fetched_ts, candles)
_KLINES_CACHE = {}
_KLINES_TTL = 15
_KLINES_INTERVALS = frozenset({"1m", "5m", "15m", "1h", "4h", "1d"})


def _fetch_klines(symbol, interval, limit):
    r = _req.get(f"https://api.binance.com/api/v3/klines",
                 params={"symbol": symbol, "interval": interval, "limit": limit},
                 headers={"User-Agent": _UA}, timeout=12)
    r.raise_for_status()
    out = r.json()
    return [[int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]),
             float(k[5])] for k in out]

LIQ_SEL = "0xc2fa746c"

# Known mainnet destinations for mempool top-to labeling
_MP_LABELS = {
    "0xdac17f958d2ee523a2206206994597c13d831ec7": ("USDT", "token"),
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": ("USDC", "token"),
    "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": ("WETH", "token"),
    "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599": ("WBTC", "token"),
    "0x6b175474e89094c44da98b954eedeac495271d0f": ("DAI", "token"),
    "0x7a250d5630b4cf539739df2c5dacb4c659f2488d": ("UniV2 Router", "router"),
    "0xe592427a0aece92de3edee1f18e0157c05861564": ("UniV3 Router", "router"),
    "0x68b3465833fb72a70ecdf485e0e4c7bd8665fc45": ("Uni Universal", "router"),
    "0x3fc91a3afd70395cd496c647d5a6cc9d4b2b7fad": ("Uni Universal", "router"),
    "0x1111111254eeb25477b68fb85ed929f73a960582": ("1inch", "router"),
    "0xdef1c0ded9bec7f1a1670819833240f027b25eff": ("0x Exchange", "router"),
    "0xba12222222228d8ba445958a75a0704d566bf2c8": ("Balancer", "router"),
    "0xd9e1ce17f2641f24ae83637ab66a2cca9c378b9f": ("Sushi Router", "router"),
    "0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2": ("Aave V3 Pool", "lending"),
    lb.SPOKE.lower(): ("Aave V4 Spoke", "lending"),
}

_SEL_NAMES = {
    "0xc2fa746c": "liquidationCall",
    "0x7ff36ab5": "swapExactETHForTokens",
    "0x18cbafe5": "swapExactTokensForETH",
    "0x38ed1739": "swapExactTokensForTokens",
    "0xb6f9de95": "swapExactETHForTokensFee",
    "0x414bf389": "exactInputSingle",
    "0xc04b8d59": "exactInput",
    "0xdb3e2198": "exactOutputSingle",
    "0x5ae401dc": "multicall",
    "0x3593564c": "execute",
    "0xac9650d8": "multicall",
    "0x095ea7b3": "approve",
    "0xa9059cbb": "transfer",
    "0x23b872dd": "transferFrom",
    "0x60806040": "contractCreate",
}

_MEV_PRI = {"liq": 0, "spoke": 1, "aave": 2, "router": 3, "create": 4, "other": 5}


def _mp_label(addr: str) -> str:
    a = (addr or "").lower()
    hit = _MP_LABELS.get(a)
    if hit:
        return hit[0]
    return (a[:10] + "…") if a.startswith("0x") else "?"


def _mp_kind(addr: str) -> str:
    a = (addr or "").lower()
    hit = _MP_LABELS.get(a)
    return hit[1] if hit else "other"


def _mp_pressure(pend: int, mev: dict) -> str:
    liq = int((mev or {}).get("liq") or 0)
    router = int((mev or {}).get("router") or 0)
    if liq > 0:
        return "hot"
    if pend > 150_000 or router > 800:
        return "elevated"
    if pend > 60_000 or router > 200:
        return "busy"
    if pend > 10_000:
        return "quiet"
    return "idle"


def _classify_tx(tx: dict) -> str:
    to = (tx.get("to") or "").lower()
    inp = (tx.get("input") or tx.get("selector") or "").lower()
    if not to:
        return "create"
    if inp[:10] in ic.LIQ_SELS or inp[:10] == LIQ_SEL:
        return "liq"
    if to in ic.ROUTERS:
        return "router"
    if to == lb.SPOKE.lower():
        return "spoke"
    if to in ic.AAVE_POOLS:
        return "aave"
    return "other"


def _build_live_mev(txs, limit: int = 40) -> list:
    """Full-hash live MEV txs (liq/spoke/aave/router), gas-sorted — not truncated samples."""
    rows = []
    for t in txs or []:
        cls = _classify_tx(t)
        if cls == "other":
            continue
        to = (t.get("to") or "").lower()
        sel = ((t.get("input") or "")[:10] or "").lower()
        try:
            gp = int(t.get("gasPrice") or "0x0", 16)
        except Exception:
            gp = 0
        try:
            tip = int(t.get("maxPriorityFeePerGas") or "0x0", 16)
        except Exception:
            tip = 0
        hx = t.get("hash") or ""
        rows.append({
            "hash": hx,
            "hash_short": (hx[:10] + "…") if hx else "--",
            "cls": cls,
            "to": to,
            "to_short": to[:10] + "…" if to else "create",
            "label": _mp_label(to) if to else "create",
            "kind": _mp_kind(to) if to else "create",
            "sel": sel,
            "sel_name": _SEL_NAMES.get(sel, sel or "--"),
            "gas_gwei": round(gp / 1e9, 3) if gp else None,
            "tip_gwei": round(tip / 1e9, 3) if tip else None,
            "from": (t.get("from") or "")[:10],
            "etherscan": f"https://etherscan.io/tx/{hx}" if len(hx) >= 66 else "",
        })
    rows.sort(key=lambda r: (
        _MEV_PRI.get(r["cls"], 9),
        -(r["tip_gwei"] or 0),
        -(r["gas_gwei"] or 0),
    ))
    return rows[:limit]
LIQ_EVENT = "0x2a1f12d996f530f89d8038aa293f9fde81cac44b6dfd6225e3358d09b78a4a37"


def _discover_spokes():
    """Enumerate every Aave V4 spoke on mainnet (shared across assets) so the
    competitor watcher catches liquidationCall txs on all of them, not just
    liquidation_bot's default WETH spoke. Filters by eth_getCode != 0x so
    cross-chain spoke addresses (which don't exist on mainnet) are skipped."""
    url = lb.RPC_CALL[0]
    n = avm.call_uint(url, avm.HUB, avm._SEL_GET_ASSET_COUNT)
    addrs = set()
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=8) as ex:
        for sps in ex.map(lambda i: avm.get_spokes(url, i), range(n)):
            for sp in sps:
                addrs.add(sp["address"].lower())
    live = set()
    for a in addrs:
        try:
            code = lb.jrpc(lb.RPC_CALL, "eth_getCode", [a, "latest"])
            if code and code != "0x":
                live.add(a)
        except Exception:
            continue
    live.add(lb.SPOKE.lower())
    if not live:
        live = {lb.SPOKE.lower()}
    return sorted(live), n

WALLETS = {
    "funder": "0xffD2A2f73c49C7d90e0616a2492C076d90Bc17e9",
    "sponsor": "0xfcc8598e8297d86cd3a1595213deaee50e56a265",
    "bot": "0xc2424436dEA633fD743247731D4918f43d5e8bf6",
}
TOKENS = {
    "USDC": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
    "USDT": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
    "WETH": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
}
TOKEN_DEC = {"USDC": 6, "USDT": 6, "WETH": 18}
SELECTOR_ERC20 = {"USDC": "0x70a08231", "USDT": "0x70a08231", "WETH": "0x70a08231"}
RESERVE_SYMS = {0: "WETH", 1: "USDC", 2: "USDT", 3: "GHO", 4: "GDOLLAR", 5: "FRAX",
                6: "EURC", 7: "RLUSD", 8: "cbBTC", 9: "WBTC", 10: "AAVE", 11: "LINK",
                12: "wstETH", 13: "weETH"}

TICK = 2.5
MAXLEN = 120


class Dashboard:
    def __init__(self):
        self.started = time.time()
        self.state = {
            "chain": "mainnet",
            "block": None,
            "gas_gwei": None,
            "gas_class": None,
            "eth_price_usd": None,
            "funds": {k: {"eth": 0, "usdc": 0, "usdt": 0, "weth": 0} for k in WALLETS},
            "performance": {},
            "mempool": {
                "count": 0, "queued": 0, "method": None,
                "spoke_txs": [], "watch_txs": [],
                "mev": {}, "mev_txs": [], "mev_samples": [],
                "top_to": [], "top_mev": [],
                "meta": {
                    "pending": 0, "queued": 0, "content_age_s": None,
                    "contested": 0, "mev_share_pct": 0, "pressure": "idle",
                    "liq": 0, "router": 0, "spoke": 0,
                },
            },
            "prices": {"reserves": {}, "deltas": []},
            "opportunities": [],
            "watchlist": [],
            "opportunities_meta": {
                "count": 0, "edge_n": 0, "best_profit": 0, "sum_profit": 0,
                "sweep_total": 0, "watch_n": 0, "avg_hf": None,
                "pressure": "idle", "pair_mix": [],
            },
            "competitors": [],
            "competitors_meta": {
                "spokes": 1, "assets": 0, "status": "init",
                "count_1h": 0, "unique_searchers": 0,
                "avg_gas": None, "sum_est_profit": 0,
                "sum_net_est": 0, "missed_by_us": 0,
                "miss_rate_pct": 0, "edge_n": 0, "revert_n": 0,
                "pressure": "idle", "top_searchers": [], "pair_mix": [],
                "last_block": None, "total": 0,
            },
            "arb": {
                "opps": [], "near": [], "stats": {}, "error": None,
                "meta": {
                    "scan_ms": None, "scan_block": None, "gas_gwei": None,
                    "live": 0, "near": 0, "actionable": 0,
                    "best_net_usd": None, "top_mid": None,
                    "preferred_mids": [], "pressure": "idle",
                    "dexes": [], "by_dex": {}, "cross_dex": 0,
                    "venue_mix": [], "mode": "dash",
                },
            },
            "intel": {"records": 0, "readiness": 0, "hours": {}, "dows": {},
                      "moves": 0, "last": None, "mev": {},
                      "brain": brain.policy({})},
            "bots": {b: {"status": "idle", "last": None, "msg": ""}
                     for b in ("mempool", "prices", "funds", "sweep",
                               "competitors", "arb", "intel", "broadcast")},
            "broadcast": {
                "enabled": True,
                "armed": False,          # must arm for real eth_sendBundle / cast send
                "sim_only": SIM_ONLY_DEFAULT,
                "edge_bias": EDGE_BIAS,
                "liq_contract": ml.CONTRACT or "",
                "arb_contract": ARB_CONTRACT,
                "min_liq_profit_usd": MIN_LIQ_PROFIT_USD,
                "min_arb_profit_usd": MIN_ARB_PROFIT_USD,
                "dyn_min_liq": MIN_LIQ_PROFIT_USD,
                "dyn_min_arb": MIN_ARB_PROFIT_USD,
                "peak_hour": False,
                "sponsor_target_eth": 0.03,
                "ready": {"liq": False, "arb": False, "reasons": []},
                "last_liq": None,
                "last_arb": None,
                "history": [],
                "near_miss_hints": [],
                "skipped": [],
                "pressure": "idle",
                "summary": {
                    "pressure": "idle", "label": "idle",
                    "n_hist": 0, "n_sent": 0, "n_sim": 0, "n_skip": 0,
                    "last_stage": None, "last_kind": None,
                },
            },
            "log": [],
            "log_meta": {
                "session_total": 0,
                "by_level": {},
                "by_cat": {},
                "last_ts": None,
            },
            "sol": self._init_sol_state(),
        }
        self.hist = {
            "tx_count": deque(maxlen=MAXLEN),
            "tx_queued": deque(maxlen=MAXLEN),
            "tx_mev": deque(maxlen=MAXLEN),
            "comp_1h": deque(maxlen=MAXLEN),
            "comp_missed": deque(maxlen=MAXLEN),
            "arb_best_net": deque(maxlen=MAXLEN),
            "arb_actionable": deque(maxlen=MAXLEN),
            "gas": deque(maxlen=MAXLEN),
            "eth": deque(maxlen=MAXLEN),
            "reserves": {rid: deque(maxlen=MAXLEN) for rid in RESERVE_SYMS},
            "sol_fee_median": deque(maxlen=MAXLEN),
            "sol_fee_p90": deque(maxlen=MAXLEN),
            "sol_tps": deque(maxlen=MAXLEN),
            "sol_arb_best_net": deque(maxlen=MAXLEN),
            "sol_arb_actionable": deque(maxlen=MAXLEN),
            "sol_comp_1h": deque(maxlen=MAXLEN),
        }
        self.clients = set()
        self.tx_pool = ThreadPoolExecutor(max_workers=8)
        self._uni = None
        self._spokes = {lb.SPOKE.lower()}
        self._spokes_fut = None
        self._liq_alerted = {}  # user -> block
        self.broadcast_enabled = True
        self.armed = False
        self.sim_only = SIM_ONLY_DEFAULT
        self.edge_bias = EDGE_BIAS
        self._price_moved = False
        self._arb_quoted_block = None
        self._contested = set()
        self.sol_armed = False
        self.sol_sim_only = SOL_SIM_ONLY_DEFAULT
        self.sol_edge_bias = True

    @staticmethod
    def _sol_funds_seed() -> tuple:
        w = sols.current_wallets()
        guide = sols.fund_guide()
        funds = {}
        for name in ("funder", "sponsor", "bot"):
            meta = sols.WALLET_META.get(name) or {}
            pk = w.get(name) or ""
            funds[name] = {
                "sol": None,
                "configured": bool(pk),
                "pubkey": pk or None,
                "role": meta.get("role"),
                "target_sol": meta.get("target_sol"),
                "note": meta.get("note"),
            }
        return funds, guide

    @staticmethod
    def _init_sol_state():
        bots = ("mempool", "prices", "funds", "sweep",
                "competitors", "arb", "intel", "broadcast")
        funds, guide = Dashboard._sol_funds_seed()
        return {
            "chain": "solana-mainnet",
            "protocol": sols.PROTOCOL,
            "protocol_note": sols.PROTOCOL_NOTE,
            "slot": None,
            "epoch": None,
            "slot_index": None,
            "slots_in_epoch": None,
            "absolute_slot": None,
            "priority_fee": None,
            "sol_price_usd": None,
            "rpc": None,
            "funds": funds,
            "fund_guide": guide,
            "performance": {
                "grade": "C",
                "verdict": "sponsor + bot created — fund from funder",
                "equity_usd": None, "session_pnl": 0, "realized": 0,
                "sim": 0, "hit_rate": None, "missed": 0,
                "edge": (
                    f"from funder send {sols.SPONSOR_TARGET_SOL} SOL → sponsor "
                    f"and {sols.BOT_TARGET_SOL} SOL → bot"
                ),
                "ledger": [],
            },
            "mempool": {
                "count": 0, "queued": 0, "method": "getRecentPrioritizationFees",
                "spoke_txs": [], "watch_txs": [],
                "mev": {}, "mev_txs": [], "mev_samples": [],
                "top_to": [], "top_mev": [],
                "meta": {
                    "pending": 0, "queued": 0, "content_age_s": None,
                    "contested": 0, "mev_share_pct": 0, "pressure": "idle",
                    "median_fee": None, "p90_fee": None, "p99_fee": None,
                    "avg_fee": None, "max_fee": None, "zero_pct": 0,
                    "slots": 0, "tps": None, "nv_tps": None,
                    "note": "getRecentPrioritizationFees · microlamports/CU",
                },
            },
            "opportunities": [],
            "watchlist": [],
            "opportunities_meta": {
                "count": 0, "edge_n": 0, "best_profit": 0, "sum_profit": 0,
                "sweep_total": 0, "watch_n": 0, "avg_hf": None,
                "pressure": "idle", "pair_mix": [],
                "protocol": sols.PROTOCOL, "status": "init",
            },
            "competitors": [],
            "competitors_meta": {
                "spokes": 1, "assets": 0, "status": "init",
                "count_1h": 0, "unique_searchers": 0,
                "avg_gas": None, "sum_est_profit": 0,
                "sum_net_est": 0, "missed_by_us": 0,
                "miss_rate_pct": 0, "edge_n": 0, "revert_n": 0,
                "pressure": "idle", "top_searchers": [], "pair_mix": [],
                "last_slot": None, "total": 0,
                "note": "Solend program sigs when discoverable — no fake liqs",
            },
            "arb": {
                "opps": [], "near": [], "stats": {}, "error": None,
                "meta": {
                    "scan_ms": None, "scan_slot": None, "gas_gwei": None,
                    "live": 0, "near": 0, "actionable": 0,
                    "best_net_usd": None, "top_mid": None,
                    "preferred_mids": [
                        "SOL-USDC", "SOL-USDT", "SOL-mSOL",
                        "SOL-JitoSOL", "USDC-USDT", "SOL-BONK",
                        "SOL-RAY", "SOL-WIF", "SOL-PYTH",
                    ],
                    "pressure": "idle",
                    "dexes": ["jupiter"], "by_dex": {}, "cross_dex": 0,
                    "venue_mix": [], "mode": "jup-smart",
                    "tip_usd": None, "quotes": 0,
                },
            },
            "intel": {
                "records": 0, "readiness": 0, "hours": {}, "dows": {},
                "moves": 0, "last": None, "mev": {},
                "brain": {"advice": "SOL twin warming — Solend + Jupiter probes",
                          "min_liq_mult": 1.0, "min_arb_mult": 1.0,
                          "prefer_edge": True},
                "act_p": None, "exp_net": None, "steps": 0,
            },
            "bots": {b: {"status": "idle", "last": None, "msg": ""}
                     for b in bots},
            "broadcast": {
                "enabled": True,
                "armed": False,
                "sim_only": SOL_SIM_ONLY_DEFAULT,
                "edge_bias": True,
                "liq_contract": SOL_LIQ_PROGRAM,
                "arb_contract": SOL_ARB_PROGRAM,
                "min_liq_profit_usd": MIN_LIQ_PROFIT_USD,
                "min_arb_profit_usd": MIN_SOL_ARB_USD,
                "dyn_min_liq": MIN_LIQ_PROFIT_USD,
                "dyn_min_arb": MIN_SOL_ARB_USD,
                "peak_hour": False,
                "sponsor_target_eth": 0,
                "ready": {"liq": False, "arb": False, "reasons": [
                    "SOL programs not deployed — see solana/README.md",
                ]},
                "last_liq": None,
                "last_arb": None,
                "history": [],
                "near_miss_hints": [],
                "skipped": [],
                "pressure": "idle",
                "summary": {
                    "pressure": "idle", "label": "idle",
                    "n_hist": 0, "n_sent": 0, "n_sim": 0, "n_skip": 0,
                    "last_stage": None, "last_kind": None,
                },
            },
            "prices": {"reserves": {}, "deltas": []},
            "log_meta": {
                "session_total": 0, "by_level": {}, "by_cat": {}, "last_ts": None,
            },
        }

    # ------------------------------------------------------------ helpers
    def log(self, cat, level, msg):
        line = {"ts": int(time.time()), "cat": cat, "level": level, "msg": str(msg)}
        self.state["log"].append(line)
        self.state["log"] = self.state["log"][-300:]
        meta = self.state.setdefault("log_meta", {
            "session_total": 0, "by_level": {}, "by_cat": {}, "last_ts": None,
        })
        meta["session_total"] = int(meta.get("session_total") or 0) + 1
        meta["last_ts"] = line["ts"]
        bl = meta.setdefault("by_level", {})
        bl[level] = int(bl.get(level) or 0) + 1
        bc = meta.setdefault("by_cat", {})
        bc[cat] = int(bc.get(cat) or 0) + 1
        print(f"[{cat}] {msg}", flush=True)
        for ws in list(self.clients):
            try:
                asyncio.run_coroutine_threadsafe(
                    ws.send_str(json.dumps({"type": "log", "line": line})),
                    self._loop,
                )
            except Exception:
                pass

    def _log_meta_snapshot(self):
        lines = self.state.get("log") or []
        meta = self.state.get("log_meta") or {}
        by_level, by_cat = {}, {}
        for l in lines:
            lvl = l.get("level") or "info"
            cat = l.get("cat") or "?"
            by_level[lvl] = by_level.get(lvl, 0) + 1
            by_cat[cat] = by_cat.get(cat, 0) + 1
        return {
            "session_total": int(meta.get("session_total") or len(lines)),
            "buffer": len(lines),
            "by_level": by_level,
            "by_cat": by_cat,
            "last_ts": (lines[-1]["ts"] if lines else meta.get("last_ts")),
        }
    def bot(self, name, status, msg=""):
        b = self.state["bots"][name]
        b["status"] = status
        b["last"] = int(time.time())
        b["msg"] = str(msg)[:200]

    def sol_bot(self, name, status, msg=""):
        bots = self.state.setdefault("sol", {}).setdefault("bots", {})
        b = bots.setdefault(name, {"status": "idle", "last": None, "msg": ""})
        b["status"] = status
        b["last"] = int(time.time())
        b["msg"] = str(msg)[:200]

    def refresh_sol_broadcast_ready(self):
        sol = self.state.setdefault("sol", {})
        bc = sol.setdefault("broadcast", {})
        reasons = []
        liq_ok = False
        arb_ok = False
        if not SOL_LIQ_PROGRAM:
            reasons.append("SOL_LIQ_PROGRAM unset — deploy solana/programs/liq")
        else:
            reasons.append("SOL_LIQ_PROGRAM set but mainnet CPI not armed in this build")
        if not SOL_ARB_PROGRAM:
            reasons.append("SOL_ARB_PROGRAM unset — deploy solana/programs/arb")
        else:
            reasons.append("SOL_ARB_PROGRAM set but Jupiter CPI stub only")
        if self.sol_sim_only:
            reasons.append("sol sim_only ON")
        if not self.sol_armed:
            reasons.append("sol not armed (POST /api/control {\"chain\":\"sol\",\"armed\":true})")
        # Never mark live-ready until programs are real + funded keypair present
        keypair = os.environ.get("SOL_KEYPAIR", "")
        if not keypair or not os.path.exists(os.path.expanduser(keypair)):
            reasons.append("SOL_KEYPAIR missing — funded keypair required for deploy/submit")
        bc.update({
            "enabled": True,
            "armed": self.sol_armed,
            "sim_only": self.sol_sim_only,
            "edge_bias": self.sol_edge_bias,
            "liq_contract": SOL_LIQ_PROGRAM,
            "arb_contract": SOL_ARB_PROGRAM,
            "min_liq_profit_usd": MIN_LIQ_PROFIT_USD,
            "min_arb_profit_usd": MIN_SOL_ARB_USD,
            "dyn_min_liq": MIN_LIQ_PROFIT_USD,
            "dyn_min_arb": MIN_SOL_ARB_USD,
            "ready": {"liq": liq_ok, "arb": arb_ok, "reasons": reasons},
        })
        hist = bc.get("history") or []
        skipped = bc.get("skipped") or []
        n_sent = sum(1 for h in hist if (h.get("stage") or "") in ("sent", "ok"))
        n_sim = sum(1 for h in hist if (h.get("stage") or "") == "simulated")
        pressure = "idle"
        label = "idle"
        if self.sol_armed and (liq_ok or arb_ok):
            pressure, label = "hot", "armed live"
        elif self.sol_sim_only:
            pressure, label = "quiet", "sim only"
        elif reasons:
            pressure, label = "blocked", "gates blocked"
        bc["pressure"] = pressure
        bc["summary"] = {
            "pressure": pressure, "label": label,
            "n_hist": len(hist), "n_sent": n_sent, "n_sim": n_sim,
            "n_skip": len(skipped),
            "last_stage": (hist[0].get("stage") if hist else None),
            "last_kind": (hist[0].get("kind") if hist else None),
        }
        return liq_ok, arb_ok, reasons

    def snapshot(self):
        s = self.state
        sol = s.get("sol") or {}
        out = {
            "started": self.started,
            "now": int(time.time()),
            "chain": s["chain"],
            "block": s["block"],
            "gas_gwei": s["gas_gwei"],
            "gas_class": s["gas_class"],
            "eth_price_usd": s["eth_price_usd"],
            "funds": s["funds"],
            "performance": s.get("performance") or {},
            "wallets": WALLETS,
            "mempool": {k: v for k, v in s["mempool"].items() if k != "txs"},
            "prices": s["prices"],
            "opportunities": s["opportunities"],
            "watchlist": s.get("watchlist", []),
            "sweep_total": s.get("sweep_total"),
            "opportunities_meta": s.get("opportunities_meta") or {},
            "competitors": s["competitors"],
            "competitors_meta": s["competitors_meta"],
            "arb": s["arb"],
            "intel": s["intel"],
            "bots": s["bots"],
            "broadcast": s["broadcast"],
            "log": s["log"],
            "log_meta": self._log_meta_snapshot(),
            "sol": {
                **{k: v for k, v in sol.items() if k != "bots"},
                "bots": sol.get("bots") or {},
                "wallets": sols.current_wallets(),
                "fund_guide": sols.fund_guide(),
            },
            "hist": {
                "tx_count": list(self.hist["tx_count"]),
                "tx_queued": list(self.hist["tx_queued"]),
                "tx_mev": list(self.hist["tx_mev"]),
                "comp_1h": list(self.hist["comp_1h"]),
                "comp_missed": list(self.hist["comp_missed"]),
                "arb_best_net": list(self.hist["arb_best_net"]),
                "arb_actionable": list(self.hist["arb_actionable"]),
                "gas": list(self.hist["gas"]),
                "eth": list(self.hist["eth"]),
                "reserves": {str(k): list(v) for k, v in self.hist["reserves"].items()},
                "sol_fee_median": list(self.hist.get("sol_fee_median") or []),
                "sol_fee_p90": list(self.hist.get("sol_fee_p90") or []),
                "sol_tps": list(self.hist.get("sol_tps") or []),
                "sol_arb_best_net": list(self.hist.get("sol_arb_best_net") or []),
                "sol_arb_actionable": list(self.hist.get("sol_arb_actionable") or []),
                "sol_comp_1h": list(self.hist.get("sol_comp_1h") or []),
            },
        }
        return out

    # ------------------------------------------------------------ broadcast
    def _contract_has_code(self, addr):
        if not addr or not str(addr).startswith("0x"):
            return False
        try:
            code = lb.jrpc(lb.RPC_CALL, "eth_getCode", [addr, "latest"])
            return bool(code and code != "0x")
        except Exception:
            return False

    def refresh_broadcast_ready(self):
        reasons = []
        liq_ok = False
        arb_ok = False
        gas = self.state.get("gas_gwei")
        eth = self.state.get("eth_price_usd")
        dyn_liq = pe.dynamic_min_liq_profit_usd(gas, MIN_LIQ_PROFIT_USD)
        dyn_arb = pe.dynamic_min_arb_profit_usd(gas, eth, MIN_ARB_PROFIT_USD)
        pol = brain.policy(self.state)
        self.state["intel"]["brain"] = pol
        dyn_liq *= float(pol.get("min_liq_mult") or 1.0)
        dyn_arb *= float(pol.get("min_arb_mult") or 1.0)
        if pol.get("prefer_edge"):
            self.edge_bias = True
        peak = pe.is_peak_hour(self.state.get("intel", {}).get("hours") or {})
        sponsor_tgt = pe.sponsor_target_eth(gas)
        if not self.broadcast_enabled:
            reasons.append("broadcast disabled (--no-broadcast)")
        else:
            if not self.armed and not self.sim_only:
                reasons.append("not armed (POST /api/control {\"armed\":true})")
            if not ml.CONTRACT:
                reasons.append("LIQ_CONTRACT unset — fund sponsor + deploy_both_mainnet.sh")
            elif not self._contract_has_code(ml.CONTRACT):
                reasons.append(
                    f"LIQ_CONTRACT {ml.CONTRACT[:10]}… has no code on mainnet")
            elif not lb.KEYSTORE_PW and not ll.SPONSOR_PW:
                reasons.append("KEYSTORE_PW / SPONSOR_PW missing")
            else:
                liq_ok = True
            if not ARB_CONTRACT:
                reasons.append("ARB_CONTRACT unset — fund sponsor + deploy_both_mainnet.sh")
            elif not self._contract_has_code(ARB_CONTRACT):
                reasons.append(f"ARB_CONTRACT {ARB_CONTRACT[:10]}… has no code")
            elif not mev_bot.KEYSTORE_PW:
                reasons.append("arb KEYSTORE_PW missing")
            else:
                arb_ok = True
        hist = self.state["broadcast"].get("history") or []
        skipped = self.state["broadcast"].get("skipped") or []
        n_sent = sum(1 for h in hist
                     if (h.get("stage") or "").lower() in ("sent", "ok"))
        n_sim = sum(1 for h in hist
                    if (h.get("stage") or "").lower() in ("simulated", "sim"))
        n_skip = len(skipped) + sum(
            1 for h in hist if (h.get("stage") or "").lower() == "skip")
        last = hist[0] if hist else None
        if not self.broadcast_enabled:
            pressure, label = "idle", "off"
        elif self.armed and (liq_ok or arb_ok):
            pressure, label = "hot", "armed live"
        elif self.armed:
            pressure, label = "elevated", "armed · blocked"
        elif self.sim_only and (liq_ok or arb_ok):
            pressure, label = "quiet", "sim ready"
        elif self.sim_only:
            pressure, label = "busy", "sim · blocked"
        elif liq_ok or arb_ok:
            pressure, label = "elevated", "ready · disarm"
        else:
            pressure, label = "busy", "blocked"
        summary = {
            "pressure": pressure,
            "label": label,
            "n_hist": len(hist),
            "n_sent": n_sent,
            "n_sim": n_sim,
            "n_skip": n_skip,
            "last_stage": (last or {}).get("stage") if last else None,
            "last_kind": (last or {}).get("kind") if last else None,
        }
        self.state["broadcast"].update({
            "enabled": self.broadcast_enabled,
            "armed": self.armed,
            "sim_only": self.sim_only,
            "edge_bias": self.edge_bias,
            "liq_contract": ml.CONTRACT or "",
            "arb_contract": ARB_CONTRACT,
            "min_liq_profit_usd": MIN_LIQ_PROFIT_USD,
            "min_arb_profit_usd": MIN_ARB_PROFIT_USD,
            "dyn_min_liq": round(dyn_liq, 2),
            "dyn_min_arb": round(dyn_arb, 2),
            "peak_hour": peak,
            "sponsor_target_eth": sponsor_tgt,
            "brain_advice": pol.get("advice"),
            "brain_act": pol.get("act_prob"),
            "ready": {"liq": liq_ok, "arb": arb_ok, "reasons": reasons},
            "near_miss_hints": pe.near_miss_hints(),
            "pressure": pressure,
            "summary": summary,
        })
        return liq_ok, arb_ok, reasons

    def _record_broadcast(self, kind, rec):
        entry = {"ts": int(time.time()), "kind": kind, **rec}
        self.state["broadcast"]["history"].insert(0, entry)
        self.state["broadcast"]["history"] = self.state["broadcast"]["history"][:40]
        if kind == "liq":
            self.state["broadcast"]["last_liq"] = entry
        elif kind == "arb":
            self.state["broadcast"]["last_arb"] = entry
        try:
            brain.learn_broadcast(self.state, kind, entry)
            self.state["intel"]["brain"] = brain.policy(self.state)
        except Exception:
            pass
        try:
            profit = rec.get("profit_usd")
            if profit is None and kind == "sweep" and rec.get("amount_eth"):
                eth = float(self.state.get("eth_price_usd") or 0)
                profit = float(rec["amount_eth"]) * eth
            pe.record_perf_event(
                kind, rec.get("stage") or rec.get("status") or "?",
                profit,
                detail=str(rec.get("reason") or rec.get("user") or "")[:160])
        except Exception:
            pass
        stage = rec.get("stage") or rec.get("status") or "?"
        self.log("broadcast", "warn" if stage in ("sent", "ok", "simulated") else "info",
                 f"{kind} {stage}: "
                 f"{rec.get('reason') or rec.get('user') or rec.get('msg') or ''}")
        self.bot("broadcast",
                 "ok" if stage in ("sent", "ok", "simulated") else "error",
                 f"{kind}:{stage}")

    def _broadcast_liquidation(self, user, profit_usd):
        """Sign + (sim|submit) a flash-liquidation bundle for `user`."""
        block = self.state["block"] or ll.latest_block()
        last = self._liq_alerted.get(user.lower(), 0)
        if block - last < LIQ_COOLDOWN_BLOCKS:
            return {"stage": "skip", "reason": "cooldown", "user": user}
        min_p = pe.dynamic_min_liq_profit_usd(
            self.state.get("gas_gwei"), MIN_LIQ_PROFIT_USD)
        try:
            min_p *= float((self.state.get("intel") or {}).get("brain", {})
                           .get("min_liq_mult") or 1.0)
        except Exception:
            pass
        if profit_usd is not None and profit_usd < min_p:
            return {"stage": "skip",
                    "reason": f"profit ${profit_usd} < dyn min ${min_p:.2f}",
                    "user": user}
        skip, why = pe.should_skip_user(
            user, self._contested,
            pe.recent_competitor_users(self.state.get("competitors") or []))
        if skip:
            self.state["broadcast"]["skipped"].insert(
                0, {"ts": int(time.time()), "user": user[:12], "why": why})
            self.state["broadcast"]["skipped"] = self.state["broadcast"]["skipped"][:30]
            return {"stage": "skip", "reason": why, "user": user}
        out = ml.build_full_plan(user)
        if out is None:
            return {"stage": "refuse", "reason": "no flash plan", "user": user}
        if self.edge_bias and not ml.is_long_tail(out):
            # still allow if profit is fat enough vs dyn min * 2
            if profit_usd is None or profit_usd < min_p * 2:
                return {"stage": "skip", "reason": "non-edge below fat floor",
                        "user": user}
        path = ll.emit_opportunity(user, out, block, ml.is_long_tail(out))
        signed_hex, signer, _ks = ll._sign_tx(out, block + 1, "bot")
        with open(path) as f:
            rec = json.load(f)
        rec["signer"] = signer
        rec["signed_tx"] = signed_hex[:66] + "..." + signed_hex[-8:]
        with open(path, "w") as f:
            json.dump(rec, f, indent=2)
        sponsor_hex = None
        if ll.SPONSOR_KEYSTORE and ll.SPONSOR_PW:
            # lean sponsor amount from gas
            ll.SPONSOR_AMOUNT_ETH = pe.sponsor_target_eth(self.state.get("gas_gwei"))
            sponsor_hex, sponsor_addr = ll._sign_sponsor(block + 1)
            body = broadcast.build_sponsored_bundle(
                signed_hex, sponsor_hex, block + 1)
            rec["sponsor"] = {"addr": sponsor_addr,
                              "amount_eth": ll.SPONSOR_AMOUNT_ETH}
            with open(path, "w") as f:
                json.dump(rec, f, indent=2)
        else:
            body = ll.build_bundle_body(signed_hex, block + 1)
        with open(path + ".bundle.json", "w") as f:
            json.dump(body, f, indent=2)
        stealth.write_relay_payloads(path + ".bundle", body)
        # sim-only unless armed for live
        do_sim_only = self.sim_only or not self.armed
        result = ll._submit(path, body, block + 1, sim_only=do_sim_only,
                            sponsor_hex=sponsor_hex)
        result["user"] = user
        result["path"] = path
        result["profit_usd"] = profit_usd
        result["sim_only"] = do_sim_only
        self._liq_alerted[user.lower()] = block
        return result

    def _broadcast_arb(self, opp, eth_usd):
        """Dry-run then optionally cast-send a DEX arb plan (fresh quote only)."""
        if not ARB_CONTRACT:
            return {"stage": "refuse", "reason": "ARB_CONTRACT unset"}
        now_block = self.state.get("block")
        if pe.arb_plan_stale(self._arb_quoted_block, now_block, max_blocks=1):
            return {"stage": "skip", "reason": "stale quote (>1 block)"}
        min_p = pe.dynamic_min_arb_profit_usd(
            self.state.get("gas_gwei"), eth_usd, MIN_ARB_PROFIT_USD)
        profit_usd = (opp["profit"] * eth_usd / 1e18) if eth_usd else 0
        if profit_usd < min_p:
            return {"stage": "skip",
                    "reason": f"profit ${profit_usd:.2f} < dyn min ${min_p:.2f}"}
        # gas-aware shrink
        depth = int(opp.get("flash_weth") or opp.get("borrow") or 0) * 4
        sized = pe.gas_aware_borrow_weth(
            int(opp["borrow"]), depth or int(opp["borrow"]),
            float(self.state.get("gas_gwei") or 1), float(eth_usd or 0),
            int(opp["profit"]))
        opp = dict(opp)
        opp["borrow"] = sized
        gas_token = mev_bot.gas_cost_token_wei()
        gas_price = mev_bot.eth_gas_price()
        sig, plan_json, _mp = mev_bot.plan_for(self._uni, opp, gas_token)
        ok, out = mev_bot.cast_call_plan(ARB_CONTRACT, sig, plan_json)
        if not ok:
            return {"stage": "sim-fail", "reason": str(out)[:240],
                    "profit_usd": round(profit_usd, 2)}
        if self.sim_only or not self.armed:
            return {"stage": "simulated", "reason": str(out)[:200],
                    "profit_usd": round(profit_usd, 2),
                    "flash": opp["flashPool"][:12], "sim_only": True}
        ok2, out2 = mev_bot.cast_send_plan(
            ARB_CONTRACT, sig, plan_json, gas_price)
        return {
            "stage": "sent" if ok2 else "error",
            "reason": str(out2)[:300],
            "profit_usd": round(profit_usd, 2),
            "flash": opp["flashPool"][:12],
            "sim_only": False,
        }

    # ------------------------------------------------------------ loops
    async def _run(self, fn, timeout, *a, **kw):
        """Run blocking RPC work in a thread with a hard per-cycle timeout so
        a hanging RPC never stalls the aiohttp event loop."""
        try:
            return await asyncio.wait_for(asyncio.to_thread(fn, *a, **kw), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    async def mempool_loop(self):
        while True:
            try:
                refresh = (time.time() - self.state["mempool"].get("last_content", 0)) > 180
                result = await self._run(self._mempool_poll, 80, refresh)
                pend, queued = result if result else (0, 0)
                s = self.state["mempool"]
                txs = s.get("txs") or []
                spoke = ic.spoke_txs(txs)
                watch = ic.watch_txs(txs, [lb.SPOKE])
                mv, _old_samples = ic.mev_classes(txs)
                live_mev = _build_live_mev(txs, limit=48)
                top = {}
                for t in txs:
                    to = (t.get("to") or "").lower()
                    if to.startswith("0x"):
                        top[to] = top.get(to, 0) + 1
                sampled_n = max(len(txs), 1)
                # rank: MEV-relevant destinations first, then by count
                ranked = sorted(
                    top.items(),
                    key=lambda kv: (
                        0 if _mp_kind(kv[0]) in ("router", "lending") else 1,
                        -kv[1],
                    ),
                )
                top_rows = []
                for addr, c in ranked[:14]:
                    kind = _mp_kind(addr)
                    top_rows.append({
                        "addr": addr,
                        "short": addr[:10],
                        "label": _mp_label(addr),
                        "kind": kind,
                        "count": c,
                        "pct": round(100.0 * c / sampled_n, 2),
                        "bar": min(100, round(100.0 * c / max(ranked[0][1], 1), 1)),
                        "etherscan": f"https://etherscan.io/address/{addr}",
                        "mev": kind in ("router", "lending"),
                    })
                top_mev = [r for r in top_rows if r["mev"]][:8]
                # enrich spoke rows for UI
                spoke_rows = []
                for t in spoke[:20]:
                    args = t.get("args") or []
                    user = ""
                    for a in args:
                        sa = str(a)
                        if "user=" in sa:
                            user = sa.split("user=", 1)[1].strip()[:42]
                            break
                        if sa.startswith("0x") and len(sa) >= 42:
                            user = sa[:42]
                            break
                    spoke_rows.append({
                        "name": t.get("name") or "?",
                        "args": [str(a)[:48] for a in args[:4]],
                        "user": user,
                        "user_short": (user[:10] + "…") if user else "",
                        "hot": (t.get("name") or "").lower().find("liquidat") >= 0,
                    })
                contested = pe.contested_users_from_mempool(spoke)
                self._contested = contested
                mev_hit = (int(mv.get("liq", 0)) + int(mv.get("router", 0))
                           + int(mv.get("spoke", 0)) + int(mv.get("aave", 0)))
                content_ts = s.get("last_content")
                age = int(time.time() - content_ts) if content_ts else None
                pressure = _mp_pressure(pend or len(txs), mv)
                now_ts = int(time.time())
                s.update({
                    "count": pend or len(txs),
                    "queued": queued,
                    "spoke_txs": spoke_rows,
                    "watch_txs": watch,
                    "mev": mv,
                    "mev_txs": live_mev,
                    "mev_samples": live_mev[:8],  # legacy alias
                    "top_to": top_rows,
                    "top_mev": top_mev,
                    "contested": sorted(contested)[:12],
                    "meta": {
                        "pending": pend or len(txs),
                        "queued": queued,
                        "content_age_s": age,
                        "contested": len(contested),
                        "mev_share_pct": round(100.0 * mev_hit / sampled_n, 2),
                        "mev_live": len(live_mev),
                        "pressure": pressure,
                        "liq": int(mv.get("liq") or 0),
                        "router": int(mv.get("router") or 0),
                        "spoke": int(mv.get("spoke") or 0),
                        "aave": int(mv.get("aave") or 0),
                        "create": int(mv.get("create") or 0),
                        "method": s.get("method"),
                        "sampled": len(txs),
                    },
                })
                self.hist["tx_count"].append([now_ts, pend or len(txs)])
                self.hist["tx_queued"].append([now_ts, queued])
                self.hist["tx_mev"].append([now_ts, mev_hit])
                self.bot("mempool", "ok",
                         f"{pend} pend + {queued} q ({s.get('method', '?')}) "
                         f"· {pressure} · MEV live={len(live_mev)} "
                         f"liq={mv['liq']} router={mv['router']}"
                         f"{f' contested={len(contested)}' if contested else ''}")
            except Exception as e:
                self.bot("mempool", "error", e)
            await asyncio.sleep(25 if pe.is_peak_hour(
                self.state.get("intel", {}).get("hours") or {}) else 40)

    def _mempool_poll(self, refresh):
        """Live pending-tx count via fast txpool_status + full txpool_content
        refresh (slow, publicnode-only) every ~60s for the spoke watch list."""
        out = self.state["mempool"]
        pend = queued = 0
        try:
            st = _healthy_jrpc(lb.RPC_CALL, "txpool_status", [])
            pend = int(st.get("pending", "0x0"), 16)
            queued = int(st.get("queued", "0x0"), 16)
        except Exception:
            pass
        if refresh:
            try:
                content = _rpc_requests(_RPC_POOL[0], "txpool_content", [],
                                        timeout=60, retries=1)
                txs = []
                for bucket in (content.get("pending") or {}).values():
                    for t in bucket.values():
                        txs.append({
                            "hash": t.get("hash", ""),
                            "from": t.get("from", ""),
                            "to": t.get("to") or "",
                            "value": t.get("value", "0x0"),
                            "input": (t.get("input") or "")[:10],
                            "gasPrice": t.get("gasPrice") or t.get("maxFeePerGas") or "0x0",
                            "maxPriorityFeePerGas": t.get("maxPriorityFeePerGas", "0x0"),
                            "gas": t.get("gas", "0x0"),
                        })
                out["txs"] = txs
                out["method"] = "txpool_content"
                out["last_content"] = int(time.time())
            except Exception:
                try:
                    got = avm.get_pending_txs(ic.MEMPOOL_RPC)
                    out["txs"] = got[0] or []
                    out["method"] = got[1] or "unavailable"
                    out["last_content"] = int(time.time())
                except Exception:
                    pass
        return pend, queued

    def _block_gas(self):
        blk = int(lb.jrpc(lb.RPC_CALL, "eth_blockNumber", []), 16)
        gas = int(lb.jrpc(lb.RPC_CALL, "eth_gasPrice", []), 16) / 1e9
        return blk, gas

    def _fetch_prices(self):
        n = 14
        try:
            n = lb.call32(lb.SPOKE, lb.SEL["getReserveCount"])
        except Exception:
            pass

        def safe_price(rid):
            try:
                return lb.get_reserve_price(rid)
            except Exception:
                return 0

        prices = list(self.tx_pool.map(safe_price, range(n)))
        return prices[:n]

    async def prices_loop(self):
        while True:
            try:
                bg = await self._run(self._block_gas, 55)
                if bg:
                    self.state["block"] = bg[0]
                    self.state["gas_gwei"] = round(bg[1], 1)
                    self.state["gas_class"] = ic.gas_class(bg[1])
                    self.hist["gas"].append([int(time.time()), round(bg[1], 1)])
                prices = await self._run(self._fetch_prices, 75)
                if prices is None:
                    self.bot("prices", "error", "reserve price fetch timed out")
                    await asyncio.sleep(20)
                    continue
                deltas = []
                reserves = {}
                for rid, v in enumerate(prices):
                    prev = self.state["prices"]["reserves"].get(str(rid))
                    if prev and v and prev != v:
                        pct = round((v - prev) / prev * 100.0, 2)
                        if abs(pct) > 0.5:
                            deltas.append([rid, RESERVE_SYMS.get(rid, str(rid)), pct])
                            self.log("price", "warn",
                                     f"reserve[{rid}] {RESERVE_SYMS.get(rid, '?')} "
                                     f"moved {pct:+.2f}%")
                    reserves[str(rid)] = v
                    self.hist["reserves"][rid].append([int(time.time()), v])
                self.state["prices"]["reserves"] = reserves
                self.state["prices"]["deltas"] = deltas
                if deltas:
                    self._price_moved = True
                    self.log("price", "warn",
                             f"{len(deltas)} reserve move(s) — fast sweep armed")

                if self._uni is None:
                    self._uni = await self._run(mev_bot.build_universe, 90)
                eth = 0.0
                if self._uni:
                    eth = await self._run(mev_bot.eth_price_usd, 30, self._uni) or 0.0
                self.state["eth_price_usd"] = round(eth, 2)
                self.hist["eth"].append([int(time.time()), round(eth, 2)])
                self.refresh_broadcast_ready()
                self.bot("prices", "ok",
                         f"block={self.state['block']} "
                         f"gas={self.state['gas_gwei']}gwei eth=${self.state['eth_price_usd']}"
                         f"{' PEAK' if self.state['broadcast'].get('peak_hour') else ''}")
            except Exception as e:
                self.bot("prices", "error", e)
            peak = pe.is_peak_hour(self.state.get("intel", {}).get("hours") or {})
            await asyncio.sleep(pe.prices_sleep_sec(25.0, peak))

    def _fetch_funds(self):
        for label, addr in WALLETS.items():
            bal = int(lb.jrpc(lb.RPC_CALL, "eth_getBalance", [addr, "latest"]), 16)
            self.state["funds"][label]["eth"] = bal / 1e18
        for sym, addr in TOKENS.items():
            for label, wal in WALLETS.items():
                d = "0x70a08231" + wal[2:].rjust(64, "0")
                r = lb.call(lb.RPC_CALL, addr, d)
                v = int(r, 16) / (10 ** TOKEN_DEC[sym])
                self.state["funds"][label][sym.lower()] = v

    async def funds_loop(self):
        while True:
            try:
                await self._run(self._fetch_funds, 60)
                bot_eth = float(self.state["funds"]["bot"]["eth"])
                do_sw, amt = pe.should_sweep_bot(bot_eth)
                msg = "3 wallets x ETH/USDC/USDT/WETH"
                if do_sw and COLD_WALLET and self.armed and not self.sim_only:
                    try:
                        rec = await self._run(self._sweep_bot_to_cold, 120, amt)
                        msg += f" | swept {amt} ETH -> cold ({rec.get('stage')})"
                        self._record_broadcast("sweep", rec)
                    except Exception as e:
                        msg += f" | sweep-err {e}"
                elif do_sw:
                    msg += f" | sweep-ready {amt} ETH (set COLD_WALLET + arm)"
                tgt = self.state["broadcast"].get("sponsor_target_eth", 0.03)
                sp = float(self.state["funds"]["sponsor"]["eth"])
                if sp < tgt * 0.5:
                    msg += f" | sponsor LOW {sp:.4f}<{tgt}"
                try:
                    self.refresh_broadcast_ready()
                    perf = pe.snapshot_performance(
                        self.state["funds"],
                        float(self.state.get("eth_price_usd") or 0),
                        competitors=self.state.get("competitors"),
                        opportunities=self.state.get("opportunities"),
                        arb=self.state.get("arb"),
                        broadcast=self.state.get("broadcast"),
                    )
                    self.state["performance"] = perf
                    pnl = perf.get("session_pnl_usd")
                    pnl_s = f"${pnl:+.2f}" if pnl is not None else "n/a"
                    msg += (f" | equity ${perf.get('equity_usd', 0):.2f}"
                            f" pnl {pnl_s} grade {perf.get('grade')}")
                except Exception as e:
                    msg += f" | perf-err {e}"
                self.bot("funds", "ok", msg)
            except Exception as e:
                self.bot("funds", "error", e)
            await asyncio.sleep(25)

    def _sweep_bot_to_cold(self, amount_eth):
        """Send excess BOT ETH to COLD_WALLET via cast (keystore defiarb)."""
        if not COLD_WALLET:
            return {"stage": "refuse", "reason": "COLD_WALLET unset"}
        if not lb.KEYSTORE_PW:
            return {"stage": "refuse", "reason": "KEYSTORE_PW missing"}
        import subprocess
        cmd = [
            "cast", "send", COLD_WALLET,
            "--value", f"{amount_eth}ether",
            "--keystore", lb.KEYSTORE_PATH,
            "--password", lb.KEYSTORE_PW,
            "--rpc-url", lb.RPC_CALL[0],
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        return {
            "stage": "sent" if r.returncode == 0 else "error",
            "reason": (r.stdout or r.stderr).strip()[:240],
            "amount_eth": amount_eth,
            "to": COLD_WALLET[:12],
        }

    async def sweep_loop(self):
        while True:
            try:
                self.bot("sweep", "running", "HF sweep of tracked borrowers")
                borrowers = ll.load_borrowers()
                await self._run(self._sweep, 180, borrowers)
                self.state["sweep_total"] = len(borrowers)
                opps = pe.rank_liq_opps(
                    self.state["opportunities"], edge_bias=self.edge_bias)
                self.state["opportunities"] = opps
                self.state["opportunities_meta"] = self._opps_meta(opps)
                n_opps = len(opps)
                self.bot("sweep", "ok",
                         f"{len(borrowers)} borrowers, {n_opps} liquidatable"
                         f"{' [edge-bias]' if self.edge_bias else ''}")
                if self.broadcast_enabled and n_opps:
                    liq_ok, _, _ = self.refresh_broadcast_ready()
                    if liq_ok:
                        for o in opps[:5]:
                            try:
                                rec = await self._run(
                                    self._broadcast_liquidation, 240,
                                    o["user"], o.get("profit_usd"))
                                if rec:
                                    self._record_broadcast("liq", rec)
                            except Exception as e:
                                self._record_broadcast(
                                    "liq", {"stage": "error",
                                            "reason": str(e)[:200],
                                            "user": o["user"]})
                    else:
                        self.bot("broadcast", "error",
                                 "liq not ready: "
                                 + "; ".join(
                                     self.state["broadcast"]["ready"]["reasons"][:3]))
            except Exception as e:
                self.bot("sweep", "error", e)
            moved = self._price_moved
            self._price_moved = False
            peak = pe.is_peak_hour(self.state.get("intel", {}).get("hours") or {})
            await asyncio.sleep(max(25.0, pe.sweep_sleep_sec(120.0, peak, moved) *
                float((self.state.get("intel") or {}).get("brain", {})
                      .get("cadence_mult") or 1.0)))

    def _sweep(self, borrowers):
        lows = []
        rows = []
        def hf(u):
            try:
                return u, lb.get_account_data(u)
            except Exception:
                return None
        res = list(self.tx_pool.map(hf, borrowers))
        for r in res:
            if not r:
                continue
            u, acc = r
            hfv = acc[2]
            coll_base = acc[3]
            avg_cf = acc[1]
            debt_base = 0
            if coll_base and hfv < (1 << 250):
                # HF = collValue * avgCF / debtValue (V4 accounting, Value=USD*1e26)
                debt_base = coll_base * avg_cf // hfv
            rows.append({
                "user": u, "hf": hfv, "coll": coll_base, "debt": debt_base,
                "avg_cf": avg_cf,
            })
            if hfv < lb.HEALTH_THRESHOLD:
                lows.append(u)
        rows.sort(key=lambda r: r["hf"])
        self.state["watchlist"] = [
            {
                "user": r["user"],
                "hf": str(r["hf"]),
                "coll": str(r["coll"]),
                "debt": str(r["debt"]),
                "avg_cf": str(r["avg_cf"]),
            } for r in rows[:8]
        ]
        opps = []
        for u in lows:
            try:
                plan = lb.build_plan(u)
                if not plan:
                    continue
                opps.append({
                    "user": u,
                    "hf": plan.get("healthFactor"),
                    "coll_sym": RESERVE_SYMS.get(plan.get("collateralReserveId"), "?"),
                    "debt_sym": RESERVE_SYMS.get(plan.get("debtReserveId"), "?"),
                    "profit_usd": round(int(plan.get("profitUsd", 0)) / 1e18, 2),
                    "edge": "long-tail" if (
                        u in _EDGE or pe.is_edge_opp({
                            "coll_sym": RESERVE_SYMS.get(plan.get("collateralReserveId"), "?"),
                            "debt_sym": RESERVE_SYMS.get(plan.get("debtReserveId"), "?"),
                        })
                    ) else "",
                })
            except Exception:
                continue
        self.state["opportunities"] = opps

    def _opps_meta(self, opps):
        opps = list(opps or [])
        wl = self.state.get("watchlist") or []
        edge_n = sum(1 for o in opps if o.get("edge"))
        profits = [float(o.get("profit_usd") or 0) for o in opps]
        best = max(profits) if profits else 0.0
        total = sum(profits)
        n = len(opps)
        if n >= 5:
            pressure = "hot"
        elif n >= 2:
            pressure = "busy"
        elif n == 1:
            pressure = "quiet"
        else:
            try:
                closest = min(
                    (float(w.get("hf") or 0) / 1e18 for w in wl
                     if float(w.get("hf") or 0) < 1e38),
                    default=None,
                )
            except Exception:
                closest = None
            if closest is not None and closest < 1.05:
                pressure = "elevated"
            elif closest is not None and closest < 1.1:
                pressure = "quiet"
            else:
                pressure = "idle"
        pair_counts = {}
        for o in opps:
            k = f"{o.get('coll_sym') or '?'}→{o.get('debt_sym') or '?'}"
            pair_counts[k] = pair_counts.get(k, 0) + 1
        pair_total = sum(pair_counts.values()) or 1
        pair_mix = [
            {"pair": p, "n": c, "pct": round(100 * c / pair_total)}
            for p, c in sorted(pair_counts.items(), key=lambda x: -x[1])[:8]
        ]
        hfs = []
        for o in opps:
            try:
                hfs.append(float(o.get("hf") or 0) / 1e18)
            except Exception:
                pass
        avg_hf = round(sum(hfs) / len(hfs), 4) if hfs else None
        return {
            "count": n,
            "edge_n": edge_n,
            "best_profit": round(best, 2),
            "sum_profit": round(total, 2),
            "sweep_total": self.state.get("sweep_total"),
            "watch_n": len(wl),
            "avg_hf": avg_hf,
            "pressure": pressure,
            "pair_mix": pair_mix,
        }

    async def competitor_loop(self):
        last_scanned = 0
        seen_tx = set()
        while True:
            try:
                if self._spokes_fut is None:
                    self._spokes_fut = self.tx_pool.submit(_discover_spokes)
                elif self._spokes_fut.done():
                    try:
                        live, n = self._spokes_fut.result()
                        if live:
                            self._spokes = set(live)
                        self.state["competitors_meta"].update({
                            "spokes": len(self._spokes),
                            "assets": n,
                            "status": "ok",
                        })
                        self._spokes_fut = None
                    except Exception as e:
                        self.state["competitors_meta"]["status"] = f"err {e}"
                        self._spokes_fut = None
                blk = self.state["block"]
                if not blk:
                    await asyncio.sleep(15)
                    continue
                # catch up: scan last ~12 blocks via logs (cheap vs full blocks)
                start = max(1, (last_scanned + 1) if last_scanned else blk - 12)
                if start <= blk:
                    await self._run(self._scan_liq_logs, 60, start, blk, seen_tx)
                    last_scanned = blk
                while len(seen_tx) > 500:
                    seen_tx.pop()
                self._refresh_competitor_stats()
                self.state["competitors_meta"]["last_block"] = blk
                m = self.state["competitors_meta"]
                self.hist["comp_1h"].append(int(m.get("count_1h") or 0))
                self.hist["comp_missed"].append(int(m.get("missed_by_us") or 0))
                self.bot("competitors", "ok",
                         f"{m.get('count_1h', 0)}/1h | "
                         f"{m.get('unique_searchers', 0)} searchers | "
                         f"{len(self._spokes)} spokes | "
                         f"missed={m.get('missed_by_us', 0)} | "
                         f"{m.get('pressure', 'idle')}")
            except Exception as e:
                self.bot("competitors", "error", e)
            await asyncio.sleep(15 if pe.is_peak_hour(
                self.state.get("intel", {}).get("hours") or {}) else 20)

    def _scan_liq_logs(self, start, end, seen_tx):
        """Pull LiquidationCall events across all known spokes via eth_getLogs."""
        spokes = sorted(self._spokes or {lb.SPOKE.lower()})
        logs = []
        for i in range(0, len(spokes), 4):
            chunk = spokes[i:i + 4]
            try:
                got = lb.jrpc(lb.RPC_CALL, "eth_getLogs", [{
                    "fromBlock": hex(start),
                    "toBlock": hex(end),
                    "address": chunk,
                    "topics": [LIQ_EVENT],
                }])
                if got:
                    logs.extend(got)
            except Exception as e:
                self.log("competitor", "info",
                         f"getLogs {start}-{end} chunk fail: {e}")
        for lg in logs:
            txh = (lg.get("transactionHash") or "").lower()
            if not txh or txh in seen_tx:
                continue
            seen_tx.add(txh)
            try:
                self._record_competitor_log(lg)
            except Exception as e:
                self.log("competitor", "info", f"record fail: {e}")

    def _parse_liq_log(self, lg):
        """Decode Spoke.LiquidationCall event.
        indexed: collateralReserveId, debtReserveId, user
        data: liquidator, receiveShares, debtAmountRestored, ...
        """
        topics = lg.get("topics") or []
        if len(topics) < 4:
            return None
        coll_rid = int(topics[1], 16)
        debt_rid = int(topics[2], 16)
        user = ("0x" + topics[3][-40:]).lower()
        data = lg.get("data") or "0x"
        words = [data[i:i + 64] for i in range(2, len(data), 64)] if len(data) > 2 else []
        searcher = ("0x" + words[0][-40:]).lower() if words else ""
        # word1 = receiveShares (bool)
        debt_restored = int(words[2], 16) if len(words) > 2 else 0
        coll_removed = int(words[5], 16) if len(words) > 5 else 0
        return {
            "coll_rid": coll_rid,
            "debt_rid": debt_rid,
            "searcher": searcher,
            "user": user,
            "debt_restored": debt_restored,
            "coll_to_liq": coll_removed,
            "spoke": (lg.get("address") or "").lower(),
            "tx": (lg.get("transactionHash") or "").lower(),
            "block": int(lg.get("blockNumber") or "0x0", 16),
            "log_index": int(lg.get("logIndex") or "0x0", 16),
        }

    def _record_competitor_log(self, lg):
        parsed = self._parse_liq_log(lg)
        if not parsed:
            return
        user = parsed["user"]
        searcher = parsed["searcher"]
        gas_used = None
        gas_price = None
        status = None
        ts = None
        try:
            rc = lb.jrpc(lb.RPC_CALL, "eth_getTransactionReceipt", [parsed["tx"]])
            if rc:
                gas_used = int(rc.get("gasUsed", "0x0"), 16)
                status = int(rc.get("status", "0x1"), 16)
                # EIP-1559 effective gas price when present
                egp = rc.get("effectiveGasPrice") or rc.get("gasPrice")
                if egp:
                    gas_price = int(egp, 16) / 1e9
        except Exception:
            pass
        try:
            blk = lb.jrpc(lb.RPC_CALL, "eth_getBlockByNumber",
                          [hex(parsed["block"]), False])
            if blk:
                ts = int(blk.get("timestamp", "0x0"), 16)
                if gas_price is None:
                    # fallback base fee
                    bf = blk.get("baseFeePerGas")
                    if bf:
                        gas_price = int(bf, 16) / 1e9
        except Exception:
            pass
        # our-model profit + pair labels
        est = None
        debt_rid = parsed["debt_rid"]
        coll_sym = RESERVE_SYMS.get(parsed["coll_rid"], str(parsed["coll_rid"]))
        debt_sym = RESERVE_SYMS.get(debt_rid, "?") if debt_rid is not None else "?"
        if user.startswith("0x"):
            try:
                p = lb.build_plan(user)
                if p:
                    est = round(int(p.get("profitUsd", 0)) / 1e18, 2)
                    if debt_rid is None:
                        debt_rid = p.get("debtReserveId")
                        debt_sym = RESERVE_SYMS.get(debt_rid, "?")
            except Exception:
                pass
        eth = self.state.get("eth_price_usd") or 0
        gas_cost_eth = None
        gas_cost_usd = None
        if gas_used and gas_price:
            gas_cost_eth = round(gas_used * gas_price * 1e-9, 6)
            if eth:
                gas_cost_usd = round(gas_cost_eth * eth, 2)
        # did we have this user in watchlist/opps?
        watched = {o.get("user", "").lower() for o in self.state.get("opportunities") or []}
        watched |= {w.get("user", "").lower() for w in self.state.get("watchlist") or []}
        missed = user in watched
        edge = pe.is_edge_opp({"coll_sym": coll_sym, "debt_sym": debt_sym})
        rec = {
            "block": parsed["block"],
            "ts": ts or int(time.time()),
            "user": user,
            "user_short": user[:10],
            "searcher": searcher,
            "searcher_short": searcher[:10],
            "coll": str(parsed["coll_rid"]),
            "debt": str(debt_rid) if debt_rid is not None else "?",
            "coll_sym": coll_sym,
            "debt_sym": debt_sym,
            "debt_to_cover": str(parsed["debt_restored"]),
            "gas_price_gwei": round(gas_price, 2) if gas_price is not None else None,
            "gas_used": gas_used,
            "gas_cost_eth": gas_cost_eth,
            "gas_cost_usd": gas_cost_usd,
            "est_profit_usd": est,
            "net_est_usd": round(est - gas_cost_usd, 2) if (est is not None and gas_cost_usd is not None) else est,
            "tx": parsed["tx"],
            "spoke": parsed["spoke"][:10],
            "status": status,
            "missed_by_us": missed,
            "edge": "long-tail" if edge else "",
        }
        # dedupe by tx
        self.state["competitors"] = [
            c for c in self.state["competitors"] if c.get("tx") != rec["tx"]
        ]
        self.state["competitors"].insert(0, rec)
        self.state["competitors"] = self.state["competitors"][:120]
        try:
            brain.learn_competitor(self.state, rec)
            self.state["intel"]["brain"] = brain.policy(self.state)
        except Exception:
            pass
        tag = "MISSED" if missed else "comp"
        self.log("competitor", "warn",
                 f"[{tag}] blk={rec['block']} {coll_sym}→{debt_sym} "
                 f"user={user[:10]} searcher={searcher[:10]} "
                 f"gas={gas_used} est=${est} net=${rec['net_est_usd']}")

    def _refresh_competitor_stats(self):
        now = int(time.time())
        rows = self.state.get("competitors") or []
        hour = [c for c in rows if now - int(c.get("ts") or 0) <= 3600]
        searchers = {c.get("searcher") for c in hour if c.get("searcher")}
        gases = [c["gas_used"] for c in hour if c.get("gas_used")]
        profits = [c["est_profit_usd"] for c in hour if c.get("est_profit_usd") is not None]
        nets = [c["net_est_usd"] for c in hour if c.get("net_est_usd") is not None]
        missed = sum(1 for c in hour if c.get("missed_by_us"))
        edge_n = sum(1 for c in hour if c.get("edge"))
        revert_n = sum(1 for c in hour if c.get("status") == 0)
        n = len(hour)
        if n == 0:
            pressure = "idle"
        elif n < 3:
            pressure = "quiet"
        elif n < 10:
            pressure = "busy"
        elif n < 25:
            pressure = "elevated"
        else:
            pressure = "hot"
        by_s = {}
        for c in hour:
            addr = (c.get("searcher") or "").lower()
            if not addr:
                continue
            slot = by_s.setdefault(addr, {
                "addr": addr,
                "short": c.get("searcher_short") or addr[:10],
                "n": 0, "est": 0.0, "missed": 0, "edge": 0,
            })
            slot["n"] += 1
            if c.get("est_profit_usd") is not None:
                slot["est"] += float(c["est_profit_usd"])
            if c.get("missed_by_us"):
                slot["missed"] += 1
            if c.get("edge"):
                slot["edge"] += 1
        top_searchers = sorted(
            ({**v, "est": round(v["est"], 2)} for v in by_s.values()),
            key=lambda x: (-x["n"], -x["est"]),
        )[:10]
        pair_counts = defaultdict(int)
        for c in hour:
            pair_counts[f"{c.get('coll_sym') or '?'}→{c.get('debt_sym') or '?'}"] += 1
        pair_mix = sorted(
            [{"pair": k, "n": v} for k, v in pair_counts.items()],
            key=lambda x: -x["n"],
        )[:10]
        pair_total = sum(p["n"] for p in pair_mix) or 1
        for p in pair_mix:
            p["pct"] = round(100.0 * p["n"] / pair_total, 1)
        for s in top_searchers:
            s["pct"] = round(100.0 * s["n"] / max(n, 1), 1)
        self.state["competitors_meta"].update({
            "count_1h": n,
            "unique_searchers": len(searchers),
            "avg_gas": int(sum(gases) / len(gases)) if gases else None,
            "sum_est_profit": round(sum(profits), 2) if profits else 0,
            "sum_net_est": round(sum(nets), 2) if nets else 0,
            "missed_by_us": missed,
            "miss_rate_pct": round(100.0 * missed / n, 1) if n else 0,
            "edge_n": edge_n,
            "revert_n": revert_n,
            "pressure": pressure,
            "top_searchers": top_searchers,
            "pair_mix": pair_mix,
            "total": len(rows),
        })

    # legacy full-block scanner removed — eth_getLogs path is primary

    def _arb_scan(self, stats=None):
        if self._uni is None:
            self._uni = mev_bot.build_universe()
        # report_all=True but scan itself floors catastrophic negatives.
        gas = float(self.state.get("gas_gwei") or 1.0)
        eth = float(self.state.get("eth_price_usd") or 2000.0)
        cap = float(getattr(mev_bot, "BORROW_CAP_WETH", 5) or 5)
        try:
            gas_eth = 500_000 * gas * 1e-9
            if gas_eth * eth > 8:
                cap = min(cap, 2.0)
            elif gas_eth * eth > 3:
                cap = min(cap, 3.0)
        except Exception:
            pass
        return mev_bot.scan(
            self._uni, False,
            borrow_cap_weth=cap,
            min_profit_weth=-0.003,
            stats=stats, report_all=True, mode="dash",
        )

    def _optimize_arb_size(self, o):
        """Re-quote top opp across size grid; keep max-profit size."""
        try:
            max_b = int(o.get("borrow") or 0)
            if max_b <= 0:
                return o
            grid = mev_bot._size_grid(max_b)
            if len(grid) <= 1:
                return o
            routes = o.get("routes") or [o["rin"], o["rout"]]
            fee = int(o["fee"])
            best = o
            best_p = int(o.get("profit") or 0)
            for b in grid:
                try:
                    amt = b
                    outs = []
                    for r in routes:
                        out = r.quote(amt)
                        if not out:
                            outs = []
                            break
                        outs.append(out)
                        amt = out
                    if not outs:
                        continue
                    fee_cost = b * fee // 1_000_000
                    profit = outs[-1] - b - fee_cost
                    if profit > best_p:
                        best_p = profit
                        best = {**o, "borrow": b, "out1": outs[0], "outs": outs,
                                "profit": profit, "sized": True}
                except Exception:
                    continue
            return best
        except Exception:
            return o

    def _arb_row(self, o, eth, gas_gwei=0.0, preferred=None):
        routes = o.get("routes") or [o["rin"], o["rout"]]
        rin, rout = routes[0], routes[-1]
        mid = o.get("mid") or mev_bot.ADDR_SYM.get(rin.token_out, "?")
        if isinstance(mid, str) and mid.startswith("0x"):
            mid = mev_bot.ADDR_SYM.get(mid, mid[:8])
        flash = o["flashPool"]
        fee_pct = o["fee"] / 10000
        borrow_w = o["borrow"] / 1e18
        profit_w = o["profit"] / 1e18
        profit_usd = round(profit_w * eth, 2) if eth else None
        depth_w = round(int(o.get("capacity") or 0) / 1e18, 2)
        # prefer live gas from scan; fall back to dashboard gas_gwei
        gas_wei = o.get("gas_wei")
        if gas_wei is not None and eth:
            gas_usd = round(gas_wei / 1e18 * eth, 2)
        else:
            gas_usd = round(500_000 * float(gas_gwei or 0) * 1e-9 * eth, 2) if eth else None
        if o.get("net_profit") is not None and eth:
            net_usd = round(o["net_profit"] / 1e18 * eth, 2)
        else:
            net_usd = round(profit_usd - gas_usd, 2) if (profit_usd is not None and gas_usd is not None) else None
        hops = int(o.get("hops") or len(routes))
        route = "→".join(
            f"{getattr(r, 'dex', 'uni')}/{r.kind.upper()}" for r in routes)
        preferred = preferred or set()
        mid_key = str(mid).split("→")[0].upper()
        dexes = o.get("dexes") or sorted({getattr(r, "dex", "uni") for r in routes})
        venue = o.get("venue") or ("+".join(dexes) if len(dexes) > 1 else (dexes[0] if dexes else "uni"))
        return {
            "flash": flash[:10],
            "flash_full": flash,
            "fee": fee_pct,
            "borrow_weth": round(borrow_w, 4),
            "depth_weth": depth_w,
            "profit_weth": round(profit_w, 6),
            "profit_usd": profit_usd,
            "gas_usd": gas_usd,
            "net_usd": net_usd,
            "roi_bps": round(profit_w / max(borrow_w, 1e-12) * 10_000, 1),
            "gap_usd": round(min(0.0, net_usd), 2) if net_usd is not None else None,
            "mid": mid,
            "hops": hops,
            "route": route,
            "venue": venue,
            "dexes": dexes,
            "cross_dex": bool(o.get("cross_dex") or len(dexes) > 1),
            "sized": bool(o.get("sized")),
            "learned": mid_key in preferred,
            "etherscan": f"https://etherscan.io/address/{flash}",
            "actionable": bool(net_usd is not None and net_usd > 0),
            "network": "ethereum-mainnet",
        }

    async def arb_loop(self):
        while True:
            try:
                self.bot("arb", "running", "scanning Uni+Sushi DEX routes…")
                if self._uni is None:
                    self._uni = await self._run(mev_bot.build_universe, 90)
                stats = {}
                t0 = time.time()
                res = await self._run(self._arb_scan, 150, stats)
                scan_ms = int((time.time() - t0) * 1000)
                if res is None and not stats:
                    self.state["arb"]["error"] = "scan timeout/empty"
                    self.bot("arb", "error", "scan timed out — retrying")
                    await asyncio.sleep(15)
                    continue
                res = res or []
                eth = self.state["eth_price_usd"] or 0.0
                gas = float(self.state.get("gas_gwei") or 0)
                hints = pe.near_miss_hints()
                preferred = pe.prefer_learned_mids(hints)

                # size-optimize top raw candidates before ranking
                raw_pos = [o for o in res if o["profit"] > 0]
                raw_near = [o for o in res if o["profit"] <= 0]
                # bias learned mids upward in near list
                raw_near.sort(
                    key=lambda o: (
                        0 if mev_bot.ADDR_SYM.get(o["rin"].token_out, "?").upper()
                        in preferred else 1,
                        -o["profit"],
                    )
                )
                optimized = []
                for o in raw_pos[:6]:
                    try:
                        optimized.append(
                            await self._run(self._optimize_arb_size, 60, o))
                    except Exception:
                        optimized.append(o)
                # keep rest without size pass
                seen = {(x["flashPool"], x["rin"].addr, x["rout"].addr,
                         x["borrow"]) for x in optimized}
                for o in raw_pos[6:]:
                    k = (o["flashPool"], o["rin"].addr, o["rout"].addr,
                         o["borrow"])
                    if k not in seen:
                        optimized.append(o)

                rows_live = pe.rank_arb_opps(
                    [self._arb_row(o, eth, gas, preferred) for o in optimized],
                    gas, eth,
                )[:10]
                rows_near = pe.rank_arb_opps(
                    [self._arb_row(o, eth, gas, preferred)
                     for o in raw_near[:12]],
                    gas, eth,
                )[:10]

                for n in raw_near[:5]:
                    mid = mev_bot.ADDR_SYM.get(n["rin"].token_out, "?")
                    pe.record_near_miss(mid, n["fee"] / 10000,
                                        n["profit"] / 1e18, gas)
                for row in (rows_live[:3] + rows_near[:3]):
                    try:
                        brain.learn_arb_near(self.state, row)
                    except Exception:
                        pass
                try:
                    self.state["intel"]["brain"] = brain.policy(self.state)
                    if brain.get_brain().steps % 8 == 0:
                        brain.save()
                except Exception:
                    pass

                actionable = sum(1 for r in rows_live if r.get("actionable"))
                best_net = rows_live[0]["net_usd"] if rows_live else (
                    rows_near[0]["net_usd"] if rows_near else None)
                top_mid = rows_live[0]["mid"] if rows_live else (
                    rows_near[0]["mid"] if rows_near else None)
                cross_n = sum(1 for r in rows_live + rows_near if r.get("cross_dex"))
                venue_counts = {}
                for r in rows_live + rows_near:
                    v = r.get("venue") or "uni"
                    venue_counts[v] = venue_counts.get(v, 0) + 1
                venue_mix = sorted(
                    [{"venue": k, "n": v} for k, v in venue_counts.items()],
                    key=lambda x: -x["n"],
                )
                vtot = sum(x["n"] for x in venue_mix) or 1
                for x in venue_mix:
                    x["pct"] = round(100.0 * x["n"] / vtot, 1)
                if actionable > 0:
                    pressure = "hot"
                elif rows_live:
                    pressure = "busy"
                elif rows_near:
                    pressure = "quiet"
                else:
                    pressure = "idle"

                stats = dict(stats or {})
                stats["scan_ms"] = scan_ms
                stats["gas_gwei"] = gas
                stats["borrow_cap"] = stats.get("borrow_cap")
                stats["actionable"] = actionable
                stats["live"] = len(rows_live)
                stats["near"] = len(rows_near)
                stats["best_net_usd"] = best_net
                stats["preferred_mids"] = sorted(preferred)[:5]

                self.state["arb"]["opps"] = rows_live
                self.state["arb"]["near"] = rows_near
                self.state["arb"]["stats"] = stats
                self.state["arb"]["scan_block"] = self.state["block"]
                self.state["arb"]["meta"] = {
                    "scan_ms": scan_ms,
                    "scan_block": self.state["block"],
                    "gas_gwei": gas,
                    "live": len(rows_live),
                    "near": len(rows_near),
                    "actionable": actionable,
                    "best_net_usd": best_net,
                    "top_mid": top_mid,
                    "preferred_mids": sorted(preferred)[:5],
                    "pressure": pressure,
                    "dexes": stats.get("dexes") or list(
                        (stats.get("by_dex") or {}).keys()),
                    "by_dex": stats.get("by_dex") or {},
                    "by_kind": stats.get("by_kind") or {},
                    "cross_dex": cross_n or stats.get("cross_dex") or 0,
                    "venue_mix": venue_mix,
                    "mode": stats.get("mode") or "dash",
                    "jobs": stats.get("jobs"),
                    "screened": stats.get("screened"),
                    "quoted": stats.get("quoted"),
                    "flash_pools": stats.get("flash_pools"),
                    "routes": stats.get("routes"),
                }
                self.hist["arb_best_net"].append(
                    float(best_net) if best_net is not None else 0.0)
                self.hist["arb_actionable"].append(int(actionable))
                self._arb_quoted_block = self.state["block"]
                self.state["arb"]["error"] = None
                self.state["broadcast"]["near_miss_hints"] = pe.near_miss_hints()

                msg = "no arb found"
                best = (stats.get("best_profit_weth") or 0) / 1e18
                if rows_live:
                    top = rows_live[0]
                    net = top.get("net_usd")
                    msg = (f"{len(rows_live)} live"
                           + (f", {actionable} actionable" if actionable else "")
                           + (f", top net ${net:.2f}" if net is not None else "")
                           + f" via {top.get('mid')} [{top.get('venue')}]")
                if rows_near:
                    gap = rows_near[0].get("gap_usd")
                    msg += f" | +{len(rows_near)} near"
                    if gap is not None:
                        msg += f" (gap ${gap:.2f})"
                dexes = ",".join(stats.get("dexes") or [])
                msg += f" | {dexes or 'dex'} | {scan_ms}ms | best {best:.5f} WETH"
                self.bot("arb", "ok", msg)

                # broadcast only net-positive after gas
                to_fire = next((o for o in optimized
                                if (o["profit"] * eth / 1e18) -
                                (500_000 * gas * 1e-9 * eth) > 0), None)
                if self.broadcast_enabled and to_fire is not None:
                    _, arb_ok, _ = self.refresh_broadcast_ready()
                    if arb_ok:
                        try:
                            rec = await self._run(
                                self._broadcast_arb, 300, to_fire, eth)
                            if rec:
                                self._record_broadcast("arb", rec)
                        except Exception as e:
                            self._record_broadcast(
                                "arb", {"stage": "error",
                                        "reason": str(e)[:200]})
                    else:
                        self.bot("broadcast", "error",
                                 "arb not ready: "
                                 + "; ".join(
                                     self.state["broadcast"]["ready"]["reasons"][:3]))
            except Exception as e:
                self.state["arb"]["error"] = str(e)[:200]
                self.bot("arb", "error", e)
            peak = pe.is_peak_hour(self.state.get("intel", {}).get("hours") or {})
            sleep = pe.arb_sleep_sec(90.0, peak)
            try:
                sleep *= float((self.state.get("intel") or {}).get("brain", {})
                               .get("cadence_mult") or 1.0)
            except Exception:
                pass
            await asyncio.sleep(max(55.0, sleep))


    @staticmethod
    def _intel_activity_fallback(records):
        """MEV/poll-weighted SAST hour & dow counts when liq-adjacent series is empty."""
        hours, dows = {}, {}
        for r in records:
            h = r.get("sast_hour")
            d = r.get("sast_dow")
            if h is None and r.get("utc_hour") is not None:
                h = (int(r["utc_hour"]) + 2) % 24
            if d is None and r.get("utc_dow") is not None:
                uh = int(r.get("utc_hour") or 0)
                d = (int(r["utc_dow"]) + (1 if uh >= 22 else 0)) % 7
            if h is None:
                continue
            mv = r.get("mev_classes") or {}
            w = (int(mv.get("liq") or 0)
                 + int(mv.get("spoke") or 0)
                 + int(mv.get("aave") or 0))
            if w <= 0:
                w = max(1, int(r.get("mempool_txs") or 0) // 5000)
            h = int(h)
            hours[h] = hours.get(h, 0) + w
            if d is not None:
                d = int(d)
                dows[d] = dows.get(d, 0) + w
        return hours, dows

    @staticmethod
    def _intel_soft_readiness(records, pol):
        """Fallback readiness when liq-adjacent score is stuck at 0."""
        recent = records[-50:] or records
        if not recent:
            return 0.0
        mev_sig = 0
        for r in recent:
            mv = r.get("mev_classes") or {}
            mev_sig += int(mv.get("liq") or 0) + int(mv.get("aave") or 0)
        dens = min(30.0, len(records) / 8.0)
        brain_pts = min(25.0, float((pol or {}).get("steps") or 0) * 0.3)
        mev_pts = min(20.0, float(mev_sig) * 0.5)
        return round(min(100.0, dens + brain_pts + mev_pts), 1)

    async def intel_loop(self):
        while True:
            try:
                rec = await self._run(ic.collect_once, 75, 0)
                if rec:
                    try:
                        ic.append(rec)
                    except OSError as e:
                        self.log("intel", "warn", f"append failed: {e}")
                    records = ia.load_records()
                    # If primary file is unreadable/empty but we have this rec, still score.
                    if not records:
                        records = [rec]
                    hours = ia.hourly_activity(records)
                    dows = ia.dow_activity(records)
                    hours_source = "liq"
                    if sum(int(v) for v in hours.values()) <= 0:
                        hours, fb_dows = self._intel_activity_fallback(records)
                        hours_source = "mev+poll"
                        if sum(int(v) for v in dows.values()) <= 0:
                            dows = fb_dows
                    elif sum(int(v) for v in dows.values()) <= 0:
                        _, dows = self._intel_activity_fallback(records)
                        hours_source = "liq+poll-dow"
                    mv = rec.get("mev_classes") or {}
                    pol = brain.policy(self.state)
                    ready = float(ia.readiness(records) or 0)
                    if ready < 1:
                        ready = self._intel_soft_readiness(records, pol)
                    if ready >= 50:
                        pressure = "hot"
                    elif ready >= 25:
                        pressure = "busy"
                    elif ready >= 8:
                        pressure = "quiet"
                    else:
                        pressure = "idle"
                    self.state["intel"].update({
                        "records": len(records),
                        "readiness": ready,
                        "pressure": pressure,
                        "hours": hours,
                        "dows": dows,
                        "hours_source": hours_source,
                        "moves": len(ia.oracle_moves(records)),
                        "last": {"ts": rec["ts"], "block": rec["block"],
                                 "gas": rec["gas_gwei"], "spoke_txs": len(rec["spoke_txs"]),
                                 "mempool_txs": rec["mempool_txs"]},
                        "mev": mv,
                        "brain": pol,
                    })
                    self.bot("intel", "ok",
                             f"{len(records)} recs · readiness="
                             f"{self.state['intel']['readiness']} · "
                             f"brain p={pol.get('act_prob')} "
                             f"exp=${pol.get('exp_net_usd')} · "
                             f"{pol.get('advice')}")
            except Exception as e:
                self.bot("intel", "error", e)
            await asyncio.sleep(75)

    # ------------------------------------------------------------ SOL twin loops (slow cadence)
    async def sol_net_loop(self):
        while True:
            try:
                self.sol_bot("prices", "running", "slot / epoch / SOLUSD…")
                info = await self._run(sols.fetch_epoch_and_slot, 20)
                px = await self._run(sols.fetch_sol_price, 12)
                sol = self.state["sol"]
                if info.get("ok"):
                    sol["slot"] = info.get("slot")
                    sol["epoch"] = info.get("epoch")
                    sol["slot_index"] = info.get("slot_index")
                    sol["slots_in_epoch"] = info.get("slots_in_epoch")
                    sol["absolute_slot"] = info.get("absolute_slot")
                    sol["rpc"] = info.get("rpc")
                if px is not None:
                    sol["sol_price_usd"] = px
                self.sol_bot("prices", "ok",
                            f"slot {sol.get('slot')} · SOL ${px or '--'}")
                self.log("sol-price", "info",
                         f"slot={sol.get('slot')} epoch={sol.get('epoch')} "
                         f"SOL=${px}")
            except Exception as e:
                self.sol_bot("prices", "error", e)
                self.log("sol-price", "error", e)
            await asyncio.sleep(35)

    async def sol_mempool_loop(self):
        """Priority-fee panel — Solana has no ETH-style public mempool dump."""
        while True:
            try:
                self.sol_bot("mempool", "running", "priority fees…")
                fees = await self._run(sols.fetch_priority_fees, 25)
                if not fees:
                    fees = {"ok": False, "error": "priority fee timeout"}
                sol = self.state["sol"]
                mp = sol["mempool"]
                meta = mp.setdefault("meta", {})
                if fees.get("ok"):
                    med = fees.get("median")
                    p90 = fees.get("p90")
                    sol["priority_fee"] = med
                    n = fees.get("samples") or 0
                    slots_n = fees.get("slots") or 0
                    hist_bins = fees.get("histogram") or []
                    mix = fees.get("mix") or {}
                    mp["count"] = n
                    mp["queued"] = slots_n
                    meta.update({
                        "pending": n,
                        "queued": slots_n,
                        "median_fee": med,
                        "p90_fee": p90,
                        "p99_fee": fees.get("p99"),
                        "avg_fee": fees.get("avg"),
                        "max_fee": fees.get("max"),
                        "zero_pct": fees.get("zero_pct") or 0,
                        "slots": slots_n,
                        "tps": fees.get("tps"),
                        "nv_tps": fees.get("nv_tps"),
                        "pressure": fees.get("pressure") or "idle",
                        "hot_share_pct": round(
                            100.0 * ((mix.get("hot") or 0) + (mix.get("elevated") or 0))
                            / max(n, 1), 1),
                        "histogram": hist_bins,
                        "rpc": fees.get("rpc"),
                        "note": "getRecentPrioritizationFees · µl/CU",
                    })
                    mp["mev_txs"] = [
                        {
                            "cls": f.get("cls") or "quiet",
                            "slot": f.get("slot"),
                            "fee": f.get("fee"),
                            "vs_med": f.get("vs_med"),
                            "solscan": (
                                f"https://solscan.io/block/{f.get('slot')}"
                                if f.get("slot") is not None else None
                            ),
                        }
                        for f in (fees.get("fees") or [])[:50]
                    ]
                    mp["mev"] = mix
                    mp["top_to"] = [
                        {
                            "kind": "bucket",
                            "label": b.get("label") or f"{b.get('lo')}+",
                            "share": round((b.get("n") or 0) / max(n, 1), 3),
                            "txs": b.get("n") or 0,
                            "pct": b.get("pct") or 0,
                        }
                        for b in hist_bins if (b.get("n") or 0) > 0
                    ]
                    mp["spoke_txs"] = [
                        {
                            "fn": s.get("cls"),
                            "user": s.get("slot"),
                            "args": s.get("fee"),
                            "cls": s.get("cls"),
                            "solscan": (
                                f"https://solscan.io/block/{s.get('slot')}"
                                if s.get("slot") is not None else None
                            ),
                        }
                        for s in (fees.get("hot_slots") or [])
                    ]
                    self.hist["sol_fee_median"].append(med or 0)
                    self.hist.setdefault(
                        "sol_fee_p90", deque(maxlen=MAXLEN)).append(p90 or 0)
                    self.hist.setdefault(
                        "sol_tps", deque(maxlen=MAXLEN)).append(
                            fees.get("tps") or 0)
                    tps_s = fees.get("tps")
                    self.sol_bot(
                        "mempool", "ok",
                        f"n={n} slots={slots_n} med={med} p90={p90} µl"
                        + (f" tps={tps_s}" if tps_s is not None else ""))
                    self.log("sol-mempool", "info",
                             f"prio n={n} med={med} p90={p90} "
                             f"zero={fees.get('zero_pct')}% "
                             f"tps={tps_s}")
                else:
                    meta["pressure"] = "idle"
                    mp["mev_txs"] = []
                    self.sol_bot("mempool", "error", fees.get("error") or "empty")
            except Exception as e:
                self.sol_bot("mempool", "error", e)
                self.log("sol-mempool", "error", e)
            await asyncio.sleep(45)

    async def sol_funds_loop(self):
        while True:
            try:
                self.sol_bot("funds", "running", "SOL wallet balances…")
                funds = await self._run(
                    sols.fetch_wallet_balances, 20, sols.current_wallets())
                sol = self.state["sol"]
                sol["funds"] = funds
                sol["fund_guide"] = sols.fund_guide()
                configured = sum(1 for v in funds.values() if v.get("configured"))
                total = sum((v.get("sol") or 0) for v in funds.values()
                            if v.get("sol") is not None)
                px = sol.get("sol_price_usd") or 0
                perf = sol.setdefault("performance", {})
                funder = funds.get("funder") or {}
                sponsor = funds.get("sponsor") or {}
                bot = funds.get("bot") or {}
                need_s = float(sponsor.get("shortfall_sol") or 0)
                need_b = float(bot.get("shortfall_sol") or 0)
                if configured:
                    funder_sol = funder.get("sol")
                    pk = (funder.get("pubkey") or "")
                    short = (pk[:4] + "…" + pk[-4:]) if len(pk) > 8 else pk
                    perf["equity_usd"] = round(total * px, 2) if px else None
                    perf["verdict"] = (
                        f"funder {short} · {total:.4f} SOL"
                        if funder_sol is not None else
                        f"{configured} wallet(s) · {total:.4f} SOL"
                    )
                    if need_s > 0 or need_b > 0:
                        parts = []
                        if need_s > 0:
                            parts.append(f"{need_s:.2f} SOL → sponsor")
                        if need_b > 0:
                            parts.append(f"{need_b:.2f} SOL → bot")
                        perf["grade"] = "C" if total <= 0 else "B"
                        perf["edge"] = "from funder send " + " and ".join(parts)
                    else:
                        perf["grade"] = "A"
                        perf["edge"] = (
                            "sponsor + bot funded — broadcast still dry-run "
                            "until programs deploy"
                        )
                else:
                    perf["equity_usd"] = None
                    perf["verdict"] = "no SOL wallets configured"
                    perf["grade"] = "—"
                    perf["ledger"] = []
                self.sol_bot("funds", "ok",
                            f"configured={configured} sol={total:.4f}")
            except Exception as e:
                self.sol_bot("funds", "error", e)
            await asyncio.sleep(55)

    async def sol_sweep_loop(self):
        """Solend reserve watchlist + obligation probe (best-effort HF)."""
        while True:
            try:
                self.sol_bot("sweep", "running", "Solend reserve + obligation probe…")
                t0 = time.time()
                data = await self._run(sols.fetch_solend_watchlist, 90)
                if not data:
                    data = {"ok": False, "watchlist": [], "opportunities": [],
                            "error": "watchlist timeout", "reserves_n": 0}
                # Obligation GPA + hydrate — slow/may fail on public RPC; cap wait
                probe = await self._run(
                    sols.probe_solend_obligations, 55, data.get("market"), 24)
                if not probe:
                    probe = {
                        "ok": False, "opportunities": [], "probed": 0,
                        "hydrated": 0, "note": "obligation probe timeout",
                    }
                ms = int((time.time() - t0) * 1000)
                sol = self.state["sol"]
                wl = data.get("watchlist") or []
                opps = list(probe.get("opportunities") or [])
                # Attach dry-run liq plans (never submit)
                for o in opps:
                    o["plan"] = sols.build_liq_plan(o)
                sol["watchlist"] = wl
                sol["opportunities"] = opps
                mix = {}
                for w in wl:
                    sym = w.get("symbol") or "?"
                    mix[sym] = mix.get(sym, 0) + 1
                pair_mix = sorted(
                    [{"pair": k, "n": v, "pct": round(100 * v / max(len(wl), 1), 1)}
                     for k, v in mix.items()],
                    key=lambda x: -x["n"],
                )
                pressure = "idle"
                if opps:
                    pressure = "hot"
                elif any(w.get("urgency") == "hot" for w in wl):
                    pressure = "elevated"
                elif wl:
                    pressure = "quiet"
                best = max((o.get("profit_usd") or 0) for o in opps) if opps else 0
                edge_n = sum(1 for o in opps if o.get("edge"))
                note = data.get("hf_note") or ""
                if probe.get("note"):
                    note = (note + " · " if note else "") + probe["note"]
                sol["opportunities_meta"] = {
                    "count": len(opps),
                    "edge_n": edge_n,
                    "best_profit": best,
                    "sum_profit": round(sum(o.get("profit_usd") or 0 for o in opps), 4),
                    "sweep_total": data.get("reserves_n") or len(wl),
                    "watch_n": len(wl),
                    "avg_hf": (sum(w.get("hf") or 0 for w in wl) / len(wl)
                               if wl else None),
                    "pressure": pressure,
                    "pair_mix": pair_mix,
                    "protocol": sols.PROTOCOL,
                    "status": "ok" if data.get("ok") else "error",
                    "market": data.get("market"),
                    "scan_ms": ms,
                    "hf_public": bool(data.get("hf_public")),
                    "obligation_probed": probe.get("probed") or 0,
                    "obligation_hydrated": probe.get("hydrated") or 0,
                    "obligation_method": probe.get("method"),
                    "note": note,
                }
                sol["prices"]["reserves"] = {
                    w["symbol"]: {
                        "util_pct": w.get("util_pct"),
                        "supply_apy": w.get("supply_apy"),
                        "borrow_apy": w.get("borrow_apy"),
                        "ltv": w.get("ltv"),
                        "liq_thresh": w.get("liq_thresh"),
                    }
                    for w in wl if w.get("symbol")
                }
                # sim dry-run history when real liquidatable found
                if opps and self.sol_sim_only:
                    bc = sol["broadcast"]
                    plan = opps[0].get("plan") or {}
                    bc.setdefault("history", []).insert(0, {
                        "ts": int(time.time()), "kind": "liq",
                        "stage": "simulated",
                        "detail": (
                            f"dry-run liq HF={opps[0].get('hf')} "
                            f"profit=${opps[0].get('profit_usd')} "
                            f"jito_tip={plan.get('jito_tip_lamports', 0)}lam"
                        ),
                        "plan": plan,
                    })
                    bc["history"] = bc["history"][:40]
                    self.refresh_sol_broadcast_ready()
                msg = (f"Solend watch={len(wl)} liq={len(opps)} "
                       f"gpa={probe.get('probed')} hyd={probe.get('hydrated')} "
                       f"reserves={data.get('reserves_n')} {ms}ms")
                # GPA failures are expected on public RPC — don't mark sweep error
                if data.get("error"):
                    msg += f" err={data['error']}"
                elif probe.get("probed") == 0 and probe.get("error"):
                    msg += " · obligation GPA blocked (set SOLANA_RPC)"
                self.sol_bot("sweep", "ok" if data.get("ok") else "error", msg)
                self.log("sol-sweep", "info" if data.get("ok") else "warn", msg)
            except Exception as e:
                self.sol_bot("sweep", "error", e)
                self.log("sol-sweep", "error", e)
            await asyncio.sleep(90)

    async def sol_competitor_loop(self):
        while True:
            try:
                self.sol_bot("competitors", "running", "decode Solend liq txs…")
                decoded = await self._run(sols.decode_solend_competitors, 90, 10)
                if not decoded:
                    decoded = {"ok": False, "rows": [], "liq_n": 0, "revert_n": 0}
                sol = self.state["sol"]
                rows = decoded.get("rows") or []
                comps = []
                revert_n = int(decoded.get("revert_n") or 0)
                liq_n = int(decoded.get("liq_n") or 0)
                searchers = {}
                for r in rows:
                    if r.get("err"):
                        revert_n = max(revert_n, revert_n)  # already counted
                    sk = r.get("searcher") or "—"
                    if sk and sk != "—":
                        searchers[sk] = searchers.get(sk, 0) + 1
                    comps.append({
                        "age": r.get("slot"),
                        "pair": r.get("pair") or "solend",
                        "searcher": sk,
                        "user": r.get("user") or "—",
                        "gas_usd": None,
                        "est": None,
                        "net": None,
                        "flags": r.get("flags") or "ok",
                        "tx": r.get("tx"),
                        "sig": r.get("sig"),
                        "slot": r.get("slot"),
                        "missed": False,
                        "edge": "liq" in str(r.get("flags") or ""),
                    })
                top = sorted(
                    [{"searcher": k, "n": v, "share": round(v / max(len(comps), 1), 3),
                      "sum_est": 0}
                     for k, v in searchers.items()],
                    key=lambda x: -x["n"],
                )[:8]
                if not top and comps:
                    top = [{"searcher": "solend-program", "n": len(comps),
                            "share": 1, "sum_est": 0}]
                pair_counts = {}
                for c in comps:
                    p = c.get("pair") or "solend"
                    pair_counts[p] = pair_counts.get(p, 0) + 1
                pair_mix = [
                    {"pair": k, "n": v,
                     "pct": round(100 * v / max(len(comps), 1), 1),
                     "share": round(v / max(len(comps), 1), 3)}
                    for k, v in pair_counts.items()
                ]
                sol["competitors"] = comps
                sol["competitors_meta"] = {
                    "spokes": 1, "assets": 0,
                    "status": "ok" if comps else "scanning",
                    "count_1h": len(comps),
                    "unique_searchers": len(searchers) or (1 if comps else 0),
                    "avg_gas": None, "sum_est_profit": 0, "sum_net_est": 0,
                    "missed_by_us": 0, "miss_rate_pct": 0,
                    "edge_n": liq_n,
                    "revert_n": revert_n,
                    "pressure": ("elevated" if liq_n else
                                 "quiet" if comps else "idle"),
                    "top_searchers": top,
                    "pair_mix": pair_mix,
                    "last_slot": comps[0].get("slot") if comps else None,
                    "total": len(comps),
                    "liq_labeled": liq_n,
                    "note": (
                        f"decoded {liq_n} liquidate-like of {len(comps)} recent "
                        "Solend sigs — PnL not invented"
                    ),
                }
                self.hist["sol_comp_1h"].append(len(comps))
                self.sol_bot("competitors", "ok",
                            f"{len(comps)} sigs · {liq_n} liq-labeled")
                self.log("sol-comp", "info",
                         f"sigs={len(comps)} liq={liq_n} revert={revert_n}")
            except Exception as e:
                self.sol_bot("competitors", "error", e)
                self.log("sol-comp", "error", e)
            await asyncio.sleep(120)

    async def sol_arb_loop(self):
        while True:
            try:
                self.sol_bot("arb", "running", "Jupiter multi-pair quotes…")
                t0 = time.time()
                prio = (self.state["sol"].get("priority_fee")
                        or ((self.state["sol"].get("mempool") or {})
                            .get("meta") or {}).get("median_fee"))
                data = await self._run(
                    sols.fetch_jupiter_roundtrips, 120, None, prio)
                if not data:
                    data = {"ok": False, "opps": [], "near": [],
                            "error": "jupiter quote timeout"}
                ms = int((time.time() - t0) * 1000)
                sol = self.state["sol"]
                live = data.get("opps") or []
                near = data.get("near") or []
                # Attach dry-run plans
                for row in live + near:
                    row["plan"] = sols.build_arb_plan(row, prio)
                actionable = sum(1 for o in live if o.get("actionable"))
                best = live[0]["net_usd"] if live else (
                    near[0]["net_usd"] if near else None)
                venue_mix = {}
                mid_mix = {}
                dex_counts: dict[str, int] = {}
                for o in live + near:
                    v = (o.get("venue") or "jup")[:24]
                    venue_mix[v] = venue_mix.get(v, 0) + 1
                    m = o.get("mid") or "?"
                    mid_mix[m] = mid_mix.get(m, 0) + 1
                    for lab in o.get("labels") or []:
                        dex_counts[lab] = dex_counts.get(lab, 0) + 1
                top_mid = (sorted(mid_mix.items(), key=lambda x: -x[1])[0][0]
                           if mid_mix else None)
                hints = []
                for n in near[:5]:
                    hints.append({
                        "mid": n.get("mid"), "gap_usd": n.get("gap_usd"),
                        "path": n.get("path"), "venue": n.get("venue"),
                    })
                tot_rows = max(len(live) + len(near), 1)
                sol["arb"] = {
                    "opps": live,
                    "near": near,
                    "stats": {
                        "quotes": data.get("quotes") or (len(live) + len(near)),
                        "pairs_tried": data.get("pairs_tried"),
                        "venues": list(venue_mix.keys()),
                        "mids": list(mid_mix.keys()),
                        "dexes": list(dex_counts.keys()),
                        "mode": "jup-smart",
                        "tip_usd": data.get("tip_usd"),
                    },
                    "error": data.get("error"),
                    "meta": {
                        "scan_ms": ms,
                        "scan_slot": sol.get("slot"),
                        "live": len(live),
                        "near": len(near),
                        "actionable": actionable,
                        "best_net_usd": best,
                        "top_mid": top_mid,
                        "preferred_mids": [
                            "SOL-USDC", "SOL-USDT", "SOL-mSOL",
                            "SOL-JitoSOL", "USDC-USDT", "SOL-BONK",
                            "SOL-RAY", "SOL-WIF", "SOL-PYTH",
                        ],
                        "pressure": (
                            "hot" if actionable else
                            "quiet" if (live or near) else "idle"
                        ),
                        "dexes": list(dex_counts.keys()) or ["jupiter"],
                        "by_dex": dex_counts or {"jupiter": len(live) + len(near)},
                        "cross_dex": sum(1 for o in live if o.get("cross_dex")),
                        "venue_mix": [
                            {"venue": k, "n": v,
                             "pct": round(100 * v / tot_rows, 1)}
                            for k, v in venue_mix.items()
                        ],
                        "mode": "jup-smart",
                        "tip_usd": data.get("tip_usd"),
                        "quotes": data.get("quotes") or 0,
                        "priority_median": prio,
                    },
                }
                bc = sol["broadcast"]
                bc["near_miss_hints"] = hints
                self.hist["sol_arb_best_net"].append(best if best is not None else 0)
                self.hist["sol_arb_actionable"].append(actionable)
                self.sol_bot(
                    "arb", "ok" if data.get("ok") else "error",
                    f"live={len(live)} near={len(near)} best={best} "
                    f"q={data.get('quotes')} {ms}ms",
                )
                self.log(
                    "sol-arb", "info" if data.get("ok") else "warn",
                    f"live={len(live)} near={len(near)} best={best} "
                    f"tip=${data.get('tip_usd')} mids={list(mid_mix.keys())}",
                )
                # sim-only history when profitable (never submit)
                if live and self.sol_sim_only:
                    plan = live[0].get("plan") or {}
                    bc.setdefault("history", []).insert(0, {
                        "ts": int(time.time()), "kind": "arb",
                        "stage": "simulated",
                        "detail": (
                            f"jup {live[0].get('path')} net ${live[0].get('net_usd')} "
                            f"jito_tip={plan.get('jito_tip_lamports', 0)}lam (sim)"
                        ),
                        "plan": plan,
                    })
                    bc["history"] = bc["history"][:40]
                    self.refresh_sol_broadcast_ready()
                elif near and self.sol_sim_only:
                    # still record a dry-run skip for closest near-miss
                    bc.setdefault("skipped", []).insert(0, {
                        "ts": int(time.time()), "kind": "arb",
                        "stage": "skip",
                        "why": f"near-miss gap ${near[0].get('gap_usd')} "
                               f"{near[0].get('path')}",
                        "plan": near[0].get("plan"),
                    })
                    bc["skipped"] = bc["skipped"][:30]
                    self.refresh_sol_broadcast_ready()
            except Exception as e:
                self.sol_bot("arb", "error", e)
                self.log("sol-arb", "error", e)
            await asyncio.sleep(70)

    async def sol_intel_loop(self):
        while True:
            try:
                self.sol_bot("intel", "running", "SOL activity intel…")
                sol = self.state["sol"]
                # Derive readiness from live SOL probes
                score = 0
                if sol.get("slot"):
                    score += 25
                if sol.get("watchlist"):
                    score += 25
                if (sol.get("mempool") or {}).get("count"):
                    score += 20
                if (sol.get("arb") or {}).get("near") or (sol.get("arb") or {}).get("opps"):
                    score += 20
                if sol.get("sol_price_usd"):
                    score += 10
                # hour/dow from sol-* log cats
                hours, dows = {}, {}
                for line in self.state.get("log") or []:
                    if not str(line.get("cat") or "").startswith("sol"):
                        continue
                    ts = line.get("ts") or 0
                    # SAST = UTC+2
                    lt = time.gmtime(ts + 2 * 3600)
                    hours[str(lt.tm_hour)] = hours.get(str(lt.tm_hour), 0) + 1
                    dows[str(lt.tm_wday)] = dows.get(str(lt.tm_wday), 0) + 1
                mp = sol.get("mempool") or {}
                sol["intel"] = {
                    "records": sum(1 for l in (self.state.get("log") or [])
                                   if str(l.get("cat") or "").startswith("sol")),
                    "readiness": min(100, score),
                    "hours": hours,
                    "dows": dows,
                    "moves": len(sol.get("watchlist") or []),
                    "last": {"slot": sol.get("slot"),
                             "protocol": sols.PROTOCOL},
                    "mev": mp.get("mev") or {},
                    "act_p": round(score / 100.0, 3),
                    "exp_net": ((sol.get("arb") or {}).get("meta") or {})
                    .get("best_net_usd"),
                    "steps": int((sol.get("intel") or {}).get("steps") or 0) + 1,
                    "brain": {
                        "advice": (
                            f"Solend watch={len(sol.get('watchlist') or [])} · "
                            f"jup near={len((sol.get('arb') or {}).get('near') or [])}"
                        ),
                        "min_liq_mult": 1.0,
                        "min_arb_mult": 1.0,
                        "prefer_edge": self.sol_edge_bias,
                        "protocol": sols.PROTOCOL,
                    },
                }
                self.refresh_sol_broadcast_ready()
                self.sol_bot("broadcast", "ok" if not self.sol_armed else "running",
                             sol["broadcast"]["summary"].get("label", "idle"))
                # Ensure intel hours never blank — seed current SAST hour if empty
                if not hours:
                    lt = time.gmtime(time.time() + 2 * 3600)
                    hours[str(lt.tm_hour)] = max(1, sol["intel"]["records"])
                    dows[str(lt.tm_wday)] = max(1, sol["intel"].get("moves") or 1)
                    sol["intel"]["hours"] = hours
                    sol["intel"]["dows"] = dows
                self.sol_bot("intel", "ok", f"ready {score}%")
                self.log("sol-intel", "info",
                         f"ready={score}% records={sol['intel']['records']} "
                         f"watch={len(sol.get('watchlist') or [])}")
            except Exception as e:
                self.sol_bot("intel", "error", e)
            await asyncio.sleep(85)

    # ------------------------------------------------------------ server
    async def start_loops(self):
        self._loop = asyncio.get_running_loop()
        self.refresh_sol_broadcast_ready()
        coros = (self.mempool_loop(), self.prices_loop(), self.funds_loop(),
                 self.sweep_loop(), self.competitor_loop(), self.arb_loop(),
                 self.intel_loop(),
                 self.sol_net_loop(), self.sol_mempool_loop(),
                 self.sol_funds_loop(), self.sol_sweep_loop(),
                 self.sol_competitor_loop(), self.sol_arb_loop(),
                 self.sol_intel_loop())
        for i, c in enumerate(coros):
            asyncio.create_task(self._late(i * 3.0, c))

    @staticmethod
    async def _late(delay, coro):
        await asyncio.sleep(delay)
        await coro

    async def ws_handler(self, request):
        ws = aiohttp.web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)
        self.clients.add(ws)
        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    if msg.data == "snapshot":
                        await ws.send_str(json.dumps(
                            {"type": "state", "data": self.snapshot()}))
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    break
        finally:
            self.clients.discard(ws)
        return ws

    async def state_api(self, request):
        return aiohttp.web.json_response(self.snapshot())

    async def health_api(self, request):
        """Liveness + mainnet freshness check for ops."""
        s = self.state
        now = time.time()
        bots = s.get("bots") or {}
        fresh = {}
        for name, b in bots.items():
            last = b.get("last")
            fresh[name] = {
                "status": b.get("status"),
                "age_s": None if last is None else int(now - last),
                "ok": b.get("status") == "ok" and last is not None
                      and (now - last) < 180,
            }
        # core mainnet signals
        core_ok = bool(s.get("block")) and bool(s.get("eth_price_usd"))
        mem_ok = fresh.get("mempool", {}).get("ok") or fresh.get("mempool", {}).get("status") == "ok"
        price_ok = fresh.get("prices", {}).get("ok") or fresh.get("prices", {}).get("status") == "ok"
        body = {
            "ok": core_ok and mem_ok and price_ok,
            "chain": s.get("chain"),
            "block": s.get("block"),
            "gas_gwei": s.get("gas_gwei"),
            "eth_price_usd": s.get("eth_price_usd"),
            "watchlist": len(s.get("watchlist") or []),
            "sweep_total": s.get("sweep_total"),
            "mempool_count": (s.get("mempool") or {}).get("count"),
            "arb_live": len((s.get("arb") or {}).get("opps") or []),
            "arb_near": len((s.get("arb") or {}).get("near") or []),
            "intel_records": (s.get("intel") or {}).get("records"),
            "bots": fresh,
            "rpc_pool": list(_RPC_POOL),
            "broadcast_ready": (s.get("broadcast") or {}).get("ready"),
        }
        return aiohttp.web.json_response(body, status=200 if body["ok"] else 503)

    async def control_api(self, request):
        """Arm / sim-only / edge-bias toggles for live profit mode.
        POST JSON: {"armed": bool, "sim_only": bool, "edge_bias": bool,
                    "arm_minutes": int, "chain": "eth"|"sol"}
        """
        try:
            body = await request.json()
        except Exception:
            body = {}
        chain = (body.get("chain") or "eth").lower()
        if chain == "sol":
            if "sim_only" in body:
                self.sol_sim_only = bool(body["sim_only"])
            if "edge_bias" in body:
                self.sol_edge_bias = bool(body["edge_bias"])
            if "armed" in body:
                self.sol_armed = bool(body["armed"])
                mins = int(body.get("arm_minutes") or 15)
                if self.sol_armed and mins > 0:
                    async def _disarm_sol():
                        await asyncio.sleep(mins * 60)
                        self.sol_armed = False
                        self.log("sol-broadcast", "warn",
                                 f"sol auto-disarmed after {mins}m")
                        self.refresh_sol_broadcast_ready()
                    asyncio.create_task(_disarm_sol())
            self.refresh_sol_broadcast_ready()
            self.log("sol-broadcast", "info",
                     f"control armed={self.sol_armed} sim_only={self.sol_sim_only} "
                     f"edge_bias={self.sol_edge_bias}")
            return aiohttp.web.json_response(self.state["sol"]["broadcast"])

        if "sim_only" in body:
            self.sim_only = bool(body["sim_only"])
        if "edge_bias" in body:
            self.edge_bias = bool(body["edge_bias"])
        if "armed" in body:
            self.armed = bool(body["armed"])
            mins = int(body.get("arm_minutes") or 15)
            if self.armed and mins > 0:
                async def _disarm():
                    await asyncio.sleep(mins * 60)
                    self.armed = False
                    self.log("broadcast", "warn",
                             f"auto-disarmed after {mins}m")
                    self.refresh_broadcast_ready()
                asyncio.create_task(_disarm())
        self.refresh_broadcast_ready()
        self.log("broadcast", "info",
                 f"control armed={self.armed} sim_only={self.sim_only} "
                 f"edge_bias={self.edge_bias}")
        return aiohttp.web.json_response(self.state["broadcast"])

    async def klines_api(self, request):
        symbol = request.query.get("symbol", "ETHUSDT").upper()
        if not symbol.isalnum() or len(symbol) > 20:
            return aiohttp.web.json_response({"error": "bad symbol"}, status=400)
        interval = (request.query.get("interval", "1h") or "1h").lower()
        if interval not in _KLINES_INTERVALS:
            return aiohttp.web.json_response(
                {"error": "bad interval", "allowed": sorted(_KLINES_INTERVALS)},
                status=400)
        try:
            limit = min(max(int(request.query.get("limit", "180")), 1), 500)
        except ValueError:
            limit = 180
        key = (symbol, interval, limit)
        now = time.time()
        cached = _KLINES_CACHE.get(key)
        if cached and now - cached[0] < _KLINES_TTL:
            return aiohttp.web.json_response(cached[1])
        try:
            data = await asyncio.wait_for(
                asyncio.to_thread(_fetch_klines, symbol, interval, limit),
                timeout=15)
            if data:
                _KLINES_CACHE[key] = (now, data)
                return aiohttp.web.json_response(data)
        except Exception:
            pass
        if cached:
            return aiohttp.web.json_response(cached[1])
        return aiohttp.web.json_response([])

    async def sol_status_api(self, request):
        """Light Solana public-RPC peek + SOLUSDT spot for the SOL tab shell."""
        out = {
            "ok": False,
            "slot": None,
            "epoch": None,
            "absolute_slot": None,
            "slot_index": None,
            "slots_in_epoch": None,
            "rpc": None,
            "sol_price_usd": None,
            "error": None,
            "ts": int(time.time()),
        }
        # Price from Binance ticker (reuse klines cache path if warm)
        try:
            cached = _KLINES_CACHE.get(("SOLUSDT", "1m", 2))
            if cached and time.time() - cached[0] < 30 and cached[1]:
                out["sol_price_usd"] = float(cached[1][-1][4])
            else:
                r = await asyncio.wait_for(
                    asyncio.to_thread(
                        lambda: _req.get(
                            "https://api.binance.com/api/v3/ticker/price",
                            params={"symbol": "SOLUSDT"},
                            headers={"User-Agent": _UA},
                            timeout=4,
                        ).json()),
                    timeout=5)
                if isinstance(r, dict) and r.get("price"):
                    out["sol_price_usd"] = float(r["price"])
        except Exception as e:
            out["error"] = f"price:{type(e).__name__}"

        rpcs = [
            "https://api.mainnet-beta.solana.com",
            "https://solana-rpc.publicnode.com",
        ]

        def _sol_rpc(url):
            hdr = {"User-Agent": _UA, "Content-Type": "application/json"}
            slot_body = {"jsonrpc": "2.0", "id": 1,
                         "method": "getSlot", "params": []}
            epoch_body = {"jsonrpc": "2.0", "id": 2,
                          "method": "getEpochInfo", "params": []}
            sr = _req.post(url, json=slot_body, headers=hdr, timeout=3)
            sr.raise_for_status()
            sj = sr.json()
            er = _req.post(url, json=epoch_body, headers=hdr, timeout=3)
            er.raise_for_status()
            ej = er.json()
            return sj.get("result"), ej.get("result")

        for url in rpcs:
            try:
                slot, epoch = await asyncio.wait_for(
                    asyncio.to_thread(_sol_rpc, url), timeout=4.5)
                if slot is None and not epoch:
                    continue
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
                out["error"] = None
                break
            except Exception as e:
                out["error"] = f"rpc:{type(e).__name__}"
                continue
        return aiohttp.web.json_response(out)

    async def ticker(self):
        while True:
            await asyncio.sleep(TICK)
            snap = self.snapshot()
            for ws in list(self.clients):
                try:
                    await ws.send_str(json.dumps(
                        {"type": "state", "data": snap}))
                except Exception:
                    pass


_EDGE = {
    "0xcdd342b2", "0x5476fbb7", "0x0e0744fe", "0xbbcbec75", "0x07cb1f8f",
}


def main():
    ap = argparse.ArgumentParser(description="Aave V4 / MEV live dashboard")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--broadcast", dest="broadcast", action="store_true",
                    default=True,
                    help="submit liquidation bundles + arb txs (default)")
    ap.add_argument("--no-broadcast", dest="broadcast", action="store_false",
                    help="monitor only; never submit")
    args = ap.parse_args()

    dash = Dashboard()
    dash.broadcast_enabled = bool(args.broadcast)
    dash.state["broadcast"]["enabled"] = dash.broadcast_enabled
    dash.refresh_broadcast_ready()
    app = aiohttp.web.Application()
    app.router.add_get("/", lambda r: aiohttp.web.FileResponse(
        os.path.join(HERE, "static", "index.html")))
    app.router.add_static("/static", os.path.join(HERE, "static"))
    app.router.add_get("/api/state", dash.state_api)
    app.router.add_get("/api/health", dash.health_api)
    app.router.add_post("/api/control", dash.control_api)
    app.router.add_get("/api/klines", dash.klines_api)
    app.router.add_get("/api/sol/status", dash.sol_status_api)
    app.router.add_get("/ws", dash.ws_handler)

    async def startup(app_):
        await dash.start_loops()
        asyncio.create_task(dash.ticker())
        mode = "BROADCAST ON" if dash.broadcast_enabled else "monitor-only"
        print(f"[dash] listening on http://{args.host}:{args.port} "
              f"[{mode} sim_only={dash.sim_only} armed={dash.armed}] "
              f"liq={ml.CONTRACT or '-'} arb={ARB_CONTRACT or '-'}",
              flush=True)
        ready = dash.state["broadcast"]["ready"]
        if ready["reasons"]:
            print(f"[dash] broadcast blockers: {'; '.join(ready['reasons'])}",
                  flush=True)

    app.on_startup.append(startup)
    aiohttp.web.run_app(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
