#!/usr/bin/env python3
"""Aave V4 + multi-protocol live Web3 dashboard -- liquidations only.

Streams everything to a WebSocket frontend:
  - mempool (pending txs, decoded Aave V4 Spoke calls, watch addrs)
  - funds (funder / sponsor / BOT EOA ETH + USDC + USDT + WETH balances)
  - Aave oracle reserve prices + ETH price + gas
  - liquidatable opportunities (multi-protocol HF sweep + Aave flash plans)
  - competitor liquidations in confirmed blocks (with our-model profit estimate)
  - intel / learning (readiness, hour/dow stats, dataset size)

With --broadcast (default on):
  - liquidations: sign + Flashbots eth_sendBundle via live_liquidator._submit

Wallet funding remains user-initiated in the browser
(ETH: MetaMask EVM -> sponsor; SOL: MetaMask Solana / Phantom -> sponsor).
"""
import argparse
import asyncio
import json
import os
import socket
import sys
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from paper_trader import PaperTrader

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


def _load_dotenv(path):
    """Load KEY=VALUE from .env without overriding a real process env. No secrets logged."""
    if not os.path.isfile(path):
        return
    try:
        with open(path, encoding="utf-8-sig") as f:
            raw = f.read()
    except OSError:
        return
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        if s.lower().startswith("export "):
            s = s[7:].strip()
        k, _, v = s.partition("=")
        k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        if k and k not in os.environ:
            os.environ[k] = v


_load_dotenv(os.path.join(HERE, ".env"))

LQ = os.path.join(HERE, "aave-v4-liquidation-bot")
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "aave-v4-monitor"))
sys.path.insert(0, os.path.join(HERE, "defi-arb"))
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
from intel_collector import aggregate_liq_intel  # noqa: E402
import profit_engine as pe  # noqa: E402
import precompute_eth as _pre_eth  # noqa: E402
import precompute_sol as _pre_sol  # noqa: E402
import profit_brain as brain  # noqa: E402
import sol_scanner as sols  # noqa: E402
import sol_lending as slend  # noqa: E402
import eth_lending as elend  # noqa: E402

# mev_bot (defi-arb) is optional — ARB removed; provide ETH price stub
try:
    import mev_bot  # noqa: E402
except ImportError:
    import types as _types
    mev_bot = _types.ModuleType("mev_bot")
    def _eth_price_stub(uni=None) -> float:
        try:
            import requests as _r
            r = _r.get("https://api.binance.com/api/v3/ticker/price",
                       params={"symbol": "ETHUSDT"}, timeout=8)
            r.raise_for_status()
            return float(r.json()["price"])
        except Exception:
            return 0.0
    def _build_universe_stub():
        return {}
    def _rpc_stub(url, method, params, timeout=10):
        raise RuntimeError("mev_bot not available")
    mev_bot.eth_price_usd = _eth_price_stub
    mev_bot.build_universe = _build_universe_stub
    mev_bot.rpc = _rpc_stub
    mev_bot.ARB_FLASH_KIND = ""
# Route every RPC through the fast requests transport (see _rpc_requests).
avm.rpc = _rpc_requests
lb.rpc = _rpc_requests
mev_bot.rpc = _rpc_requests

# Live broadcast knobs. Prefer env, else gitignored contracts.json.
_CONTRACTS_JSON = os.path.join(HERE, "contracts.json")
_BAKED = {}
if os.path.exists(_CONTRACTS_JSON):
    try:
        import json as _json
        with open(_CONTRACTS_JSON) as _f:
            _BAKED = _json.load(_f) or {}
    except Exception:
        _BAKED = {}
for _k in ("LIQ_CONTRACT", "LIQ_GENERIC_CONTRACT", "ARB_CONTRACT",
           "ARB_FLASH_KIND", "SOL_LIQ_PROGRAM", "SOL_ARB_PROGRAM"):
    _v = _BAKED.get(_k)
    if _v and not (os.environ.get(_k) or "").strip():
        os.environ[_k] = str(_v).strip()
GENERIC_LIQ = (
    os.environ.get("LIQ_GENERIC_CONTRACT")
    or _BAKED.get("LIQ_GENERIC_CONTRACT", "")
    or ""
)
if os.environ.get("LIQ_CONTRACT"):
    ml.CONTRACT = os.environ["LIQ_CONTRACT"]
elif _BAKED.get("LIQ_CONTRACT"):
    ml.CONTRACT = _BAKED["LIQ_CONTRACT"]
elif GENERIC_LIQ:
    ml.CONTRACT = GENERIC_LIQ
    os.environ.setdefault("LIQ_CONTRACT", GENERIC_LIQ)
if GENERIC_LIQ:
    os.environ.setdefault("LIQ_GENERIC_CONTRACT", GENERIC_LIQ)
_baked_kind = (os.environ.get("ARB_FLASH_KIND") or "").strip().lower()
if _baked_kind:
    mev_bot.ARB_FLASH_KIND = _baked_kind
# Leftover DEX-arb env — unused. Liquidation uses LIQ_CONTRACT / LIQ_GENERIC_CONTRACT.
ARB_CONTRACT = os.environ.get("ARB_CONTRACT") or _BAKED.get("ARB_CONTRACT", "")
SOL_LIQ_PROGRAM = os.environ.get("SOL_LIQ_PROGRAM") or _BAKED.get("SOL_LIQ_PROGRAM", "")
SOL_ARB_PROGRAM = os.environ.get("SOL_ARB_PROGRAM") or _BAKED.get("SOL_ARB_PROGRAM", "")
MIN_LIQ_PROFIT_USD = float(os.environ.get("MIN_LIQ_PROFIT_USD", "10"))
MIN_ARB_PROFIT_USD = float(os.environ.get("MIN_ARB_PROFIT_USD", "5"))  # unused leftover
MIN_SOL_ARB_USD = float(os.environ.get("MIN_SOL_ARB_USD", "0.05"))  # unused leftover
MIN_SOL_LIQ_USD = float(os.environ.get("MIN_SOL_LIQ_USD", "0.50"))
LIQ_COOLDOWN_BLOCKS = int(os.environ.get("LIQ_COOLDOWN_BLOCKS", "2"))
LIQ_LANDED_COOLDOWN_BLOCKS = int(os.environ.get("LIQ_LANDED_COOLDOWN_BLOCKS", "50"))
EDGE_BIAS = os.environ.get("EDGE_BIAS", "1") != "0"
SIM_ONLY_DEFAULT = os.environ.get("SIM_ONLY", "1") != "0"
COLD_WALLET = os.environ.get("COLD_WALLET", "")  # optional profit sweep destination
SOL_SIM_ONLY_DEFAULT = os.environ.get("SOL_SIM_ONLY", "1") != "0"
ARM_MINUTES_DEFAULT = int(os.environ.get("ARM_MINUTES", "15") or 15)
_PREFS_PATH = os.path.join(HERE, "broadcast_prefs.json")


def _load_broadcast_prefs() -> dict:
    """Runtime Keep Live flags. No secrets. Gitignored."""
    if not os.path.isfile(_PREFS_PATH):
        return {}
    try:
        with open(_PREFS_PATH, encoding="utf-8") as f:
            data = json.load(f) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_broadcast_prefs(keep_live: bool, sol_keep_live: bool) -> None:
    try:
        with open(_PREFS_PATH, "w", encoding="utf-8") as f:
            json.dump({
                "keep_live": bool(keep_live),
                "sol_keep_live": bool(sol_keep_live),
            }, f)
    except OSError:
        pass

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


_NO_COOLDOWN_METHODS = frozenset({
    "eth_getLogs", "txpool_status", "txpool_content",
})


def _healthy_jrpc(urls, method, params):
    now = time.time()
    last = None
    skipped = []
    for url in urls:
        if url in _RPC_COOLDOWN and now < _RPC_COOLDOWN[url]:
            skipped.append(url)
            continue
        try:
            out = _rpc_requests(url, method, params)
            _RPC_FAILS.pop(url, None)
            _RPC_COOLDOWN.pop(url, None)
            return out
        except Exception as e:  # noqa: BLE001
            last = e
            if method in _NO_COOLDOWN_METHODS:
                continue
            n = _RPC_FAILS.get(url, 0) + 1
            _RPC_FAILS[url] = n
            _RPC_COOLDOWN[url] = now + min(60 * (2 ** min(n, 3)), 300)
    for url in skipped:
        try:
            out = _rpc_requests(url, method, params)
            _RPC_FAILS.pop(url, None)
            _RPC_COOLDOWN.pop(url, None)
            return out
        except Exception as e:  # noqa: BLE001
            last = e
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
LIQ_SELS_ALL = {
    "0xc2fa746c",  # Aave V3/V4 liquidationCall
    "0xc3cecfd2",  # Compound V3 absorb
    "0xd8eabcb8",  # Morpho Blue liquidate
    "0x1bc1e9ba",  # Compound V3 batchAbsorb
}

# All lending pool addresses — classify txs to these as "aave" (lending) if not liq/router/spoke
_LENDING_POOLS = {
    "0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2",  # Aave V3 Pool
    "0xc13e21b648a5ee794902342038ff3adab66be987",  # Spark Lend
    "0xc3d688b66703497daa19211eedff47f25384cdc3",  # Compound cUSDCv3
    "0xa17581a9e3356d9a858b789d68b4d866e593ae94",  # Compound cWETHv3
    "0x3afdc9bca9213a35503b077a6072f3d0d5ab0840",  # Compound cUSDTv3
    "0xbbbbbbbbbb9cc5e90e3b3af64bdaf62c37eeffcb",  # Morpho Blue
}

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
    "0xc13e21b648a5ee794902342038ff3adab66be987": ("Spark Lend", "lending"),
    "0xc3d688b66703497daa19211eedff47f25384cdc3": ("Compound cUSDCv3", "lending"),
    "0xa17581a9e3356d9a858b789d68b4d866e593ae94": ("Compound cWETHv3", "lending"),
    "0x3afdc9bca9213a35503b077a6072f3d0d5ab0840": ("Compound cUSDTv3", "lending"),
    "0xbbbbbbbbbb9cc5e90e3b3af64bdaf62c37eeffcb": ("Morpho Blue", "lending"),
    lb.SPOKE.lower(): ("Aave V4 Spoke", "lending"),
}

_SEL_NAMES = {
    "0xc2fa746c": "liquidationCall",
    "0xc3cecfd2": "absorb",
    "0xd8eabcb8": "liquidate",
    "0x1bc1e9ba": "batchAbsorb",
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
    if inp[:10] in LIQ_SELS_ALL or inp[:10] in ic.LIQ_SELS:
        return "liq"
    if to in ic.ROUTERS:
        return "router"
    if to == lb.SPOKE.lower():
        return "spoke"
    if to in ic.AAVE_POOLS or to in _LENDING_POOLS:
        return "aave"
    return "other"


def _build_live_mev(txs, limit: int = 40) -> list:
    """Liquidation-only live MEV txs (liq/spoke/aave), gas-sorted — not truncated samples."""
    rows = []
    _liq_cls = {"liq", "spoke", "aave"}
    for t in txs or []:
        cls = _classify_tx(t)
        if cls not in _liq_cls:
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
HOUR_S = 3600
# Solana ~2.5 slots/s; used when a competitor row has a slot but no unix ts.
SOL_SLOTS_PER_HOUR = 9000


def _as_int(v, default=0):
    try:
        if v is None or v == "":
            return default
        return int(float(v))
    except (TypeError, ValueError):
        return default


def competitor_in_last_hour(row, now=None, last_slot=None):
    """True if a competitor liq belongs in the 1h share window.

    Unix ts is preferred. Slot-like values (< 1e9) are compared to last_slot.
    Missing clocks keep the row so a visible feed event is not dropped.
    """
    now = int(now or time.time())
    ts = _as_int(row.get("ts") if isinstance(row, dict) else 0, 0)
    if ts > now + 10_000:
        ts //= 1000
    if ts >= 1_000_000_000:
        return -60 <= (now - ts) <= HOUR_S
    slot = _as_int((row or {}).get("slot"), 0)
    if not slot and 0 < ts < 1_000_000_000:
        slot = ts
    tip = _as_int(last_slot, 0)
    if slot and tip:
        return 0 <= (tip - slot) <= SOL_SLOTS_PER_HOUR
    return True


def _searcher_addr(row):
    return str((row or {}).get("searcher") or (row or {}).get("liquidator") or "").strip()


def _honest_usd(v):
    """Positive reconstructed $ only. Never treat a fake loss as PnL."""
    try:
        if v is None or v == "":
            return None
        f = float(v)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def _row_est_usd(row):
    v = (row or {}).get("est_profit_usd")
    if v is None:
        v = (row or {}).get("est")
    return _honest_usd(v)


def _row_net_usd(row):
    v = (row or {}).get("net_est_usd")
    if v is None:
        v = (row or {}).get("net")
    return _honest_usd(v)


def _row_unix_ts(row):
    ts = _as_int((row or {}).get("ts"), 0)
    if ts > 1_000_000_000_000:
        ts //= 1000
    return ts if ts >= 1_000_000_000 else 0


def aggregate_searcher_share(rows, now=None, last_slot=None, top_n=10):
    """Count-share leaderboard for last-hour competitor liquidations.

    Share is event-count share so SOL leftover rows without $ still appear.
    Does not invent searcher addresses; rows with no liquidator are counted
    in n but omitted from the named leaderboard.
    """
    now = int(now or time.time())
    hour = [c for c in (rows or []) if competitor_in_last_hour(c, now, last_slot)]
    n = len(hour)
    by_s = {}
    searchers = set()
    profits, nets, gases = [], [], []
    missed = edge_n = revert_n = 0
    pair_counts = {}
    last_hit = 0
    for c in (rows or []):
        ts = _row_unix_ts(c)
        if ts > last_hit:
            last_hit = ts
    for c in hour:
        addr = _searcher_addr(c)
        if addr:
            searchers.add(addr)
            slot = by_s.setdefault(addr, {
                "addr": addr,
                "searcher": addr,
                "short": c.get("searcher_short") or addr[:10],
                "n": 0, "est": 0.0, "sum_est": 0.0,
                "missed": 0, "edge": 0,
            })
            slot["n"] += 1
            est_s = _row_est_usd(c)
            if est_s is not None:
                slot["est"] += est_s
                slot["sum_est"] += est_s
            if c.get("missed_by_us") or c.get("missed"):
                slot["missed"] += 1
            if c.get("edge"):
                slot["edge"] += 1
        est = _row_est_usd(c)
        if est is not None:
            profits.append(est)
        net = _row_net_usd(c)
        if net is not None:
            nets.append(net)
        if c.get("missed_by_us") or c.get("missed"):
            missed += 1
        if c.get("edge"):
            edge_n += 1
        flags = str(c.get("flags") or "").lower()
        if c.get("status") == 0 or "revert" in flags:
            revert_n += 1
        pair = (c.get("pair")
                or f"{c.get('coll_sym') or '?'}→{c.get('debt_sym') or '?'}")
        pair_counts[pair] = pair_counts.get(pair, 0) + 1
        if c.get("gas_used"):
            try:
                gases.append(int(c["gas_used"]))
            except (TypeError, ValueError):
                pass
    top = sorted(
        by_s.values(),
        key=lambda x: (-x["n"], -x["est"]),
    )[:top_n]
    for s in top:
        s["est"] = round(s["est"], 2)
        s["sum_est"] = round(s["sum_est"], 2)
        s["share"] = round(s["n"] / max(n, 1), 3)
        s["pct"] = round(100.0 * s["n"] / max(n, 1), 1)
    pair_total = sum(pair_counts.values()) or 1
    pair_mix = sorted(
        [{"pair": k, "n": v,
          "pct": round(100.0 * v / pair_total, 1),
          "share": round(v / pair_total, 3)}
         for k, v in pair_counts.items()],
        key=lambda x: -x["n"],
    )[:10]
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
    return {
        "count_1h": n,
        "unique_searchers": len(searchers),
        "top_searchers": top,
        "pair_mix": pair_mix,
        "sum_est_profit": round(sum(profits), 2) if profits else 0,
        "sum_net_est": round(sum(nets), 2) if nets else 0,
        "missed_by_us": missed,
        "miss_rate_pct": round(100.0 * missed / n, 1) if n else 0,
        "edge_n": edge_n,
        "revert_n": revert_n,
        "pressure": pressure,
        "avg_gas": int(sum(gases) / len(gases)) if gases else None,
        "total": len(rows or []),
        "est_n": len(profits),
        "last_hit_ts": last_hit or None,
    }


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
                "last_scan": None, "last_block": None,
                "scanned": 0, "skipped": {}, "submit_gate": "blocked",
                "from_block": None, "to_block": None, "n_logs": 0,
                "protocols": ["aave", "spark", "compound", "morpho"],
                "leftovers": [], "flash_fee_bps": 9, "flash_fee_src": "aave-v3",
            },
            "competitors": [],
            "competitors_meta": {
                "spokes": 1, "assets": 0, "status": "init",
                "count_1h": 0, "unique_searchers": 0,
                "avg_gas": None, "sum_est_profit": 0,
                "sum_net_est": 0, "missed_by_us": 0,
                "miss_rate_pct": 0, "edge_n": 0, "revert_n": 0,
                "pressure": "idle", "top_searchers": [], "pair_mix": [],
                "last_block": None, "last_scan": None,
                "from_block": None, "to_block": None, "n_logs": 0,
                "window": 0, "v3_pool": True, "errors": [],
                "total": 0, "est_n": 0, "last_hit_ts": None,
                "protocols": ["aave", "spark", "compound", "morpho"],
            },
            "intel": {"records": 0, "readiness": 0, "hours": {}, "dows": {},
                      "moves": 0, "last": None, "mev": {},
                      "brain": brain.policy({}),
                      "liq_intel": {
                          "volume_24h": 0.0, "count_24h": 0, "avg_size": 0.0, "gas_per_liq": 0.0,
                          "protocols": {"aave_v3": {"count": 0, "volume": 0.0}, "compound_v3": {"count": 0, "volume": 0.0},
                                        "morpho": {"count": 0, "volume": 0.0}, "spark": {"count": 0, "volume": 0.0},
                                        "solend": {"count": 0, "volume": 0.0}},
                          "health_dist": {"<1.0": 0, "1.0-1.05": 0, "1.05-1.1": 0, ">1.1": 0},
                          "competitors": {"searchers": 0, "success_rate": 0.0, "missed": 0},
                          "volume_history": [],
                          "pressure": "idle",
                      }},
            "bots": {b: {"status": "idle", "last": None, "msg": ""}
                     for b in ("mempool", "prices", "funds", "sweep",
                               "competitors", "intel", "broadcast")},
            "broadcast": {
                "enabled": True,
                "armed": False,          # must arm for real eth_sendBundle / cast send
                "keep_live": False,
                "sim_only": SIM_ONLY_DEFAULT,
                "edge_bias": EDGE_BIAS,
                "liq_contract": ml.CONTRACT or "",
                "liq_generic": GENERIC_LIQ or "",
                "min_liq_profit_usd": MIN_LIQ_PROFIT_USD,
            "dyn_min_liq": MIN_LIQ_PROFIT_USD,
            "liq_cooldown_blocks": LIQ_COOLDOWN_BLOCKS,
            "race_policy": "submit-if-floor",
            "plans_cached": 0,
                "peak_hour": False,
                "sponsor_target_eth": 0.03,
                "ready": {"liq": False, "reasons": []},
                "last_liq": None,
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
            "gas": deque(maxlen=MAXLEN),
            "eth": deque(maxlen=MAXLEN),
            "reserves": {rid: deque(maxlen=MAXLEN) for rid in RESERVE_SYMS},
            "sol_fee_median": deque(maxlen=MAXLEN),
            "sol_fee_p90": deque(maxlen=MAXLEN),
            "sol_tps": deque(maxlen=MAXLEN),
            "sol_comp_1h": deque(maxlen=MAXLEN),
            "sol_mp_liq": deque(maxlen=MAXLEN),
            "sol_mp_mev": deque(maxlen=MAXLEN),
        }
        self._paper_eth = PaperTrader.load("ETH")
        self._paper_sol = PaperTrader.load("SOL")
        self.state["paper_eth"] = self._paper_eth.state_dict()
        self.state["paper_sol"] = self._paper_sol.state_dict()
        self.clients = set()
        self.tx_pool = ThreadPoolExecutor(max_workers=8)
        self._uni = None
        self._spokes = {lb.SPOKE.lower()}
        self._spokes_fut = None
        self._liq_alerted = {}  # plan-key -> block of last submit attempt
        self._liq_landed = {}  # plan-key -> block of last landed/sent submit
        self._flash_plans = {}  # plan-key -> {block, plan, ts}
        self._liq_fire_lock = threading.Lock()
        self._liq_inflight = set()
        self._generic_liq_cached = None  # (ts, addr, ok)
        self._pending_aave_users = set()
        self._pending_aave_ok = False
        self.hybrid_enabled = False  # default off for safety
        self.hybrid_executor = None  # initialized in start_loops
        self._eth_hot_kick = None
        self._liq_harvest_block = 0
        self._comp_last_scanned = 0
        self.broadcast_enabled = True
        self.armed = False
        self.keep_live = False
        self.sim_only = SIM_ONLY_DEFAULT
        self.edge_bias = EDGE_BIAS
        self._price_moved = False
        self._contested = set()
        self.sol_armed = False
        self.sol_keep_live = False
        self.sol_sim_only = SOL_SIM_ONLY_DEFAULT
        self.sol_edge_bias = True
        self._eth_arm_gen = 0
        self._sol_arm_gen = 0
        self._eth_disarm_task = None
        self._sol_disarm_task = None
        self._arm_until = None
        self._sol_arm_until = None
        self._restore_keep_live()

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
                "competitors", "intel", "broadcast")
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
                "count": 0, "queued": 0, "method": "landing+prio",
                "spoke_txs": [], "watch_txs": [],
                "mev": {}, "mev_txs": [], "mev_samples": [],
                "hits": [], "liq_hits": [], "mev_hits": [],
                "top_to": [], "top_mev": [],
                "meta": {
                    "pending": 0, "queued": 0, "content_age_s": None,
                    "contested": 0, "mev_share_pct": 0, "pressure": "idle",
                    "median_fee": None, "p90_fee": None, "p99_fee": None,
                    "avg_fee": None, "max_fee": None, "zero_pct": 0,
                    "slots": 0, "tps": None, "nv_tps": None,
                    "liq_hits": 0, "mev_hits": 0, "refresh_n": 0,
                    "jito_bundles": 0, "decoded": 0,
                    "note": "Solend landing + Jito tips · µl/CU",
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
                "last_slot": None, "last_scan": None, "total": 0,
                "scanned": 0, "window": 0, "n_logs": 0, "errors": [],
                "leftovers": [], "est_n": 0, "last_hit_ts": None,
                "note": "Solend main market confirmed liquidations — no fake liqs",
            },
            "intel": {
                "records": 0, "readiness": 0, "hours": {}, "dows": {},
                "moves": 0, "last": None, "mev": {},
                "brain": {"advice": "SOL twin warming — Solend liquidations",
                          "min_liq_mult": 1.0,
                          "prefer_edge": True},
                "act_p": None, "exp_net": None, "steps": 0,
                "liq_intel": {
                    "volume_24h": 0.0, "count_24h": 0, "avg_size": 0.0, "gas_per_liq": 0.0,
                    "protocols": {"aave_v3": {"count": 0, "volume": 0.0}, "compound_v3": {"count": 0, "volume": 0.0},
                                  "morpho": {"count": 0, "volume": 0.0}, "spark": {"count": 0, "volume": 0.0},
                                  "solend": {"count": 0, "volume": 0.0},
                                  "kamino": {"count": 0, "volume": 0.0},
                                  "marginfi": {"count": 0, "volume": 0.0},
                                  "drift": {"count": 0, "volume": 0.0}},
                    "health_dist": {"<1.0": 0, "1.0-1.05": 0, "1.05-1.1": 0, ">1.1": 0},
                    "competitors": {"searchers": 0, "success_rate": 0.0, "missed": 0},
                    "volume_history": [],
                    "pressure": "idle",
                },
            },
            "bots": {b: {"status": "idle", "last": None, "msg": ""}
                     for b in bots},
            "broadcast": {
                "enabled": True,
                "armed": False,
                "keep_live": False,
                "sim_only": SOL_SIM_ONLY_DEFAULT,
                "edge_bias": True,
                "liq_contract": SOL_LIQ_PROGRAM,
                "min_liq_profit_usd": MIN_SOL_LIQ_USD,
                "dyn_min_liq": MIN_SOL_LIQ_USD,
                "peak_hour": False,
                "sponsor_target_eth": 0,
                "ready": {"liq": False, "reasons": [
                    "sol sim_only ON — Python Solend+Jito send after arm",
                ]},
                "last_liq": None,
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

    def _eth_keep_live_ready(self):
        """True when a keystore (or sponsor) is configured — not a fresh clone."""
        ks = getattr(lb, "KEYSTORE_PATH", "") or ""
        sp = getattr(ll, "SPONSOR_KEYSTORE", "") or ""
        pw = bool(getattr(lb, "KEYSTORE_PW", "") or getattr(ll, "SPONSOR_PW", ""))
        has_file = (ks and os.path.isfile(ks)) or (sp and os.path.isfile(sp))
        return bool(pw and has_file)

    def _sol_keep_live_ready(self):
        env = os.environ.get("SOL_KEYPAIR") or ""
        path = getattr(sols, "BOT_KEY_PATH", "") or ""
        return bool((env and os.path.isfile(env)) or (path and os.path.isfile(path)))

    def _restore_keep_live(self):
        prefs = _load_broadcast_prefs()
        if prefs.get("keep_live"):
            self.keep_live = True
            if self._eth_keep_live_ready():
                self.sim_only = False
                self.armed = True
                self._arm_until = None
        if prefs.get("sol_keep_live"):
            self.sol_keep_live = True
            if self._sol_keep_live_ready():
                self.sol_sim_only = False
                self.sol_armed = True
                self._sol_arm_until = None

    def _persist_keep_live(self):
        _save_broadcast_prefs(self.keep_live, self.sol_keep_live)

    def _cancel_eth_disarm(self):
        self._eth_arm_gen += 1
        t = self._eth_disarm_task
        self._eth_disarm_task = None
        if t is not None:
            try:
                t.cancel()
            except Exception:
                pass

    def _cancel_sol_disarm(self):
        self._sol_arm_gen += 1
        t = self._sol_disarm_task
        self._sol_disarm_task = None
        if t is not None:
            try:
                t.cancel()
            except Exception:
                pass

    def _schedule_eth_disarm(self, mins):
        self._cancel_eth_disarm()
        if self.keep_live or int(mins or 0) <= 0:
            self._arm_until = None
            return
        gen = self._eth_arm_gen
        self._arm_until = time.time() + int(mins) * 60

        async def _disarm():
            try:
                await asyncio.sleep(int(mins) * 60)
            except asyncio.CancelledError:
                return
            if gen != self._eth_arm_gen or self.keep_live:
                return
            self.armed = False
            self._arm_until = None
            self.log("broadcast", "warn", f"auto-disarmed after {mins}m")
            self.refresh_broadcast_ready()

        try:
            self._eth_disarm_task = asyncio.get_running_loop().create_task(_disarm())
        except RuntimeError:
            self._eth_disarm_task = None

    def _schedule_sol_disarm(self, mins):
        self._cancel_sol_disarm()
        if self.sol_keep_live or int(mins or 0) <= 0:
            self._sol_arm_until = None
            return
        gen = self._sol_arm_gen
        self._sol_arm_until = time.time() + int(mins) * 60

        async def _disarm():
            try:
                await asyncio.sleep(int(mins) * 60)
            except asyncio.CancelledError:
                return
            if gen != self._sol_arm_gen or self.sol_keep_live:
                return
            self.sol_armed = False
            self._sol_arm_until = None
            self.log("sol-broadcast", "warn", f"sol auto-disarmed after {mins}m")
            self.refresh_sol_broadcast_ready()

        try:
            self._sol_disarm_task = asyncio.get_running_loop().create_task(_disarm())
        except RuntimeError:
            self._sol_disarm_task = None

    def _arm_note(self, keep, armed, until):
        if keep and armed:
            return "armed · auto-renew"
        if armed and until:
            left = int(until - time.time())
            if left > 0:
                m, s = divmod(left, 60)
                return f"armed {m}m{s:02d}s"
            return "arm expired"
        if armed:
            return "armed"
        return "not armed"

    def _enforce_keep_live(self):
        """Re-arm if Keep Live is on and keys exist (beats stale 15m timers)."""
        changed = False
        if self.keep_live and self._eth_keep_live_ready():
            if self.sim_only or not self.armed:
                self.sim_only = False
                self.armed = True
                self._cancel_eth_disarm()
                self._arm_until = None
                changed = True
                self.log("broadcast", "ok", "keep-live re-armed")
        if self.sol_keep_live and self._sol_keep_live_ready():
            if self.sol_sim_only or not self.sol_armed:
                self.sol_sim_only = False
                self.sol_armed = True
                self._cancel_sol_disarm()
                self._sol_arm_until = None
                changed = True
                self.log("sol-broadcast", "ok", "sol keep-live re-armed")
        if changed:
            self.refresh_broadcast_ready()
            self.refresh_sol_broadcast_ready()

    async def keep_live_loop(self):
        while True:
            try:
                self._enforce_keep_live()
            except Exception as e:  # noqa: BLE001
                self.log("broadcast", "info", f"keep-live: {e}")
            await asyncio.sleep(30)

    def refresh_sol_broadcast_ready(self):
        sol = self.state.setdefault("sol", {})
        bc = sol.setdefault("broadcast", {})
        liq_blockers = sols.live_submit_blockers("liq")
        funds = sol.get("funds") or {}
        funded, fund_rs = sols.wallets_funded_enough(funds)
        liq_ok = (not liq_blockers) and funded
        reasons = []
        seen = set()
        if self.sol_keep_live and not self._sol_keep_live_ready():
            reasons.append("Keep Live on but no SOL bot keypair — staying sim")
        if self.sol_sim_only:
            reasons.append("sol sim_only ON — hunter still runs liquidations; Keep Live / Arm to submit")
        if not self.sol_armed:
            reasons.append("sol not armed (Broadcast → Keep Live / Arm LIVE)")
        if not funded:
            reasons.extend(fund_rs)
        for r in liq_blockers:
            if r not in seen:
                seen.add(r)
                reasons.append(f"liq: {r}")
        px = sol.get("sol_price_usd")
        prio = sol.get("priority_fee")
        jito = float(os.environ.get("SOL_JITO_TIP_SOL", "0.00001") or 0)
        dyn_liq = pe.dynamic_min_sol_liq_usd(prio, px, MIN_SOL_LIQ_USD,
                                            jito_sol=jito)
        bc.update({
            "enabled": True,
            "armed": self.sol_armed,
            "keep_live": self.sol_keep_live,
            "arm_until": self._sol_arm_until,
            "arm_note": self._arm_note(self.sol_keep_live, self.sol_armed,
                                       self._sol_arm_until),
            "sim_only": self.sol_sim_only,
            "edge_bias": self.sol_edge_bias,
            "liq_contract": SOL_LIQ_PROGRAM,
            "min_liq_profit_usd": MIN_SOL_LIQ_USD,
            "dyn_min_liq": dyn_liq,
            "ready": {"liq": liq_ok, "reasons": reasons},
        })
        hist = bc.get("history") or []
        skipped = bc.get("skipped") or []
        n_sent = sum(1 for h in hist if (h.get("stage") or "") in ("sent", "ok"))
        n_sim = sum(1 for h in hist if (h.get("stage") or "") == "simulated")
        n_liq = sum(1 for h in hist if h.get("kind") == "liq")
        pressure = "idle"
        label = "idle"
        if self.sol_armed and liq_ok:
            pressure, label = "hot", (
                "armed · auto-renew" if self.sol_keep_live else "armed live")
        elif self.sol_sim_only:
            pressure, label = "quiet", "sim only"
        elif reasons:
            pressure, label = "blocked", "gates blocked"
        bc["pressure"] = pressure
        bc["summary"] = {
            "pressure": pressure, "label": label,
            "n_hist": len(hist), "n_sent": n_sent, "n_sim": n_sim,
            "n_skip": len(skipped),
            "n_liq": n_liq,
            "last_stage": (hist[0].get("stage") if hist else None),
            "last_kind": (hist[0].get("kind") if hist else None),
        }
        return liq_ok, reasons

    def _sol_submit_gate(self, kind: str = "liq"):
        if self.sol_sim_only:
            return "sim", "sol sim_only"
        if not self.sol_armed:
            return "blocked", "sol not armed"
        reasons = list(sols.live_submit_blockers("liq"))
        funds = (self.state.get("sol") or {}).get("funds") or {}
        funded, fund_rs = sols.wallets_funded_enough(funds)
        if not funded:
            reasons.extend(fund_rs)
        if reasons:
            return "blocked", reasons[0]
        if self.sol_keep_live:
            return "live", "keep-live auto-renew"
        return "live", "solend+jito"

    def _sol_decorate_liq(self, o: dict) -> dict:
        submit, reason = self._sol_submit_gate("liq")
        o["submit"] = submit
        o["submit_reason"] = reason
        pk = o.get("obligation") or o.get("user") or ""
        o["user"] = pk
        o["solscan"] = f"https://solscan.io/account/{pk}" if pk else ""
        o["coll_sym"] = o.get("coll_sym") or o.get("collateral_sym")
        o["race"] = bool(o.get("contested") or o.get("race"))
        plan = o.get("plan") or {}
        o["flash"] = True
        o["flash_fee_bps"] = o.get("flash_fee_bps") or plan.get("flash_fee_bps")
        o["flash_fee_usd"] = o.get("flash_fee_usd") or plan.get("flash_fee_usd")
        o["flash_fee_src"] = o.get("flash_fee_src") or "solend"
        leftover = plan.get("leftover") or plan.get("account_gaps") or o.get(
            "account_gaps") or []
        o["leftover"] = leftover
        return o

    def _sol_record(self, rec: dict, skip: bool = False):
        """Append a SOL sim/blocked/skip row to broadcast history."""
        sol = self.state.setdefault("sol", {})
        bc = sol.setdefault("broadcast", {})
        if skip:
            bc.setdefault("skipped", []).insert(0, rec)
            bc["skipped"] = bc["skipped"][:30]
        else:
            bc.setdefault("history", []).insert(0, rec)
            bc["history"] = bc["history"][:40]
            if rec.get("kind") == "liq":
                bc["last_liq"] = rec
        self.refresh_sol_broadcast_ready()

    def _sol_maybe_submit(self, kind: str, opp: dict, plan: dict) -> dict:
        """Sim-only by default; LIVE stays gated. Never silent-sends."""
        floor = (self.state.get("sol") or {}).get("broadcast") or {}
        min_usd = float(
            floor.get("dyn_min_liq") or MIN_SOL_LIQ_USD
        )
        profit = float(
            opp.get("profit_usd") or opp.get("net_usd")
            or plan.get("expected_profit_usd") or plan.get("expected_net_usd")
            or 0
        )
        key = plan.get("obligation") or plan.get("path") or ""
        now = time.time()
        for h in (floor.get("history") or [])[:8]:
            hp = h.get("plan") or {}
            same = (
                (key and (hp.get("obligation") == plan.get("obligation")
                          or hp.get("path") == plan.get("path")))
            )
            if same and now - float(h.get("ts") or 0) < 90:
                return h
        if profit <= 0 or profit < min_usd:
            rec = {
                "ts": int(time.time()), "kind": kind, "stage": "skip",
                "why": (f"non-positive net ${profit}" if profit <= 0
                        else f"below floor ${min_usd} (net ${profit})"),
                "plan": plan,
            }
            self._sol_record(rec, skip=True)
            return rec
        if kind == "liq" and self.sol_edge_bias:
            contested = bool(opp.get("contested"))
            if contested and not pe.sol_is_edge_opp(opp):
                rec = {
                    "ts": int(time.time()), "kind": "liq", "stage": "skip",
                    "why": "mempool-contested crowded name",
                    "plan": plan,
                }
                self._sol_record(rec, skip=True)
                return rec
        rec = sols.submit_sol_plan(
            plan, sim_only=self.sol_sim_only, armed=self.sol_armed,
            funds=(self.state.get("sol") or {}).get("funds"),
        )
        rec["kind"] = kind
        if rec.get("stage") == "skip":
            self._sol_record(rec, skip=True)
        else:
            self._sol_record(rec)
        lvl = "info" if rec.get("stage") == "sent" else "warn"
        self.log(
            "sol-broadcast",
            lvl,
            rec.get("detail") or rec.get("why") or rec.get("stage"),
        )
        return rec

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
                "gas": list(self.hist["gas"]),
                "eth": list(self.hist["eth"]),
                "reserves": {str(k): list(v) for k, v in self.hist["reserves"].items()},
                "sol_fee_median": list(self.hist.get("sol_fee_median") or []),
                "sol_fee_p90": list(self.hist.get("sol_fee_p90") or []),
                "sol_tps": list(self.hist.get("sol_tps") or []),
                "sol_comp_1h": list(self.hist.get("sol_comp_1h") or []),
                "sol_mp_liq": list(self.hist.get("sol_mp_liq") or []),
                "sol_mp_mev": list(self.hist.get("sol_mp_mev") or []),
            },
        }
        out["paper_eth"] = self._paper_eth.state_dict()
        out["paper_sol"] = self._paper_sol.state_dict()
        out["hybrid_execution"] = {
            "enabled": self.hybrid_enabled,
            "phase": self.hybrid_executor.phase.value if self.hybrid_executor else "idle",
            "stats": self.hybrid_executor.tracker.get_stats() if self.hybrid_executor else {},
        }
        # Pre-compute cache stats
        try:
            import precompute_eth as pe_mod
            import precompute_sol as psol_mod
            out["precompute"] = {
                "eth": pe_mod.cache_stats(),
                "sol": psol_mod.cache_stats(),
            }
        except ImportError:
            out["precompute"] = {"eth": {}, "sol": {}}
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

    def _liq_pid(self, o):
        pid = str((o or {}).get("protocol_id") or (o or {}).get("protocol") or "aave").lower()
        if pid in ("v3", "v4", "aave", ""):
            return "aave"
        if "spark" in pid:
            return "spark"
        if "compound" in pid or pid == "comet":
            return "compound"
        if "morpho" in pid:
            return "morpho"
        return pid or "aave"

    def _plan_key(self, pid, user):
        return f"{(pid or 'aave').lower()}:{(user or '').lower()}"

    def _generic_executor_addr(self):
        for a in (GENERIC_LIQ, ml.CONTRACT):
            if a and str(a).startswith("0x"):
                try:
                    if elend.executor.contract_is_generic(a):
                        return a
                except Exception:
                    continue
        return ""

    def _generic_liq_ok(self):
        now = time.time()
        hit = self._generic_liq_cached
        if hit and now - hit[0] < 60:
            return bool(hit[2])
        addr = self._generic_executor_addr()
        ok = bool(addr)
        self._generic_liq_cached = (now, addr, ok)
        return ok

    def refresh_broadcast_ready(self):
        reasons = []
        liq_ok = False
        gas = self.state.get("gas_gwei")
        dyn_liq = pe.dynamic_min_liq_profit_usd(gas, MIN_LIQ_PROFIT_USD)
        pol = brain.policy(self.state)
        self.state["intel"]["brain"] = pol
        dyn_liq *= float(pol.get("min_liq_mult") or 1.0)
        if pol.get("prefer_edge"):
            self.edge_bias = True
        peak = pe.is_peak_hour(self.state.get("intel", {}).get("hours") or {})
        sponsor_tgt = pe.sponsor_target_eth(gas)
        if not self.broadcast_enabled:
            reasons.append("broadcast disabled (--no-broadcast)")
        else:
            if self.keep_live and not self._eth_keep_live_ready():
                reasons.append(
                    "Keep Live on but no keystore — staying sim until funded")
            if not self.armed and not self.sim_only:
                reasons.append("not armed (Broadcast → Keep Live / Arm LIVE)")
            if not ml.CONTRACT:
                reasons.append(
                    "LIQ_CONTRACT / LIQ_GENERIC_CONTRACT unset — "
                    "deploy GenericFlashLiquidator (contracts/DEPLOY.md)")
            elif not self._contract_has_code(ml.CONTRACT):
                reasons.append(
                    f"LIQ_CONTRACT {ml.CONTRACT[:10]}… has no code on mainnet")
            elif not lb.KEYSTORE_PW and not ll.SPONSOR_PW:
                reasons.append("KEYSTORE_PW / SPONSOR_PW missing")
            else:
                liq_ok = True
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
        elif self.armed and liq_ok:
            pressure, label = "hot", (
                "armed · auto-renew" if self.keep_live else "armed live")
        elif self.armed:
            pressure, label = "elevated", "armed · blocked"
        elif self.sim_only and liq_ok:
            pressure, label = "quiet", "sim ready"
        elif self.sim_only:
            pressure, label = "busy", "sim · blocked"
        elif liq_ok:
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
            "keep_live": self.keep_live,
            "arm_until": self._arm_until,
            "arm_note": self._arm_note(self.keep_live, self.armed, self._arm_until),
            "sim_only": self.sim_only,
            "edge_bias": self.edge_bias,
            "liq_contract": ml.CONTRACT or "",
            "liq_generic": self._generic_executor_addr() or GENERIC_LIQ or "",
            "min_liq_profit_usd": MIN_LIQ_PROFIT_USD,
            "dyn_min_liq": round(dyn_liq, 2),
            "liq_cooldown_blocks": LIQ_COOLDOWN_BLOCKS,
            "race_policy": "submit-if-floor",
            "plans_cached": len(self._flash_plans),
            "pending_aave": {
                "ok": bool(self._pending_aave_ok),
                "n": len(self._pending_aave_users),
            },
            "peak_hour": peak,
            "sponsor_target_eth": sponsor_tgt,
            "brain_advice": pol.get("advice"),
            "brain_act": pol.get("act_prob"),
            "ready": {"liq": liq_ok, "reasons": reasons},
            "near_miss_hints": pe.near_miss_hints(),
            "pressure": pressure,
            "summary": summary,
        })
        return liq_ok, reasons

    def _record_broadcast(self, kind, rec):
        entry = {"ts": int(time.time()), "kind": kind, **rec}
        self.state["broadcast"]["history"].insert(0, entry)
        self.state["broadcast"]["history"] = self.state["broadcast"]["history"][:40]
        if kind == "liq":
            self.state["broadcast"]["last_liq"] = entry
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

    def _broadcast_liquidation(self, user, profit_usd, opp=None):
        """Sign + (sim|submit) a flash-liquidation bundle for `user`.

        Race path is in-memory: cached/adapter plan → sign → eth_sendBundle spray.
        Contested users still submit when sim net ≥ floor (higher tip).
        """
        pid = self._liq_pid(opp) if opp else "aave"
        uk = (user or "").lower()
        key = self._plan_key(pid, uk)
        block = self.state["block"] or ll.latest_block()
        landed = self._liq_landed.get(key, 0)
        if landed and block - landed < LIQ_LANDED_COOLDOWN_BLOCKS:
            return {"stage": "skip", "reason": "landed-cooldown", "user": user,
                    "protocol": pid}
        last = self._liq_alerted.get(key, 0)
        if last and block - last < LIQ_COOLDOWN_BLOCKS:
            return {"stage": "skip", "reason": "cooldown", "user": user,
                    "protocol": pid}
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
                    "user": user, "protocol": pid}
        skip, why = pe.should_skip_user(
            user, self._contested,
            pe.recent_competitor_users(self.state.get("competitors") or []),
            net_usd=profit_usd, min_usd=min_p)
        if skip:
            self.state["broadcast"]["skipped"].insert(
                0, {"ts": int(time.time()), "user": user[:12], "why": why})
            self.state["broadcast"]["skipped"] = self.state["broadcast"]["skipped"][:30]
            return {"stage": "skip", "reason": why, "user": user, "protocol": pid}
        with self._liq_fire_lock:
            if key in self._liq_inflight:
                return {"stage": "skip", "reason": "inflight", "user": user,
                        "protocol": pid}
            self._liq_inflight.add(key)
        try:
            return self._broadcast_liquidation_locked(
                user, uk, profit_usd, min_p, why, block, opp=opp, pid=pid, key=key)
        finally:
            self._liq_inflight.discard(key)

    def _materialize_liq_plan(self, out, pid):
        """Attach executor contract + ABI args the signer consumes."""
        out = dict(out)
        out.setdefault("protocol_id", pid)
        if pid == "aave":
            out.setdefault("protocol", "Aave")
            out.setdefault("live_ok", True)
        fp = out.get("flash_plan") or {}
        if fp.get("liq_sig") and not out.get("liq_sig"):
            out["liq_sig"] = fp["liq_sig"]
            out["liq_args"] = list(fp.get("liq_args") or [])
        if not out.get("collateralAsset"):
            out["collateralAsset"] = out.get("coll_addr") or fp.get("coll") or ""
        if not out.get("debtAsset"):
            out["debtAsset"] = out.get("debt_addr") or fp.get("flash_asset") or ""
        if pid == "aave" and not out.get("liq_args"):
            out["contract"] = out.get("contract") or ml.CONTRACT or ""
            out["liq_sig"] = out.get("liq_sig") or getattr(ml, "LIQ_SIG", None) or (
                "flashLiquidate(address,address,address,uint256)")
        else:
            gen = self._generic_executor_addr()
            out["contract"] = gen or out.get("contract") or ""
        if fp.get("gas_limit"):
            out["gas_limit"] = fp["gas_limit"]
        if fp.get("flash_amount") and not out.get("debtToCover"):
            out["debtToCover"] = fp["flash_amount"]
        return out

    def _broadcast_liquidation_locked(self, user, uk, profit_usd, min_p, why, block,
                                      opp=None, pid="aave", key=""):
        out = None
        cached = False
        if opp and (opp.get("liq_args") or ((opp.get("flash_plan") or {}).get("liq_args"))):
            out = dict(opp)
        if out is None:
            out = self._cached_flash_plan(uk, block, need_liquidatable=True, pid=pid)
            cached = bool(out)
        if out is None and pid == "aave":
            out = ml.build_full_plan(
                user,
                gas_gwei=self.state.get("gas_gwei"),
                eth_usd=self.state.get("eth_price_usd"),
                allow_near=False)
        if out is None:
            return {"stage": "refuse", "reason": "no flash plan", "user": user,
                    "protocol": pid}
        if not out.get("liquidatable", True) and pid == "aave":
            if not opp:
                return {"stage": "skip", "reason": "not liquidatable", "user": user}
        out = self._materialize_liq_plan(out, pid)
        if not self._liq_live_ok(out):
            return {
                "stage": "skip",
                "reason": out.get("live_block_reason")
                or "executor cannot hit this venue",
                "user": user,
                "protocol": out.get("protocol"),
            }
        net = out.get("net_usd")
        if net is None:
            net = profit_usd
        try:
            net_f = float(net)
        except (TypeError, ValueError):
            net_f = None
        if net_f is not None and net_f < min_p:
            return {"stage": "skip",
                    "reason": f"net ${net_f:.2f} < dyn min ${min_p:.2f}",
                    "user": user}
        out["gas_gwei"] = float(self.state.get("gas_gwei") or out.get("gas_gwei") or 2)
        if pid == "aave":
            out["contract"] = out.get("contract") or ml.CONTRACT or ""
        prio_mult = pe.race_prio_mult(why)
        do_sim_only = self.sim_only or not self.armed
        if not out.get("contract"):
            reason = (
                "LIQ_CONTRACT unset" if pid == "aave"
                else "LIQ_GENERIC_CONTRACT unset — deploy GenericFlashLiquidator "
                "(contracts/DEPLOY.md)")
            return {"stage": "refuse", "reason": reason,
                    "user": user, "profit_usd": profit_usd,
                    "race": why, "sim_only": True, "protocol": pid}

        signed_hex = None
        signer = None
        try:
            signed_hex, signer, _ks = ll._sign_tx(
                out, block + 1, "bot", prio_mult=prio_mult)
        except Exception as e:
            if not do_sim_only:
                return {"stage": "error", "reason": f"sign: {e}"[:200],
                        "user": user, "race": why, "cached_plan": cached,
                        "protocol": pid}
            # sim still records the attempt with economics
            self._liq_alerted[key or uk] = block
            return {
                "stage": "simulated",
                "reason": f"sim — sign skipped ({e})"[:200],
                "user": user, "profit_usd": profit_usd,
                "race": why, "prio_mult": prio_mult,
                "cached_plan": cached, "sim_only": True, "protocol": pid,
            }

        sponsor_hex = None
        bot_eth = float((self.state.get("funds") or {}).get("bot", {}).get("eth") or 0)
        if (not do_sim_only and bot_eth < 0.008
                and ll.SPONSOR_KEYSTORE and ll.SPONSOR_PW):
            try:
                ll.SPONSOR_AMOUNT_ETH = pe.sponsor_target_eth(
                    self.state.get("gas_gwei"))
                sponsor_hex, _sa = ll._sign_sponsor(block + 1)
            except Exception:
                sponsor_hex = None
        if sponsor_hex:
            body = broadcast.build_sponsored_bundle(
                signed_hex, sponsor_hex, block + 1)
        else:
            body = ll.build_bundle_body(signed_hex, block + 1)
        result = ll._submit(None, body, block + 1, sim_only=do_sim_only,
                            sponsor_hex=sponsor_hex)
        result["user"] = user
        result["profit_usd"] = profit_usd if profit_usd is not None else net_f
        result["sim_only"] = do_sim_only
        result["race"] = why
        result["prio_mult"] = prio_mult
        result["cached_plan"] = cached
        result["signer"] = (signer[:10] + "…") if signer else None
        result["protocol"] = pid
        self._liq_alerted[key or uk] = block
        stage = (result.get("stage") or "").lower()
        if stage in ("sent", "ok"):
            self._liq_landed[key or uk] = block
        return result

    def _cached_flash_plan(self, user, block, need_liquidatable=False, pid="aave"):
        rec = self._flash_plans.get(self._plan_key(pid, user))
        if not rec:
            rec = self._flash_plans.get((user or "").lower())
        if not rec:
            return None
        pb = int(rec.get("block") or 0)
        plan = rec.get("plan")
        if not plan:
            return None
        if need_liquidatable:
            # liquidatable calldata is reusable for the next block
            if not plan.get("liquidatable", True) and pid == "aave":
                return None
            if not block or (int(block) - pb) > 1:
                return None
            return plan
        # near-HF plans expire at the next block so we re-check HF
        if not block or int(block) != pb:
            return None
        return plan

    def _put_flash_plan(self, user, block, plan):
        uk = (user or "").lower()
        if not uk.startswith("0x") or not plan:
            return
        pid = self._liq_pid(plan)
        key = self._plan_key(pid, uk)
        self._flash_plans[key] = {
            "block": int(block or 0), "plan": plan, "ts": time.time(),
        }
        if len(self._flash_plans) > 64:
            oldest = sorted(
                self._flash_plans.items(),
                key=lambda kv: kv[1].get("ts") or 0)
            for k, _ in oldest[: len(self._flash_plans) - 48]:
                self._flash_plans.pop(k, None)

    def _invalidate_stale_plans(self, block):
        dead = [u for u, r in self._flash_plans.items()
                if int(block or 0) - int(r.get("block") or 0) > 1]
        for u in dead:
            self._flash_plans.pop(u, None)

    def _kick_eth_hot(self):
        ev = self._eth_hot_kick
        if ev is not None:
            try:
                ev.set()
            except Exception:
                pass

    def _poll_pending_aave(self):
        """Pending Aave users from mempool + optional pending logs. Fail-open."""
        users = set()
        for t in (self.state.get("mempool") or {}).get("spoke_txs") or []:
            u = str(t.get("user") or "").lower()
            if u.startswith("0x") and len(u) >= 42:
                users.add(u[:42])
        users.update(x.lower()[:42] for x in (self._contested or [])
                     if str(x).startswith("0x"))
        pending_ok = False
        try:
            got = lb.pending_event_users()
            pending_ok = bool(got.get("pending_ok"))
            for u in got.get("users") or []:
                if str(u).startswith("0x"):
                    users.add(str(u).lower()[:42])
        except Exception:
            pending_ok = False
        blk = int(self.state.get("block") or 0)
        if blk:
            try:
                h = lb.harvest_event_users(max(1, blk - 2), blk)
                for u in h.get("users") or []:
                    if str(u).startswith("0x"):
                        users.add(str(u).lower()[:42])
            except Exception:
                pass
        self._pending_aave_users = users
        self._pending_aave_ok = pending_ok
        return users

    def _merge_liq_opp_from_plan(self, plan):
        if not plan or not plan.get("liquidatable"):
            return
        net = plan.get("net_usd")
        if net is not None and float(net) <= 0:
            return
        uk = (plan.get("user") or "").lower()
        rec = {
            "user": uk,
            "hf": plan.get("healthFactor") or plan.get("hf"),
            "coll_sym": plan.get("coll_sym") or "?",
            "debt_sym": plan.get("debt_sym") or "?",
            "coll_usd": plan.get("coll_usd"),
            "debt_usd": plan.get("debt_usd"),
            "bonus_usd": plan.get("bonus_usd"),
            "gas_usd": plan.get("gas_usd"),
            "profit_usd": plan.get("profit_usd") or plan.get("net_usd"),
            "net_usd": plan.get("net_usd"),
            "protocol": plan.get("protocol") or "Aave",
            "protocol_id": plan.get("protocol_id") or "aave",
            "protocol_label": plan.get("protocol_label") or "Aave",
            "live_ok": plan.get("live_ok", True),
            "flash_fee_bps": plan.get("flash_fee_bps"),
            "flash_fee_usd": plan.get("flash_fee_usd"),
            "flash_note": plan.get("flash_note") or "Aave V3 flashLoan",
            "contested": uk in self._contested,
            "recent_competitor": False,
            "race": uk in self._contested,
            "source": "hot",
            "plan_cached": True,
        }
        rec = self._decorate_liq_opp(rec)
        pid = rec.get("protocol_id") or "aave"
        opps = list(self.state.get("opportunities") or [])
        opps = [o for o in opps if not (
            (o.get("user") or "").lower() == uk
            and (o.get("protocol_id") or "aave") == pid)]
        opps.append(rec)
        self.state["opportunities"] = pe.rank_liq_opps(
            opps, edge_bias=self.edge_bias)

    def _precompute_closest(self):
        """Keep flash plans ready for closest-10 + pending/contested users."""
        block = int(self.state.get("block") or 0)
        if not block:
            return 0
        self._invalidate_stale_plans(block)
        gas = self.state.get("gas_gwei")
        eth = self.state.get("eth_price_usd")
        users = []
        seen = set()
        for w in (self.state.get("watchlist") or [])[:16]:
            pid = str(w.get("protocol_id") or w.get("protocol") or "aave").lower()
            if pid not in ("aave", "v3", "v4", ""):
                continue
            u = (w.get("user") or "").lower()
            if u.startswith("0x") and u not in seen:
                seen.add(u)
                users.append(u)
        for u in list(self._pending_aave_users) + list(self._contested or []):
            a = str(u or "").lower()[:42]
            if a.startswith("0x") and a not in seen:
                seen.add(a)
                users.append(a)
        n = 0
        for u in users[:16]:
            if self._cached_flash_plan(u, block) is not None:
                continue
            try:
                plan = ml.build_full_plan(
                    u, gas_gwei=gas, eth_usd=eth, allow_near=True, max_hf=1.05)
            except Exception:
                continue
            if not plan:
                continue
            plan = dict(plan)
            plan["protocol_id"] = "aave"
            plan["protocol"] = "Aave"
            plan["protocol_label"] = "Aave"
            plan["live_ok"] = True
            cover = float(plan.get("cover_usd") or plan.get("debt_usd") or 0)
            try:
                extra = elend.util.apply_flash_fee(
                    cover, float(plan.get("bonus_usd") or 0), 0.0, 0.0)
                plan["flash_fee_bps"] = extra["flash_fee_bps"]
                plan["flash_fee_usd"] = extra["flash_fee_usd"]
                plan["flash_note"] = extra["flash_note"]
                if plan.get("net_usd") is not None:
                    plan["net_usd"] = round(
                        float(plan["net_usd"]) - extra["flash_fee_usd"], 2)
                    plan["profit_usd"] = plan["net_usd"]
            except Exception:
                pass
            self._put_flash_plan(u, block, plan)
            n += 1
            self._touch_watch_hf(u, plan)
            if plan.get("liquidatable"):
                self._merge_liq_opp_from_plan(plan)
        if self.state.get("broadcast") is not None:
            self.state["broadcast"]["plans_cached"] = len(self._flash_plans)
        return n

    def _touch_watch_hf(self, user, plan):
        uk = (user or "").lower()
        for w in self.state.get("watchlist") or []:
            if (w.get("user") or "").lower() == uk:
                if plan.get("healthFactor") is not None:
                    w["hf"] = plan["healthFactor"]
                w["plan_cached"] = True
                w["liquidatable"] = bool(plan.get("liquidatable"))
                return

    def _fire_cached_liquidatable(self):
        """If a cached closest-book just became HF<1 and net ≥ floor, submit."""
        if not self.broadcast_enabled:
            return []
        liq_ok, _ = self.refresh_broadcast_ready()
        if not liq_ok:
            return []
        min_p = float((self.state.get("broadcast") or {}).get("dyn_min_liq")
                      or MIN_LIQ_PROFIT_USD)
        fired = []
        block = int(self.state.get("block") or 0)
        for uk, rec in list(self._flash_plans.items()):
            plan = rec.get("plan") or {}
            pb = int(rec.get("block") or 0)
            if block and pb and (block - pb) > 1:
                continue
            if not plan.get("liquidatable"):
                continue
            if not self._liq_live_ok(plan):
                continue
            net = plan.get("net_usd")
            if net is None or float(net) < min_p:
                continue
            try:
                user = (plan.get("user") or "")
                if not user and ":" in str(uk):
                    user = str(uk).split(":", 1)[-1]
                elif not user:
                    user = uk
                out = self._broadcast_liquidation(user, float(net), plan)
            except Exception as e:  # noqa: BLE001
                out = {"stage": "error", "reason": str(e)[:200], "user": uk}
            if out:
                fired.append(out)
                st = (out.get("stage") or "").lower()
                if st not in ("skip",):
                    self._record_broadcast("liq", out)
        return fired

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
                refresh = (time.time() - self.state["mempool"].get("last_content", 0)) > 25
                result = await self._run(self._mempool_poll, 80, refresh)
                pend, queued = result if result else (0, 0)
                s = self.state["mempool"]
                txs = s.get("txs") or []
                spoke = ic.spoke_txs(txs)
                watch = ic.watch_txs(txs, [lb.SPOKE, getattr(lb, "V3_POOL", "")])
                mv, _old_samples = ic.mev_classes(txs)
                live_mev = _build_live_mev(txs, limit=48)
                _liq_cls2 = {"liq", "spoke", "aave"}
                _liq_only = {"liq"}
                top = {}
                for t in txs:
                    cls = _classify_tx(t)
                    if cls not in _liq_only:
                        continue
                    to = (t.get("to") or "").lower()
                    if to.startswith("0x"):
                        top[to] = top.get(to, 0) + 1
                sampled_n = max(len([t for t in txs if _classify_tx(t) in _liq_only]), 1)
                # rank: MEV-relevant destinations first, then by count
                ranked = sorted(
                    top.items(),
                    key=lambda kv: (
                        0 if _mp_kind(kv[0]) in ("lending",) else 1,
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
                        "mev": kind == "lending",
                    })
                top_mev = [r for r in top_rows if r["mev"]][:8]
                # enrich spoke rows for UI
                spoke_rows = []
                for t in spoke[:30]:
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
                        "proto": t.get("proto", "other"),
                        "proto_label": t.get("proto_label", "?"),
                        "args": [str(a)[:48] for a in args[:4]],
                        "user": user,
                        "user_short": (user[:10] + "…") if user else "",
                        "hot": t.get("hot", False) or "liquidat" in (t.get("name") or "").lower(),
                    })
                contested = pe.contested_users_from_mempool(spoke)
                self._contested = contested
                if contested or any(r.get("hot") for r in spoke_rows):
                    self._kick_eth_hot()
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
                                        timeout=40, retries=1)
                txs = []
                dex = getattr(mev_bot, "DEX_ROUTERS", set())
                for bucket in (content.get("pending") or {}).values():
                    for t in bucket.values():
                        to = (t.get("to") or "").lower()
                        full = to in dex
                        rec = {
                            "hash": t.get("hash", ""),
                            "from": t.get("from", ""),
                            "to": t.get("to") or "",
                            "value": t.get("value", "0x0"),
                            "input": (t.get("input") or "")[:4096] if full
                            else (t.get("input") or "")[:10],
                            "gasPrice": t.get("gasPrice") or t.get("maxFeePerGas") or "0x0",
                            "maxPriorityFeePerGas": t.get("maxPriorityFeePerGas", "0x0"),
                            "gas": t.get("gas", "0x0"),
                        }
                        if full:
                            rec.update({
                                "nonce": t.get("nonce"),
                                "type": t.get("type"),
                                "v": t.get("v"), "r": t.get("r"), "s": t.get("s"),
                                "chainId": t.get("chainId") or "0x1",
                                "maxFeePerGas": t.get("maxFeePerGas"),
                                "accessList": t.get("accessList") or [],
                            })
                        txs.append(rec)
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
                    prev_blk = self.state["block"]
                    self.state["block"] = bg[0]
                    self.state["gas_gwei"] = round(bg[1], 1)
                    if prev_blk != bg[0]:
                        self._invalidate_stale_plans(bg[0])
                        self._kick_eth_hot()
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
                    self.hist["reserves"].setdefault(
                        rid, deque(maxlen=MAXLEN)).append([int(time.time()), v])
                self.state["prices"]["reserves"] = reserves
                self.state["prices"]["deltas"] = deltas
                if deltas:
                    self._price_moved = True
                    self.log("price", "warn",
                             f"{len(deltas)} reserve move(s) — fast sweep armed")

                if self._uni is None:
                    self._uni = await self._run(mev_bot.build_universe, 90)
                eth = 0.0
                try:
                    eth = await self._run(mev_bot.eth_price_usd, 30, self._uni) or 0.0
                except Exception:
                    pass
                if not eth:
                    try:
                        r = _req.get("https://api.binance.com/api/v3/ticker/price",
                                     params={"symbol": "ETHUSDT"},
                                     headers={"User-Agent": _UA}, timeout=8)
                        r.raise_for_status()
                        eth = float(r.json()["price"])
                    except Exception:
                        pass
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

    def _liq_submit_gate(self):
        if not ml.CONTRACT:
            return "blocked", "LIQ_CONTRACT / LIQ_GENERIC_CONTRACT unset"
        if not lb.KEYSTORE_PW:
            return "blocked", "no liq keystore"
        if self.sim_only:
            return "sim", "sim-only"
        if not self.armed:
            return "sim", "not armed"
        if self.keep_live:
            return "live", "keep-live auto-renew"
        return "live", "armed"

    def _liq_live_ok(self, o):
        """True when the executor bytecode can actually hit this venue."""
        if not o:
            return False
        pid = self._liq_pid(o)
        blocked = o.get("live_ok") is False
        reason = str(o.get("live_block_reason") or o.get("leftover") or "")
        only_generic = "GenericFlashLiquidator" in reason or "KIND()" in reason
        if blocked and not (pid in ("spark", "compound", "morpho") and only_generic):
            return False
        if pid in ("spark", "compound", "morpho"):
            if not self._generic_liq_ok():
                return False
            fp = o.get("flash_plan") or {}
            if not (o.get("liq_args") or fp.get("liq_args")):
                return False
            return not blocked or only_generic
        if pid == "aave":
            return True
        return bool(o.get("live_ok"))

    def _decorate_liq_opp(self, o):
        submit, reason = self._liq_submit_gate()
        pid = self._liq_pid(o)
        if pid in ("spark", "compound", "morpho") and not self._generic_liq_ok():
            o["live_ok"] = False
            o["live_block_reason"] = elend.executor.LIVE_BLOCK_NEED_GENERIC
            submit = "blocked"
            reason = o["live_block_reason"]
        elif not self._liq_live_ok(o):
            submit = "blocked"
            reason = (o.get("live_block_reason")
                      or "executor cannot hit this venue")
        o["submit"] = submit
        o["submit_reason"] = reason
        net = o.get("net_usd")
        if net is None:
            net = o.get("profit_usd")
        o["actionable"] = bool(
            submit in ("live", "sim") and net is not None and float(net) > 0)
        if o.get("edge"):
            pass
        elif pe.is_edge_opp(o) or (o.get("user") or "")[:10] in _EDGE:
            o["edge"] = "long-tail"
        else:
            o["edge"] = ""
        u = o.get("user") or ""
        o["etherscan"] = f"https://etherscan.io/address/{u}" if u else ""
        if not o.get("protocol_id"):
            p = str(o.get("protocol") or "aave").lower()
            if p in ("v3", "v4", "aave"):
                o["protocol_id"] = "aave"
                o["protocol"] = "Aave"
                o["protocol_label"] = "Aave"
        o.setdefault("flash_fee_src", "aave-v3")
        o.setdefault("flash_note", "Aave V3 flashLoan")
        if o.get("repay_usd") is None and o.get("cover_usd") is not None:
            o["repay_usd"] = o.get("cover_usd")
        return o

    @staticmethod
    def _hf_float(hf):
        """Ray (1e18) or already-human HF → float. None if missing."""
        try:
            n = float(hf)
        except (TypeError, ValueError):
            return None
        if n > 1e9:
            return n / 1e18
        return n

    async def sweep_loop(self):
        while True:
            try:
                self.bot("sweep", "running", "HF sweep Aave / Spark / Compound / Morpho")
                borrowers = ll.load_borrowers()
                ran = await self._run(self._sweep, 580, borrowers)
                if ran is None:
                    extra = dict(self.state.get("_liq_sweep_extra") or {})
                    extra["last_scan"] = int(time.time())
                    extra["timeout"] = True
                    prev_scanned = int(extra.get("scanned") or 0)
                    if prev_scanned:
                        extra["status"] = "partial"
                        left = list(extra.get("leftovers") or [])
                        msg = "this HF sweep timed out — showing last book"
                        if msg not in left:
                            left.insert(0, msg)
                        extra["leftovers"] = left
                    else:
                        extra["status"] = "error"
                        extra["errors"] = list(extra.get("errors") or []) + [
                            "HF sweep timed out"]
                    self.state["_liq_sweep_extra"] = extra
                    self.state["opportunities_meta"] = self._opps_meta(
                        self.state.get("opportunities") or [])
                    self.bot("sweep", "error", "HF sweep timed out")
                else:
                    opps = pe.rank_liq_opps(
                        self.state["opportunities"], edge_bias=self.edge_bias)
                    self.state["opportunities"] = opps
                    self.state["opportunities_meta"] = self._opps_meta(opps)
                    n_opps = len(opps)
                    scanned = (self.state.get("opportunities_meta") or {}).get("scanned")
                    self.bot("sweep", "ok",
                             f"{scanned if scanned is not None else len(borrowers)} scanned, "
                             f"{n_opps} liquidatable"
                             f"{' [edge-bias]' if self.edge_bias else ''}")
                    if self.broadcast_enabled and n_opps:
                        liq_ok, _ = self.refresh_broadcast_ready()
                        if liq_ok:
                            for o in opps[:5]:
                                if not self._liq_live_ok(o):
                                    continue
                                try:
                                    rec = await self._run(
                                        self._broadcast_liquidation, 240,
                                        o["user"], o.get("profit_usd"), o)
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
                extra = dict(self.state.get("_liq_sweep_extra") or {})
                extra["last_scan"] = int(time.time())
                extra["status"] = extra.get("status") or "error"
                extra["errors"] = list(extra.get("errors") or []) + [str(e)[:160]]
                self.state["_liq_sweep_extra"] = extra
                try:
                    self.state["opportunities_meta"] = self._opps_meta(
                        self.state.get("opportunities") or [])
                except Exception:
                    pass
                self.bot("sweep", "error", e)
            moved = self._price_moved
            self._price_moved = False
            peak = pe.is_peak_hour(self.state.get("intel", {}).get("hours") or {})
            await asyncio.sleep(max(25.0, pe.sweep_sleep_sec(120.0, peak, moved) *
                float((self.state.get("intel") or {}).get("brain", {})
                      .get("cadence_mult") or 1.0)))

    async def eth_hot_loop(self):
        """Closest-10 liq plans. Does not block startup."""
        while True:
            try:
                await self._run(self._poll_pending_aave, 12)
                n = await self._run(self._precompute_closest, 45)
                fired = await self._run(self._fire_cached_liquidatable, 60)
                n_fire = len(fired or [])
                cached = len(self._flash_plans)
                pend = len(self._pending_aave_users)
                if n or n_fire or cached:
                    self.bot(
                        "broadcast", "ok" if not n_fire else "running",
                        f"hot plans={cached} new={n or 0} pending={pend}"
                        f" pend_ok={self._pending_aave_ok}"
                        f"{f' fired={n_fire}' if n_fire else ''}")
            except Exception as e:
                self.bot("broadcast", "error", f"hot: {e}")
            ev = self._eth_hot_kick
            if ev is None:
                await asyncio.sleep(3)
                continue
            try:
                await asyncio.wait_for(ev.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                pass
            else:
                ev.clear()

    def _sweep(self, borrowers):
        t0 = time.time()
        blk = int(self.state.get("block") or 0)
        start = end = None
        if blk:
            last = int(self._liq_harvest_block or 0)
            start = (last + 1) if last else max(1, blk - 8000)
            end = min(blk, start + 3999)
            if start <= blk:
                self._liq_harvest_block = end
        users = list(borrowers or [])
        users.extend(self._contested or [])
        prev_watch = list(self.state.get("watchlist") or [])
        for w in prev_watch:
            u = (w.get("user") or "").lower()
            if u.startswith("0x"):
                users.append(u)
        gas = float(self.state.get("gas_gwei") or 1.0)
        eth = float(self.state.get("eth_price_usd") or 0.0)
        recent = pe.recent_competitor_users(self.state.get("competitors") or [])
        result = elend.scan_all({
            "from_block": start or 0,
            "to_block": end or 0,
            "gas_gwei": gas,
            "eth_usd": eth,
            "contested": self._contested,
            "recent_comp": recent,
            "borrowers": users,
            "prev_watch": prev_watch,
        })
        opps = []
        for o in result.get("opps") or []:
            rec = self._decorate_liq_opp(dict(o))
            opps.append(rec)
            try:
                if rec.get("user"):
                    rec.setdefault("liquidatable", True)
                    self._put_flash_plan(rec["user"], blk, rec)
            except Exception:
                pass
        self.state["watchlist"] = result.get("watch") or []
        self.state["opportunities"] = opps
        scanned = int(result.get("scanned") or 0)
        self.state["sweep_total"] = scanned
        extra = {
            "scanned": scanned,
            "skipped": result.get("skipped") or {},
            "n_logs": result.get("n_logs") or 0,
            "from_block": start,
            "to_block": end,
            "errors": result.get("errors") or [],
            "scan_ms": int((time.time() - t0) * 1000),
            "last_scan": int(time.time()),
            "last_block": blk or None,
            "leftovers": result.get("leftovers") or [],
            "adapters": result.get("adapters") or [],
            "pills": result.get("pills") or [],
            "flash_fee_bps": result.get("flash_fee_bps") or 9,
            "flash_fee_src": result.get("flash_fee_src") or "aave-v3",
            "status": result.get("status") or "ok",
        }
        self.state["_liq_sweep_extra"] = extra
        return extra

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
            hf_f = self._hf_float(o.get("hf"))
            if hf_f is not None:
                hfs.append(hf_f)
        avg_hf = round(sum(hfs) / len(hfs), 4) if hfs else None
        extra = self.state.get("_liq_sweep_extra") or {}
        skipped = extra.get("skipped") or {}
        gate, gate_reason = self._liq_submit_gate()
        actionable = sum(1 for o in opps if o.get("actionable"))
        proto_counts = {}
        for o in opps:
            k = (o.get("protocol_id") or o.get("protocol") or "aave").lower()
            if k in ("v3", "v4"):
                k = "aave"
            proto_counts[k] = proto_counts.get(k, 0) + 1
        proto_mix = [
            {"id": p, "n": c, "pct": round(100 * c / max(n, 1))}
            for p, c in sorted(proto_counts.items(), key=lambda x: -x[1])
        ]
        leftovers = list(extra.get("leftovers") or [])
        wl_n = len(wl)
        if (0 < wl_n < 50
                and not any("closest watch" in str(x) for x in leftovers)):
            leftovers.append(
                f"closest watch {wl_n}/50 unique HF accounts "
                "(not inventing users — harvest/RPC only returned these)")
        if not self._generic_liq_ok():
            msg = elend.executor.LIVE_BLOCK_NEED_GENERIC
            if msg not in leftovers:
                leftovers.insert(0, msg)
        return {
            "count": n,
            "edge_n": edge_n,
            "best_profit": round(best, 2),
            "sum_profit": round(total, 2),
            "sweep_total": self.state.get("sweep_total") or extra.get("scanned"),
            "watch_n": len(wl),
            "avg_hf": avg_hf,
            "pressure": pressure,
            "pair_mix": pair_mix,
            "last_scan": extra.get("last_scan"),
            "last_block": extra.get("last_block") or self.state.get("block"),
            "scanned": extra.get("scanned") or self.state.get("sweep_total") or 0,
            "skipped": skipped,
            "skipped_n": sum(int(v or 0) for v in skipped.values()),
            "n_logs": extra.get("n_logs") or 0,
            "from_block": extra.get("from_block"),
            "to_block": extra.get("to_block"),
            "scan_ms": extra.get("scan_ms"),
            "submit_gate": gate,
            "submit_reason": gate_reason,
            "actionable": actionable,
            "plans_cached": len(self._flash_plans),
            "pending_aave_ok": bool(self._pending_aave_ok),
            "errors": extra.get("errors") or [],
            "leftovers": leftovers,
            "adapters": extra.get("adapters") or [],
            "pills": extra.get("pills") or list(elend.PILLS),
            "flash_fee_bps": extra.get("flash_fee_bps") or 9,
            "flash_fee_src": extra.get("flash_fee_src") or "aave-v3",
            "protocol_mix": proto_mix,
            "status": extra.get("status") or (
                "error" if (extra.get("errors") and not extra.get("scanned"))
                else "ok"),
        }

    async def competitor_loop(self):
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
                # Catch up via logs: first pass ~last 1800 blocks (~6h), then incremental.
                last_scanned = self._comp_last_scanned
                lookback = 8000  # ~1.1d; empty means none in this window
                start = max(1, (last_scanned + 1) if last_scanned else blk - lookback)
                # Cap a cycle so public RPCs finish inside the timeout.
                end = min(blk, start + 1999)
                if start <= blk:
                    rec = await self._run(self._scan_liq_logs, 180, start, end, seen_tx)
                    if rec is None:
                        # Events may already be in state (thread still running).
                        self._refresh_competitor_stats()
                        self.state["competitors_meta"]["status"] = (
                            "err eth_getLogs / adapter scan timed out")
                        self.state["competitors_meta"]["last_scan"] = int(time.time())
                        self.bot("competitors", "error",
                                 "eth_getLogs timeout — retrying same window")
                        await asyncio.sleep(15)
                        continue
                    self._comp_last_scanned = end
                while len(seen_tx) > 500:
                    seen_tx.pop()
                self._refresh_competitor_stats()
                now = int(time.time())
                self.state["competitors_meta"]["last_block"] = blk
                self.state["competitors_meta"]["last_scan"] = now
                self.state["competitors_meta"]["window"] = (
                    (end - start + 1) if start else 0)
                m = self.state["competitors_meta"]
                self.hist["comp_1h"].append(int(m.get("count_1h") or 0))
                self.hist["comp_missed"].append(int(m.get("missed_by_us") or 0))
                scanned_n = m.get("n_logs")
                self.bot("competitors", "ok",
                         f"{m.get('count_1h', 0)}/1h | "
                         f"{m.get('unique_searchers', 0)} searchers | "
                         f"Aave+Spark+Compound+Morpho | "
                         f"missed={m.get('missed_by_us', 0)} | "
                         f"{m.get('pressure', 'idle')}"
                         + (f" | logs={scanned_n}" if scanned_n else "")
                         + f" | blk {start}→{end}")
            except Exception as e:
                self.bot("competitors", "error", e)
            await asyncio.sleep(15 if pe.is_peak_hour(
                self.state.get("intel", {}).get("hours") or {}) else 20)

    def _scan_liq_logs(self, start, end, seen_tx):
        """Pull liquidation event logs across ETH lending adapters."""
        extras = []
        scanned = elend.scan_all_logs(start, end, extra_addrs=extras)
        events = scanned.get("events") or []
        n_new = 0
        for ev in events:
            txh = (ev.get("tx") or "").lower()
            if not txh or txh in seen_tx:
                continue
            seen_tx.add(txh)
            n_new += 1
            if n_new > 60:
                break
            try:
                self._record_competitor_event(ev)
            except Exception as e:  # noqa: BLE001
                self.log("competitor", "info", f"record fail: {e}")
        meta = self.state["competitors_meta"]
        meta["from_block"] = scanned.get("from_block") or start
        meta["to_block"] = scanned.get("to_block") or end
        meta["n_logs"] = int(scanned.get("n_logs") or 0)
        meta["errors"] = scanned.get("errors") or []
        if scanned.get("errors") and not events:
            meta["status"] = "err " + str(scanned["errors"][0])[:80]
        else:
            meta["status"] = "ok"
        self._refresh_competitor_stats()
        return {
            "n_logs": int(scanned.get("n_logs") or 0),
            "n_events": len(events),
            "n_new": n_new,
            "from_block": start,
            "to_block": end,
        }

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
        topic0 = str((lg.get("topics") or [""])[0] or "").lower()
        addr = (lg.get("address") or "").lower()
        ev = None
        if topic0 == getattr(lb, "V3_LIQ_TOPIC", "").lower() or addr == lb.V3_POOL.lower():
            ev = lb.parse_v3_liq_log(lg)
        if not ev:
            ev = lb.parse_v4_liq_log(lg) or self._parse_liq_log(lg)
            if ev and "protocol" not in ev:
                ev["protocol"] = "v4"
                ev["coll_addr"] = ""
                ev["debt_addr"] = ""
                ev["coll_sym"] = RESERVE_SYMS.get(ev.get("coll_rid"), str(ev.get("coll_rid")))
                ev["debt_sym"] = RESERVE_SYMS.get(ev.get("debt_rid"), "?")
                ev["debt_to_cover"] = ev.get("debt_restored") or ev.get("debt_to_cover") or 0
                ev["coll_seized"] = ev.get("coll_to_liq") or ev.get("coll_seized") or 0
        if ev:
            self._record_competitor_event(ev)

    def _record_competitor_event(self, parsed):
        if not parsed:
            return
        user = (parsed.get("user") or "").lower()
        searcher = (parsed.get("searcher") or parsed.get("liquidator") or "").lower()
        gas_used = None
        gas_price = None
        status = None
        ts = None
        txh = parsed.get("tx") or ""
        try:
            rc = lb.jrpc(lb.RPC_CALL, "eth_getTransactionReceipt", [txh])
            if rc:
                gas_used = int(rc.get("gasUsed", "0x0"), 16)
                status = int(rc.get("status", "0x1"), 16)
                egp = rc.get("effectiveGasPrice") or rc.get("gasPrice")
                if egp:
                    gas_price = int(egp, 16) / 1e9
                if not searcher:
                    searcher = (rc.get("from") or "").lower()
        except Exception:
            pass
        try:
            blk = lb.jrpc(lb.RPC_CALL, "eth_getBlockByNumber",
                          [hex(int(parsed.get("block") or 0)), False])
            if blk:
                ts = int(blk.get("timestamp", "0x0"), 16)
                if gas_price is None:
                    bf = blk.get("baseFeePerGas")
                    if bf:
                        gas_price = int(bf, 16) / 1e9
        except Exception:
            pass
        coll_sym = parsed.get("coll_sym") or RESERVE_SYMS.get(
            parsed.get("coll_rid"), "?")
        debt_sym = parsed.get("debt_sym") or RESERVE_SYMS.get(
            parsed.get("debt_rid"), "?")
        leftovers = []
        est, coll_usd = None, None
        try:
            est, coll_usd = lb.liq_event_profit(parsed)
        except Exception:
            est, coll_usd = None, None
        if est is None:
            leftovers.append("bonus/oracle not decoded — no fake $")
        elif est <= 0:
            leftovers.append("reconstructed bonus ≤ 0 — not their PnL")
            est = None
        eth = self.state.get("eth_price_usd") or 0
        gas_cost_eth = None
        gas_cost_usd = None
        if gas_used and gas_price:
            gas_cost_eth = round(gas_used * gas_price * 1e-9, 6)
            if eth:
                gas_cost_usd = round(gas_cost_eth * eth, 2)
        net = None
        if est is not None and gas_cost_usd is not None:
            raw_net = round(est - gas_cost_usd, 2)
            if raw_net > 0:
                net = raw_net
            else:
                leftovers.append("our-model gas ≥ bonus — net unknown")
        elif est is not None:
            net = est
        watched = {o.get("user", "").lower() for o in self.state.get("opportunities") or []}
        watched |= {w.get("user", "").lower() for w in self.state.get("watchlist") or []}
        missed = user in watched
        edge = pe.is_edge_opp({"coll_sym": coll_sym, "debt_sym": debt_sym})
        rec = {
            "block": parsed.get("block"),
            "ts": ts or int(time.time()),
            "user": user,
            "user_short": user[:10],
            "searcher": searcher,
            "searcher_short": searcher[:10],
            "coll": parsed.get("coll_addr") or str(parsed.get("coll_rid") or "?"),
            "debt": parsed.get("debt_addr") or str(parsed.get("debt_rid") or "?"),
            "coll_sym": coll_sym,
            "debt_sym": debt_sym,
            "debt_to_cover": str(parsed.get("debt_to_cover") or 0),
            "coll_usd": coll_usd,
            "gas_price_gwei": round(gas_price, 2) if gas_price is not None else None,
            "gas_used": gas_used,
            "gas_cost_eth": gas_cost_eth,
            "gas_cost_usd": gas_cost_usd,
            "est_profit_usd": est,
            "net_est_usd": net,
            "tx": txh,
            "etherscan": f"https://etherscan.io/tx/{txh}" if txh else "",
            "spoke": (parsed.get("spoke") or "")[:12],
            "protocol": parsed.get("protocol") or "Aave",
            "protocol_id": parsed.get("protocol_id") or (
                "aave" if str(parsed.get("protocol") or "").lower() in ("v3", "v4", "aave", "")
                else str(parsed.get("protocol") or "").lower()),
            "status": status,
            "missed_by_us": missed,
            "edge": "long-tail" if edge else "",
            "leftover": leftovers,
        }
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
                 f"[{tag}] {rec.get('protocol')} blk={rec['block']} "
                 f"{coll_sym}→{debt_sym} user={user[:10]} searcher={searcher[:10]} "
                 f"gas={gas_used} est=${est} net=${rec['net_est_usd']}")

    def _refresh_competitor_stats(self):
        agg = aggregate_searcher_share(self.state.get("competitors") or [])
        self.state["competitors_meta"].update({
            "count_1h": agg["count_1h"],
            "unique_searchers": agg["unique_searchers"],
            "avg_gas": agg["avg_gas"],
            "sum_est_profit": agg["sum_est_profit"],
            "sum_net_est": agg["sum_net_est"],
            "missed_by_us": agg["missed_by_us"],
            "miss_rate_pct": agg["miss_rate_pct"],
            "edge_n": agg["edge_n"],
            "revert_n": agg["revert_n"],
            "pressure": agg["pressure"],
            "top_searchers": agg["top_searchers"],
            "pair_mix": agg["pair_mix"],
            "total": agg["total"],
            "est_n": agg.get("est_n") or 0,
            "last_hit_ts": agg.get("last_hit_ts"),
        })

    # legacy full-block scanner removed — eth_getLogs path is primary

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
                    # --- liquidation intel aggregation ---
                    eth_price = (self.state.get("eth_price_usd")
                                 or self.state.get("eth_price") or 3500.0)
                    liq_data = aggregate_liq_intel(
                        rec.get("spoke_txs") or [],
                        eth_price=eth_price,
                        competitors=self.state.get("competitors") or [],
                        watchlist=self.state.get("watchlist") or [],
                        opportunities=self.state.get("opportunities") or [],
                        competitors_meta=self.state.get("competitors_meta") or {},
                    )
                    vol_hist = self.state["intel"].get("liq_intel", {}).get("volume_history", [])
                    vol_hist.append({"ts": int(time.time()), "volume": liq_data["volume_24h"]})
                    if len(vol_hist) > 288:
                        vol_hist = vol_hist[-288:]
                    liq_data["volume_history"] = vol_hist
                    self.state["intel"]["liq_intel"] = liq_data
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
        """Priority fees + Solend landing stream (public-RPC mempool twin)."""
        while True:
            try:
                self.sol_bot("mempool", "running", "prio + Solend landing…")
                fees = await self._run(sols.fetch_priority_fees, 25)
                if not fees:
                    fees = {"ok": False, "error": "priority fee timeout"}
                sol = self.state["sol"]
                mp = sol["mempool"]
                meta = mp.setdefault("meta", {})
                pressure = "idle"
                med = None
                if fees.get("ok"):
                    med = fees.get("median")
                    p90 = fees.get("p90")
                    sol["priority_fee"] = med
                    n = fees.get("samples") or 0
                    slots_n = fees.get("slots") or 0
                    hist_bins = fees.get("histogram") or []
                    mix = fees.get("mix") or {}
                    pressure = fees.get("pressure") or "idle"
                    mp["count"] = n
                    mp["queued"] = slots_n
                    mp["method"] = "landing+prio"
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
                        "pressure": pressure,
                        "hot_share_pct": round(
                            100.0 * ((mix.get("hot") or 0) + (mix.get("elevated") or 0))
                            / max(n, 1), 1),
                        "histogram": hist_bins,
                        "rpc": fees.get("rpc"),
                        "note": "Solend landing + Jito tips · µl/CU",
                    })
                    mp["mev_txs"] = [
                        {
                            "cls": f.get("cls") or "quiet",
                            "kind": "prio",
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
                landing = await self._run(
                    sols.watch_solend_landing, 55, 32,
                    sol.get("sol_price_usd"), med, pressure)
                if not landing:
                    landing = {"ok": False, "hits": [], "opportunities": [],
                               "liq_n": 0, "mev_n": 0}
                hits = landing.get("hits") or []
                liq_hits = [h for h in hits if h.get("kind") == "liq"]
                mev_hits = [h for h in hits
                            if h.get("kind") in ("jito", "backrun")]
                mp["hits"] = hits
                mp["liq_hits"] = liq_hits
                mp["mev_hits"] = mev_hits
                meta["liq_hits"] = int(landing.get("liq_n") or len(liq_hits))
                meta["mev_hits"] = int(landing.get("mev_n") or len(mev_hits))
                meta["refresh_n"] = int(landing.get("refresh_n") or 0)
                meta["jito_bundles"] = int(
                    (landing.get("jito") or {}).get("bundles") or 0)
                meta["decoded"] = int(landing.get("decoded") or 0)
                meta["contested"] = int(landing.get("contested_n") or 0)
                meta["landing_note"] = landing.get("note") or ""
                meta["last_slot"] = landing.get("last_slot") or sol.get("slot")
                meta["last_scan"] = int(time.time())
                land_watch = landing.get("watch") or []
                if land_watch:
                    sols.remember_hydrates(land_watch)
                    sol["watchlist"] = sols.closest_to_stress(50)
                if landing.get("liq_n") or landing.get("opportunities"):
                    meta["pressure"] = "hot"
                elif landing.get("refresh_n"):
                    meta["pressure"] = "elevated"
                self.hist.setdefault(
                    "sol_mp_liq", deque(maxlen=MAXLEN)).append(
                        meta.get("liq_hits") or 0)
                self.hist.setdefault(
                    "sol_mp_mev", deque(maxlen=MAXLEN)).append(
                        meta.get("mev_hits") or 0)
                # Merge mempool-sourced liquidatable into the opp feed
                land_opps = [self._sol_decorate_liq(dict(o))
                             for o in (landing.get("opportunities") or [])]
                if land_opps:
                    prio = med
                    for o in land_opps:
                        o["plan"] = sols.build_liq_plan(
                            o, prio, meta.get("pressure"))
                    existing = sol.get("opportunities") or []
                    merged = [self._sol_decorate_liq(o) for o in
                              sols.merge_liq_opportunities(existing, land_opps)
                              if o.get("hf") is not None and o["hf"] < 1.0]
                    sol["opportunities"] = merged
                    om = sol.setdefault("opportunities_meta", {})
                    om["count"] = len(merged)
                    om["mempool_n"] = len(land_opps)
                    om["edge_n"] = sum(1 for o in merged if o.get("edge"))
                    om["best_profit"] = max(
                        (o.get("profit_usd") or 0) for o in merged) if merged else 0
                    om["sum_profit"] = round(
                        sum(o.get("profit_usd") or 0 for o in merged), 4)
                    om["watch_n"] = len(sol.get("watchlist") or [])
                    om["last_scan"] = int(time.time())
                    om["last_slot"] = landing.get("last_slot") or sol.get("slot")
                    om["last_block"] = om["last_slot"]
                    if merged:
                        om["pressure"] = "hot"
                    # sim / gated submit on best mempool liq
                    best = next(
                        (o for o in merged if o.get("actionable")), None)
                    if best:
                        self._sol_maybe_submit(
                            "liq", best, best.get("plan") or {})
                tps_s = (fees or {}).get("tps") if fees else None
                self.sol_bot(
                    "mempool", "ok" if (fees.get("ok") or landing.get("ok"))
                    else "error",
                    f"liq={meta.get('liq_hits')} mev={meta.get('mev_hits')} "
                    f"jito={meta.get('jito_bundles')} med={med}µl"
                    + (f" tps={tps_s}" if tps_s is not None else ""))
                self.log("sol-mempool", "info",
                         f"landing liq={meta.get('liq_hits')} "
                         f"refresh={meta.get('refresh_n')} "
                         f"jito={meta.get('jito_bundles')} "
                         f"{landing.get('note') or ''}")
            except Exception as e:
                self.sol_bot("mempool", "error", e)
                self.log("sol-mempool", "error", e)
            await asyncio.sleep(28)

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
                            "sponsor + bot funded — arm LIVE to send "
                            "Jupiter/Solend + Jito bundles"
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
                    sols.probe_solend_obligations, 55, data.get("market"), 40)
                if not probe:
                    probe = {
                        "ok": False, "opportunities": [], "probed": 0,
                        "hydrated": 0, "note": "obligation probe timeout",
                    }
                # Multi-protocol probe (Kamino, MarginFi, Drift)
                multi = await self._run(slend.scan_all_obligations, 60,
                                        max_accounts=40)
                if not multi:
                    multi = {"opportunities": [], "watch": [], "probed": 0,
                             "hydrated": 0, "errors": [], "adapters": []}
                ms = int((time.time() - t0) * 1000)
                sol = self.state["sol"]
                sol["sol_lending_adapters"] = multi.get("adapters") or []
                sol["sol_lending_pills"] = multi.get("pills") or []
                wl_res = data.get("reserves") or data.get("watchlist") or []
                solend_opps = [self._sol_decorate_liq(dict(o))
                               for o in (probe.get("opportunities") or [])]
                for o in solend_opps:
                    o.setdefault("protocol_id", "solend")
                    o.setdefault("protocol", "Solend")
                multi_opps = [self._sol_decorate_liq(dict(o))
                              for o in (multi.get("opportunities") or [])]
                gpa_opps = solend_opps + multi_opps
                prio = sol.get("priority_fee")
                pressure = ((sol.get("mempool") or {}).get("meta") or {}).get(
                    "pressure")
                px = sol.get("sol_price_usd")
                for o in gpa_opps:
                    o["source"] = o.get("source") or "gpa"
                    o["kind"] = "liq"
                    pid = o.get("protocol_id") or "solend"
                    if pid == "solend":
                        scored = sols.score_liq_profit(
                            o, sol_px=px, priority_median=prio, pressure=pressure)
                        o.update(scored)
                        o["plan"] = sols.build_liq_plan(o, prio, pressure)
                    else:
                        o.setdefault("actionable", o.get("hf") is not None and o["hf"] < 1.0)
                        o.setdefault("profit_usd", None)
                        o.setdefault("net_usd", None)
                        o["plan"] = {"kind": "liq", "execute": f"{pid}-jito",
                                     "protocol_id": pid, "ready": False,
                                     "note": f"{pid} plan builder not yet wired"}
                existing = [o for o in (sol.get("opportunities") or [])
                            if o.get("kind") == "liq" or o.get("hf") is not None]
                opps = [self._sol_decorate_liq(o) for o in
                        sols.merge_liq_opportunities(gpa_opps, existing)
                        if o.get("hf") is not None and o["hf"] < 1.0
                        and not sols._dust_obligation(o)]
                sols.remember_hydrates(probe.get("watch") or gpa_opps)
                wl = probe.get("watch") or sols.closest_to_stress(50)
                if not wl:
                    wl = sols.closest_to_stress(50)
                multi_wl = multi.get("watch") or []
                wl = sorted(wl + multi_wl, key=lambda w: w.get("hf") or 99)
                sol["watchlist"] = wl[:50]
                sol["opportunities"] = opps
                mix = {}
                src_mix = opps if opps else wl
                for w in src_mix:
                    pair = (
                        f"{w.get('coll_sym') or w.get('collateral_sym') or '?'}→"
                        f"{w.get('debt_sym') or '?'}"
                    )
                    mix[pair] = mix.get(pair, 0) + 1
                pair_mix = sorted(
                    [{"pair": k, "n": v, "pct": round(100 * v / max(len(src_mix), 1), 1)}
                     for k, v in mix.items()],
                    key=lambda x: -x["n"],
                )
                opp_pressure = "idle"
                if opps:
                    opp_pressure = "hot"
                elif any((w.get("hf") or 99) < 1.05 for w in wl):
                    opp_pressure = "elevated"
                elif any((w.get("hf") or 99) < 1.1 for w in wl):
                    opp_pressure = "quiet"
                best = max((o.get("profit_usd") or 0) for o in opps) if opps else 0
                edge_n = sum(1 for o in opps if o.get("edge"))
                hfs = [w.get("hf") for w in wl if w.get("hf") is not None]
                note = data.get("hf_note") or ""
                if probe.get("note"):
                    note = (note + " · " if note else "") + probe["note"]
                mp_n = sum(1 for o in opps if o.get("source") == "mempool")
                gate, gate_reason = self._sol_submit_gate("liq")
                leftovers = []
                if data.get("error") and data.get("ok"):
                    leftovers.append(str(data["error"]))
                elif data.get("error") and not data.get("ok"):
                    leftovers.append("watchlist: " + str(data["error"]))
                if probe.get("probed") == 0 and probe.get("error"):
                    leftovers.append(
                        "obligation GPA blocked on public RPC — set SOLANA_RPC")
                elif probe.get("note") and not probe.get("hydrated"):
                    leftovers.append(str(probe["note"])[:160])
                if 0 < len(wl) < 50:
                    leftovers.append(
                        f"closest watch {len(wl)}/50 Solend hydrates "
                        "(not inventing obligations — GPA/landing only returned these)")
                probe_ok = bool(probe.get("ok") or probe.get("hydrated")
                                or (probe.get("probed") or 0) > 0)
                data_ok = bool(data.get("ok"))
                if data_ok or probe_ok:
                    status = "partial" if leftovers and not opps else "ok"
                else:
                    status = "error"
                sol["opportunities_meta"] = {
                    "count": len(opps),
                    "edge_n": edge_n,
                    "best_profit": best,
                    "sum_profit": round(sum(o.get("profit_usd") or 0 for o in opps), 4),
                    "sweep_total": data.get("reserves_n") or len(wl_res),
                    "watch_n": len(wl),
                    "scanned": (probe.get("probed") or 0) + (probe.get("hydrated") or 0),
                    "avg_hf": (sum(hfs) / len(hfs) if hfs else None),
                    "pressure": opp_pressure,
                    "pair_mix": pair_mix,
                    "protocol": sols.PROTOCOL,
                    "status": status,
                    "market": data.get("market"),
                    "scan_ms": ms,
                    "hf_public": bool(probe.get("hydrated")),
                    "obligation_probed": probe.get("probed") or 0,
                    "obligation_hydrated": probe.get("hydrated") or 0,
                    "obligation_method": probe.get("method"),
                    "mempool_n": mp_n,
                    "gpa_n": len(gpa_opps),
                    "submit_gate": gate,
                    "submit_reason": gate_reason,
                    "last_scan": int(time.time()),
                    "last_slot": sol.get("slot"),
                    "last_block": sol.get("slot"),
                    "note": note,
                    "leftovers": leftovers,
                }
                sol.setdefault("prices", {})["reserves"] = {
                    w["symbol"]: {
                        "util_pct": w.get("util_pct"),
                        "supply_apy": w.get("supply_apy"),
                        "borrow_apy": w.get("borrow_apy"),
                        "ltv": w.get("ltv"),
                        "liq_thresh": w.get("liq_thresh"),
                    }
                    for w in wl_res if w.get("symbol")
                }
                fire = next((o for o in opps if o.get("actionable")), None)
                if fire:
                    self._sol_maybe_submit("liq", fire, fire.get("plan") or {})
                multi_n = sum(a.get("opps", 0) for a in (multi.get("adapters") or []))
                msg = (f"watch={len(wl)} liq={len(opps)} "
                       f"solend_gpa={probe.get('probed')} "
                       f"multi={multi_n} "
                       f"hyd={probe.get('hydrated')} "
                       f"reserves={data.get('reserves_n')} {ms}ms")
                # GPA failures are expected on public RPC — don't mark sweep error
                if data.get("error"):
                    msg += f" err={data['error']}"
                elif probe.get("probed") == 0 and probe.get("error"):
                    msg += " · obligation GPA blocked (set SOLANA_RPC)"
                self.sol_bot("sweep", "ok" if status != "error" else "error", msg)
                self.log("sol-sweep", "info" if status != "error" else "warn", msg)
            except Exception as e:
                sol = self.state.get("sol") or {}
                om = sol.setdefault("opportunities_meta", {})
                om["last_scan"] = int(time.time())
                om["status"] = "error"
                om["errors"] = [str(e)[:160]]
                self.sol_bot("sweep", "error", e)
                self.log("sol-sweep", "error", e)
            await asyncio.sleep(90)

    async def sol_competitor_loop(self):
        while True:
            try:
                self.sol_bot("competitors", "running",
                             "Solend main-market liquidate sigs…")
                px = (self.state.get("sol") or {}).get("sol_price_usd")
                decoded = await self._run(
                    sols.decode_solend_competitors, 100, 24, px)
                sol = self.state["sol"]
                prev_meta = sol.get("competitors_meta") or {}
                now = int(time.time())
                if decoded is None:
                    agg = aggregate_searcher_share(
                        sol.get("competitors") or [], now,
                        last_slot=prev_meta.get("last_slot"), top_n=8)
                    sol["competitors_meta"] = {
                        **prev_meta, **agg,
                        "status": "err getTransaction / competitor scan timed out",
                        "last_scan": now,
                    }
                    self.sol_bot("competitors", "error",
                                 "Solend competitor scan timeout")
                    await asyncio.sleep(30)
                    continue
                if not decoded.get("ok"):
                    err = decoded.get("error") or "Solend competitor RPC failed"
                    agg = aggregate_searcher_share(
                        sol.get("competitors") or [], now,
                        last_slot=prev_meta.get("last_slot"), top_n=8)
                    sol["competitors_meta"] = {
                        **prev_meta, **agg,
                        "status": f"err {err}"[:160],
                        "last_scan": now,
                        "errors": [err],
                    }
                    self.sol_bot("competitors", "error", err)
                    await asyncio.sleep(45)
                    continue

                watched = {
                    (o.get("user") or o.get("obligation") or "")
                    for o in (sol.get("opportunities") or [])
                }
                watched |= {
                    (w.get("user") or w.get("obligation") or "")
                    for w in (sol.get("watchlist") or [])
                }
                watched.discard("")

                by_sig = {}
                for c in (sol.get("competitors") or []):
                    key = c.get("sig") or c.get("tx")
                    if key:
                        by_sig[key] = c
                for r in decoded.get("rows") or []:
                    sig = r.get("sig") or r.get("tx") or ""
                    if not sig:
                        continue
                    user = r.get("user") or ""
                    missed = bool(user and user in watched)
                    edge = pe.sol_is_edge_opp({
                        "coll_sym": r.get("coll_sym"),
                        "debt_sym": r.get("debt_sym"),
                        "edge": False,
                    })
                    leftover = list(r.get("leftover") or [])
                    flags = r.get("flags") or "liq"
                    if leftover:
                        flags = (flags + " leftover").strip()
                    est = _honest_usd(r.get("est"))
                    net = _honest_usd(r.get("net"))
                    by_sig[sig] = {
                        "age": r.get("slot"),
                        "ts": r.get("ts") or now,
                        "pair": r.get("pair") or "solend-liq",
                        "coll_sym": r.get("coll_sym"),
                        "debt_sym": r.get("debt_sym"),
                        "searcher": r.get("searcher") or r.get("liquidator") or "",
                        "user": user,
                        "gas_usd": r.get("gas_usd"),
                        "est": est,
                        "est_profit_usd": est,
                        "net": net,
                        "net_est_usd": net,
                        "flags": flags,
                        "tx": sig,
                        "sig": sig,
                        "solscan": r.get("solscan") or f"https://solscan.io/tx/{sig}",
                        "slot": r.get("slot"),
                        "missed": missed,
                        "missed_by_us": missed,
                        "edge": edge,
                        "leftover": leftover,
                        "protocol": "Solend",
                        "protocol_id": "solend",
                        "ix": r.get("ix"),
                    }
                # Multi-protocol competitor scan (Kamino, MarginFi, Drift)
                try:
                    multi_comp = await self._run(
                        slend.scan_all_competitors, 60,
                        limit=12, sol_px=px or 0.0)
                    for ev in (multi_comp or {}).get("events") or []:
                        sig = ev.get("sig") or ""
                        if sig and sig not in by_sig:
                            by_sig[sig] = {
                                "age": ev.get("slot"),
                                "ts": ev.get("slot") or now,
                                "pair": f"{ev.get('protocol_id', '?')}-liq",
                                "searcher": "",
                                "user": "",
                                "tx": sig, "sig": sig,
                                "solscan": f"https://solscan.io/tx/{sig}",
                                "slot": ev.get("slot"),
                                "missed": False, "missed_by_us": False,
                                "protocol": ev.get("protocol", ""),
                                "protocol_id": ev.get("protocol_id", ""),
                                "flags": "liq",
                            }
                except Exception:
                    pass
                comps = sorted(
                    by_sig.values(),
                    key=lambda c: -(int(c.get("slot") or 0)),
                )[:120]
                sol["competitors"] = comps

                last_slot = decoded.get("last_slot") or prev_meta.get("last_slot")
                agg = aggregate_searcher_share(
                    comps, now, last_slot=last_slot, top_n=8)
                leftovers = list(decoded.get("leftovers") or [])
                scanned_n = int(decoded.get("scanned") or 0)
                decoded_n = int(decoded.get("decoded") or 0)
                new_n = int(decoded.get("liq_n") or 0)
                n = agg["count_1h"]
                sol["competitors_meta"] = {
                    "spokes": 1, "assets": 0,
                    "status": "ok",
                    "count_1h": n,
                    "unique_searchers": agg["unique_searchers"],
                    "avg_gas": None,
                    "sum_est_profit": agg["sum_est_profit"],
                    "sum_net_est": agg["sum_net_est"],
                    "missed_by_us": agg["missed_by_us"],
                    "miss_rate_pct": agg["miss_rate_pct"],
                    "edge_n": agg["edge_n"],
                    "revert_n": agg["revert_n"],
                    "pressure": agg["pressure"],
                    "top_searchers": agg["top_searchers"],
                    "pair_mix": agg["pair_mix"],
                    "last_slot": last_slot,
                    "last_block": last_slot,
                    "last_scan": now,
                    "scanned": scanned_n,
                    "decoded": decoded_n,
                    "total": len(comps),
                    "liq_labeled": new_n,
                    "window": scanned_n,
                    "n_logs": new_n,
                    "est_n": agg.get("est_n") or 0,
                    "last_hit_ts": agg.get("last_hit_ts"),
                    "errors": [],
                    "leftovers": leftovers,
                    "market": decoded.get("market"),
                    "note": decoded.get("note") or (
                        f"{new_n} new Solend liqs · {decoded_n} decoded"
                        + (f" · slot {last_slot}" if last_slot else "")
                    ),
                }
                self.hist["sol_comp_1h"].append(n)
                self.sol_bot(
                    "competitors", "ok",
                    f"{n}/1h · {len(comps)} tracked · +{new_n} this scan · "
                    f"decoded {decoded_n}/{scanned_n}"
                    + (f" · slot {last_slot}" if last_slot else ""),
                )
                self.log("sol-comp", "info",
                         f"new={new_n} tracked={len(comps)} 1h={n} "
                         f"decoded={decoded_n} scanned={scanned_n}")
            except Exception as e:
                self.sol_bot("competitors", "error", e)
                self.log("sol-comp", "error", e)
                try:
                    meta = (self.state.get("sol") or {}).get("competitors_meta")
                    if isinstance(meta, dict):
                        meta["status"] = f"err {e}"[:160]
                        meta["last_scan"] = int(time.time())
                except Exception:
                    pass
            await asyncio.sleep(45)

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
                if sol.get("opportunities") or sol.get("watchlist"):
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
                # --- liquidation intel aggregation (SOL) ---
                sol_spoke_rows = mp.get("spoke_txs") or mp.get("landing") or []
                sol_liq_data = aggregate_liq_intel(
                    sol_spoke_rows,
                    eth_price=0.0,
                    competitors=sol.get("competitors") or [],
                    watchlist=sol.get("watchlist") or [],
                    opportunities=sol.get("opportunities") or [],
                    competitors_meta=sol.get("competitors_meta") or {},
                )
                sol_vol_hist = sol.get("intel", {}).get("liq_intel", {}).get("volume_history", [])
                sol_vol_hist.append({"ts": int(time.time()), "volume": sol_liq_data["volume_24h"]})
                if len(sol_vol_hist) > 288:
                    sol_vol_hist = sol_vol_hist[-288:]
                sol_liq_data["volume_history"] = sol_vol_hist
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
                    "exp_net": ((sol.get("opportunities_meta") or {})
                                .get("best_profit")),
                    "steps": int((sol.get("intel") or {}).get("steps") or 0) + 1,
                    "brain": {
                        "advice": (
                            f"liq={len(sol.get('opportunities') or [])} "
                            f"mempool={(sol.get('mempool') or {}).get('meta', {}).get('liq_hits', 0)}"
                        ),
                        "min_liq_mult": 1.0,
                        "prefer_edge": self.sol_edge_bias,
                        "protocol": sols.PROTOCOL,
                    },
                    "liq_intel": sol_liq_data,
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
    def _get_hot_eth_positions(self):
        watchlist = self.state.get("watchlist", [])
        return [w for w in watchlist if w.get("hf", 999) < 1.05]

    def _get_hot_sol_obligations(self):
        sol = self.state.get("sol", {})
        return sol.get("watchlist", [])

    async def start_loops(self):
        self._loop = asyncio.get_running_loop()
        self._eth_hot_kick = asyncio.Event()
        self._enforce_keep_live()
        self.refresh_broadcast_ready()
        self.refresh_sol_broadcast_ready()
        _pre_eth.start_block_listener(_RPC_POOL, self._get_hot_eth_positions)
        _pre_sol.start_slot_listener(
            "https://api.mainnet-beta.solana.com",
            self._get_hot_sol_obligations)
        coros = (self.mempool_loop(), self.eth_hot_loop(), self.prices_loop(),
                 self.funds_loop(),
                 self.sweep_loop(), self.competitor_loop(),
                 self.intel_loop(),
                 self.keep_live_loop(),
                 self.sol_net_loop(), self.sol_mempool_loop(),
                 self.sol_funds_loop(), self.sol_sweep_loop(),
                 self.sol_competitor_loop(),
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
            "liq": len(s.get("opportunities") or []),
            "liq_watch": len(s.get("watchlist") or []),
            "liq_last_scan": (s.get("opportunities_meta") or {}).get("last_scan"),
            "liq_last_block": (s.get("opportunities_meta") or {}).get("last_block"),
            "liq_scanned": (s.get("opportunities_meta") or {}).get("scanned"),
            "competitors_1h": (s.get("competitors_meta") or {}).get("count_1h"),
            "competitors_total": len(s.get("competitors") or []),
            "comp_last_block": (s.get("competitors_meta") or {}).get("last_block"),
            "comp_last_scan": (s.get("competitors_meta") or {}).get("last_scan"),
            "mempool_count": (s.get("mempool") or {}).get("count"),
            "intel_records": (s.get("intel") or {}).get("records"),
            "bots": fresh,
            "rpc_pool": list(_RPC_POOL),
            "broadcast_ready": (s.get("broadcast") or {}).get("ready"),
        }
        return aiohttp.web.json_response(body, status=200 if body["ok"] else 503)

    async def control_api(self, request):
        """Arm / sim-only / Keep Live / edge-bias toggles for live profit mode.
        POST JSON: {"armed": bool, "sim_only": bool, "keep_live": bool,
                    "edge_bias": bool, "arm_minutes": int, "chain": "eth"|"sol"}
        Arm once auto-enables Keep Live (process-lifetime + persist).
        Sim ON is the panic switch: Keep Live off, no sends.
        """
        try:
            body = await request.json()
        except Exception:
            body = {}
        chain = (body.get("chain") or "eth").lower()
        if chain == "sol":
            if body.get("refresh_funds"):
                try:
                    funds = await self._run(
                        sols.fetch_wallet_balances, 20, sols.current_wallets())
                    if funds:
                        self.state["sol"]["funds"] = funds
                        self.state["sol"]["fund_guide"] = sols.fund_guide()
                    return aiohttp.web.json_response({
                        "ok": True,
                        "funds": self.state["sol"].get("funds") or {},
                        "fund_guide": self.state["sol"].get("fund_guide") or {},
                    })
                except Exception as e:  # noqa: BLE001
                    return aiohttp.web.json_response(
                        {"ok": False, "error": str(e)[:160]}, status=500)
            persist = False
            if "sim_only" in body:
                self.sol_sim_only = bool(body["sim_only"])
                if self.sol_sim_only:
                    self.sol_keep_live = False
                    self.sol_armed = False
                    self._cancel_sol_disarm()
                    persist = True
            if "keep_live" in body:
                want = bool(body["keep_live"])
                self.sol_keep_live = want
                persist = True
                if want:
                    self.sol_sim_only = False
                    if self._sol_keep_live_ready():
                        self.sol_armed = True
                    self._cancel_sol_disarm()
                    self._sol_arm_until = None
                elif self.sol_armed:
                    mins = int(body.get("arm_minutes") or ARM_MINUTES_DEFAULT)
                    self._schedule_sol_disarm(mins)
            if "edge_bias" in body:
                self.sol_edge_bias = bool(body["edge_bias"])
            if "armed" in body:
                want_arm = bool(body["armed"])
                if want_arm:
                    self.sol_armed = True
                    self.sol_sim_only = False
                    self.sol_keep_live = True
                    persist = True
                    self._cancel_sol_disarm()
                    self._sol_arm_until = None
                else:
                    self.sol_armed = False
                    self.sol_keep_live = False
                    persist = True
                    self._cancel_sol_disarm()
                    self._sol_arm_until = None
            if persist:
                self._persist_keep_live()
            self.refresh_sol_broadcast_ready()
            self.log("sol-broadcast", "info",
                     f"control armed={self.sol_armed} keep_live={self.sol_keep_live} "
                     f"sim_only={self.sol_sim_only} edge_bias={self.sol_edge_bias}")
            return aiohttp.web.json_response(self.state["sol"]["broadcast"])

        persist = False
        if "sim_only" in body:
            self.sim_only = bool(body["sim_only"])
            if self.sim_only:
                self.keep_live = False
                self.armed = False
                self._cancel_eth_disarm()
                persist = True
        if "keep_live" in body:
            want = bool(body["keep_live"])
            self.keep_live = want
            persist = True
            if want:
                self.sim_only = False
                if self._eth_keep_live_ready():
                    self.armed = True
                self._cancel_eth_disarm()
                self._arm_until = None
            elif self.armed:
                mins = int(body.get("arm_minutes") or ARM_MINUTES_DEFAULT)
                self._schedule_eth_disarm(mins)
        if "edge_bias" in body:
            self.edge_bias = bool(body["edge_bias"])
        if "armed" in body:
            want_arm = bool(body["armed"])
            if want_arm:
                self.armed = True
                self.sim_only = False
                self.keep_live = True
                persist = True
                self._cancel_eth_disarm()
                self._arm_until = None
            else:
                self.armed = False
                self.keep_live = False
                persist = True
                self._cancel_eth_disarm()
                self._arm_until = None
        if persist:
            self._persist_keep_live()
        self.refresh_broadcast_ready()
        self.log("broadcast", "info",
                 f"control armed={self.armed} keep_live={self.keep_live} "
                 f"sim_only={self.sim_only} edge_bias={self.edge_bias}")
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

    async def sol_blockhash_api(self, request):
        """Recent blockhash for the SOL Funds card (browser wallet transfer)."""
        try:
            data = await self._run(sols.latest_blockhash, 8)
            if not data:
                return aiohttp.web.json_response(
                    {"ok": False, "error": "timeout"}, status=504)
            return aiohttp.web.json_response(data)
        except Exception as e:  # noqa: BLE001
            return aiohttp.web.json_response(
                {"ok": False, "error": str(e)[:160]}, status=502)

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

    async def paper_candle_loop(self):
        """Feed 5m candles to paper traders every 15 seconds."""
        while True:
            await asyncio.sleep(15)
            for asset, symbol, trader in [
                ("ETH", "ETHUSDT", self._paper_eth),
                ("SOL", "SOLUSDT", self._paper_sol),
            ]:
                if not trader.enabled:
                    continue
                try:
                    data = await asyncio.wait_for(
                        asyncio.to_thread(_fetch_klines, symbol, "5m", 5),
                        timeout=10)
                    if data:
                        for candle in data:
                            trader.on_candle(candle)
                except Exception:
                    pass

    async def paper_control(self, request):
        """Handle paper bot control: range mode toggle, enable/disable."""
        try:
            body = await request.json()
        except Exception:
            return aiohttp.web.json_response({"error": "bad json"}, status=400)
        asset = (body.get("asset") or "").upper()
        trader = self._paper_eth if asset == "ETH" else self._paper_sol if asset == "SOL" else None
        if not trader:
            return aiohttp.web.json_response({"error": "bad asset"}, status=400)
        if "range_mode" in body:
            trader.set_range_mode(body["range_mode"])
        if "enabled" in body:
            trader.enabled = bool(body["enabled"])
        return aiohttp.web.json_response(trader.state_dict())

    async def hybrid_toggle(self, request):
        """Toggle hybrid execution on/off."""
        try:
            body = await request.json()
        except Exception:
            return aiohttp.web.json_response({"error": "bad json"}, status=400)
        enabled = bool(body.get("enabled", False))
        self.hybrid_enabled = enabled
        if self.hybrid_executor:
            self.hybrid_executor.enabled = enabled
        self.log("hybrid", "info", f"hybrid execution {'enabled' if enabled else 'disabled'}")
        return aiohttp.web.json_response({"enabled": enabled})


_EDGE = {
    "0xcdd342b2", "0x5476fbb7", "0x0e0744fe", "0xbbcbec75", "0x07cb1f8f",
}


def main():
    ap = argparse.ArgumentParser(description="Aave V4 / multi-protocol liquidation dashboard")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--broadcast", dest="broadcast", action="store_true",
                    default=True,
                    help="submit liquidation bundles (default)")
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
    app.router.add_get("/api/sol/blockhash", dash.sol_blockhash_api)
    app.router.add_get("/ws", dash.ws_handler)
    app.router.add_post("/api/paper/control", dash.paper_control)
    app.router.add_post("/api/hybrid/toggle", dash.hybrid_toggle)

    async def startup(app_):
        await dash.start_loops()
        asyncio.create_task(dash.ticker())
        asyncio.create_task(dash.paper_candle_loop())
        mode = "BROADCAST ON" if dash.broadcast_enabled else "monitor-only"
        print(f"[dash] listening on http://{args.host}:{args.port} "
              f"[{mode} sim_only={dash.sim_only} armed={dash.armed} "
              f"keep_live={dash.keep_live}] "
              f"liq={ml.CONTRACT or '-'} generic={GENERIC_LIQ or '-'}",
              flush=True)
        ready = dash.state["broadcast"]["ready"]
        if ready["reasons"]:
            print(f"[dash] broadcast blockers: {'; '.join(ready['reasons'])}",
                  flush=True)

    app.on_startup.append(startup)
    aiohttp.web.run_app(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
