#!/usr/bin/env python3
"""Aave V3 Pool + V4 Spoke helpers for the TONI dashboard.

Real eth_call / eth_getLogs against public RPCs (dashboard overwrites jrpc +
RPC_CALL with an IPv4-healthy pool). Sim-only: no keystores, no submit.
"""
from __future__ import annotations

import json
import logging
import os
import random
import time
from concurrent.futures import ThreadPoolExecutor

import requests

log = logging.getLogger("liquidation_bot")

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# Aave V4 default WETH spoke (hub-discovered extras are merged by dashboard).
SPOKE = "0x94e7A5dCbE816e498b89aB752661904E2F56c485"
HUB = "0xCca852Bc40e560adC3b1Cc58CA5b55638ce826c9"
# Aave V3 Ethereum Pool — already labeled in dashboard.py
V3_POOL = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
# AaveOracle (USD, 8 decimals)
V3_ORACLE = "0x54586bE62E3c3580375aE3723C145253060Ca68C"

HEALTH_THRESHOLD = 10**18
DUST_USD = 25.0
MIN_NET_USD = 0.01
GAS_UNITS_FLASH = 550_000
DEFAULT_BONUS = 0.05
PROTOCOL_FEE = 0.10
CLOSE_FACTOR_HF = 0.95  # 100% close if HF below this

KEYSTORE_PATH = os.environ.get("KEYSTORE_PATH", "")
KEYSTORE_PW = os.environ.get("KEYSTORE_PW", "")

SEL = {
    "getReserveCount": "0x99806546",
    "getReservePrice": "0xd45c35ff",
    "getUserAccountData": "0xbf92857c",  # V3 Pool (+ same 4byte on some spokes)
    "getReservesList": "0xd1946dbc",
    "getUserConfiguration": "0x4417a583",
    "getAssetPrice": "0xb3596f07",
    "decimals": "0x313ce567",
    "symbol": "0x95d89b41",
}

# Aave V3 LiquidationCall(address,address,address,uint256,uint256,address,bool)
V3_LIQ_TOPIC = "0xe413a321e8681d831f4dbccbca790d2952b56f977908e45be37335533e005286"
# Aave V3 Borrow(address,address,address,uint256,uint8,uint256,uint16)
V3_BORROW_TOPIC = "0xb3d084820fb1a9decffb176436bd02558d15fac9b0ddfed8c465bc7359d7dce0"
# Supply(address,address,address,uint256,uint16) — topics[2]=onBehalfOf
V3_SUPPLY_TOPIC = "0x2b627736bca15cd5381dcf80b0bf11fd197d01a037c52b927a881a10fb73ba61"
# Withdraw(address,address,address,uint256) — topics[2]=user
V3_WITHDRAW_TOPIC = "0x3115d1449a7b732c986cba18244e897a450f61e1bb8d589cd2e69e6c8924f9f7"
# Repay(address,address,address,uint256,bool) — topics[2]=user
V3_REPAY_TOPIC = "0xa534c8dbe71f871f9f3530e97a74601fea17b426cae02e1c5aee42c96c784051"
# Dashboard-era V4 Spoke.LiquidationCall (keep; empty is honest if unused)
V4_LIQ_TOPIC = "0x2a1f12d996f530f89d8038aa293f9fde81cac44b6dfd6225e3358d09b78a4a37"
# (topic0, indexed user word) — harvest unique borrowers, not just Borrow/Liq
V3_USER_LOG_TOPICS = (
    (V3_SUPPLY_TOPIC, 2),
    (V3_BORROW_TOPIC, 2),
    (V3_WITHDRAW_TOPIC, 2),
    (V3_REPAY_TOPIC, 2),
    (V3_LIQ_TOPIC, 3),
)
CLOSEST_N = 50
CLOSEST_SWEEP = 50

RPC_CALL = [
    "https://ethereum-rpc.publicnode.com",
    "https://ethereum.publicnode.com",
    "https://rpc.flashbots.net",
    "https://eth.drpc.org",
    "https://1rpc.io/eth",
    "https://eth-mainnet.public.blastapi.io",
    "https://rpc.mevblocker.io",
]
RPC_LOG = list(RPC_CALL)

_RPC_CIRCUIT = {}  # url -> (fail_count, open_until_ts)

def _rpc_is_open(url):
    state = _RPC_CIRCUIT.get(url)
    if not state:
        return True
    fails, open_until = state
    if time.time() > open_until:
        return True
    return False

def _rpc_record_failure(url):
    state = _RPC_CIRCUIT.get(url, (0, 0))
    fails = state[0] + 1
    cooldown = min(60 * (2 ** min(fails - 3, 4)), 600) if fails >= 3 else 0
    _RPC_CIRCUIT[url] = (fails, time.time() + cooldown if cooldown else 0)

def _rpc_record_success(url):
    _RPC_CIRCUIT.pop(url, None)

TOKEN_SYMS = {
    "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": "WETH",
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": "USDC",
    "0xdac17f958d2ee523a2206206994597c13d831ec7": "USDT",
    "0x6b175474e89094c44da98b954eedeac495271d0f": "DAI",
    "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599": "WBTC",
    "0x7f39c581f595b53c5cb19bd0b3f8da6c935e2ca0": "wstETH",
    "0xae78736cd615f374b308ea6a797d5a45726a2565": "rETH",
    "0xbe9895146f7af43049ca1c1ae358b0541ea49704": "cbETH",
    "0xcd5fe23c85820f7b72d0926fc9b05b43e359b7ee": "weETH",
    "0xbf5495efe5db9ce00f80364c8b423567e58d2110": "ezETH",
    "0x40d16fc0246ad3160ccc4b2c58399c3456b3c28b": "GHO",
    "0x514910771af9ca656af840dff83e8264ecf986ca": "LINK",
    "0x7fc66500c84a76ad7e9c93437bfc5ac33e2ddae9": "AAVE",
    "0x853d955acef822db058eb8505911ed77f175b99e": "FRAX",
    "0x5f98805a4e8be255a32880fdec7f6728c6568ba0": "LUSD",
    "0xf939e0a03fb07f5956450c4963a1c95e71d1626": "crvUSD",
    "0x4c9edd5852cd905f086c759e8383e09bff1e68b3": "USDe",
    "0x83f20f44975d03b1b09e64809b757c47f942beea": "sDAI",
    "0xa35b1b31ce002fbf2058d22f30f95d405200a15b": "ETHx",
    "0xf1c9acdc66974dfb6decb12aa385b9cd01190e38": "osETH",
    "0x9d39a5de30e57443bff2a8307a4256c8797a4f84": "sUSDe",
    "0x18084fba666a33d37592fa2633fd49a74de5": "tBTC",
    "0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf": "cbBTC",
}
TOKEN_DEC = {
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": 6,
    "0xdac17f958d2ee523a2206206994597c13d831ec7": 6,
    "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599": 8,
    "0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf": 8,
}
STABLES = {
    "USDC", "USDT", "DAI", "GHO", "FRAX", "LUSD", "crvUSD", "USDe", "sDAI", "sUSDe",
}

_CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "borrowers.json")
_reserves_cache = {"ts": 0, "addrs": []}
_price_cache = {}
_dec_cache = dict(TOKEN_DEC)


def rpc(url, method, params, timeout=10):
    r = requests.post(
        url,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        headers={"Content-Type": "application/json", "User-Agent": _UA},
        timeout=timeout,
    )
    r.raise_for_status()
    out = r.json()
    if "error" in out:
        raise RuntimeError(f"RPC error {method}: {out['error']}")
    return out["result"]


def jrpc(urls, method, params):
    last = None
    for attempt, url in enumerate(urls or []):
        if not _rpc_is_open(url):
            continue
        u = url
        try:
            result = rpc(u, method, params)
            _rpc_record_success(u)
            return result
        except Exception as e:  # noqa: BLE001
            last = e
            log.debug("RPC %s failed (%s), trying next url", u, e)
            _rpc_record_failure(u)
            time.sleep(0.05 * (2 ** min(attempt, 4)) + random.uniform(0, 0.05))
    log.error("All RPCs failed for %s: %s", method, last)
    raise RuntimeError(f"all RPCs failed for {method}: {last}")


def call(urls, to, data):
    return jrpc(urls, "eth_call", [{"to": to, "data": data}, "latest"])


def call32(to, selector):
    raw = call(RPC_CALL, to, selector if str(selector).startswith("0x")
               else "0x" + selector)
    return int(raw, 16)


def _pad_addr(user: str) -> str:
    a = (user or "").lower().replace("0x", "")
    return "0x" + a.rjust(64, "0")[-64:]


def _words(raw) -> list[str]:
    hx = (raw or "0x")[2:]
    return [hx[i:i + 64] for i in range(0, len(hx), 64) if len(hx[i:i + 64]) == 64]


def _addr_from_word(w: str) -> str:
    if not w:
        return ""
    return ("0x" + w[-40:]).lower()


def _decode_addr_array(raw) -> list[str]:
    words = _words(raw)
    if len(words) < 2:
        return []
    try:
        off = int(words[0], 16) // 32
    except ValueError:
        return []
    if off >= len(words):
        off = 1
    n = int(words[off], 16)
    out = []
    for i in range(min(n, 80)):
        idx = off + 1 + i
        if idx >= len(words):
            break
        a = _addr_from_word(words[idx])
        if int(a, 16) != 0:
            out.append(a)
    return out


def token_sym(addr: str) -> str:
    a = (addr or "").lower()
    if a in TOKEN_SYMS:
        return TOKEN_SYMS[a]
    if a and a.startswith("0x"):
        return a[2:8].upper()
    return "?"


def token_decimals(addr: str) -> int:
    a = (addr or "").lower()
    if a in _dec_cache:
        return _dec_cache[a]
    try:
        raw = call(RPC_CALL, a, SEL["decimals"])
        n = int(raw, 16)
        if 0 < n <= 36:
            _dec_cache[a] = n
            return n
    except (KeyError, TypeError, ValueError):
        pass
    _dec_cache[a] = 18
    return 18


def asset_price_usd(addr: str) -> float:
    """Aave oracle USD, 8 decimals. Stables fall back to 1.0."""
    a = (addr or "").lower()
    now = time.time()
    hit = _price_cache.get(a)
    if hit and now - hit[0] < 20:
        return hit[1]
    px = 0.0
    try:
        data = SEL["getAssetPrice"] + a.replace("0x", "").rjust(64, "0")
        raw = call(RPC_CALL, V3_ORACLE, data)
        n = int(raw, 16)
        if n > 0:
            px = n / 1e8
    except (requests.RequestException, KeyError, ValueError):
        px = 0.0
    if px <= 0 and token_sym(a) in STABLES:
        px = 1.0
    _price_cache[a] = (now, px)
    return px


def amount_usd(addr: str, amount: int) -> float:
    if not amount:
        return 0.0
    dec = token_decimals(addr)
    px = asset_price_usd(addr)
    if px <= 0:
        return 0.0
    return (int(amount) / (10 ** dec)) * px


def get_reserve_price(rid: int) -> float:
    data = SEL["getReservePrice"] + hex(int(rid))[2:].rjust(64, "0")
    try:
        raw = call(RPC_CALL, SPOKE, data)
        n = int(raw, 16)
    except (requests.RequestException, KeyError, ValueError):
        return 0.0
    if n <= 0:
        return 0.0
    if n > 10**12:
        return n / 1e8
    return float(n)


def get_reserves_list() -> list[str]:
    now = time.time()
    if _reserves_cache["addrs"] and now - _reserves_cache["ts"] < 600:
        return list(_reserves_cache["addrs"])
    try:
        raw = call(RPC_CALL, V3_POOL, SEL["getReservesList"])
        addrs = _decode_addr_array(raw)
    except (requests.RequestException, KeyError, ValueError):
        addrs = []
    if addrs:
        _reserves_cache["ts"] = now
        _reserves_cache["addrs"] = addrs
    return list(addrs)


_PREFERRED_COLL = ("wstETH", "weETH", "cbBTC", "WETH", "WBTC", "rETH", "cbETH")
_PREFERRED_DEBT = ("USDC", "USDT", "DAI", "GHO", "WETH", "USDe", "FRAX")


def _user_assets_from_config(user: str) -> tuple[list[str], list[str]]:
    """Collateral / debt reserve addresses from V3 user configuration bitmap."""
    try:
        raw = call(RPC_CALL, V3_POOL, SEL["getUserConfiguration"] + _pad_addr(user)[2:])
    except (requests.RequestException, KeyError, ValueError):
        return [], []
    words = _words(raw)
    if not words:
        return [], []
    if len(words) >= 2 and int(words[0], 16) == 32:
        data = int(words[1], 16)
    else:
        data = int(words[0], 16)
    reserves = get_reserves_list()
    if not reserves or not data:
        return [], []
    colls, debts = [], []
    for i, asset in enumerate(reserves):
        if (data >> (2 * i)) & 1:
            colls.append(asset)
        if (data >> (2 * i + 1)) & 1:
            debts.append(asset)
    return colls, debts


def _pick_preferred(addrs: list[str], preferred: tuple[str, ...]) -> str:
    by_sym = {token_sym(a).upper(): a for a in addrs}
    for p in preferred:
        a = by_sym.get(p.upper())
        if a:
            return a
    return addrs[0] if addrs else ""


def user_liq_pair(user: str) -> dict:
    """Best collateral (seize) + debt (cover) addresses for a flash plan."""
    colls, debts = _user_assets_from_config(user)
    coll = _pick_preferred(colls, _PREFERRED_COLL)
    debt = _pick_preferred(debts, _PREFERRED_DEBT)
    return {
        "coll_addr": coll,
        "debt_addr": debt,
        "coll_sym": token_sym(coll) if coll else "?",
        "debt_sym": token_sym(debt) if debt else "?",
    }


def _user_pair_from_config(user: str) -> tuple[str, str]:
    """Best-effort coll/debt symbols via V3 user configuration bitmap."""
    pair = user_liq_pair(user)
    return pair.get("coll_sym") or "?", pair.get("debt_sym") or "?"


def _parse_account_raw(raw, protocol: str) -> dict | None:
    words = [int(w, 16) for w in _words(raw)]
    while len(words) < 6:
        words.append(0)
    # V3 layout: collBase, debtBase, avail, liqThresh, ltv, healthFactor
    hf_v3 = words[5]
    coll_v3 = words[0]
    debt_v3 = words[1]
    v3_like = (1e15 < hf_v3 < 1e23) or (hf_v3 == 0 and debt_v3 > 0) or (
        hf_v3 > (1 << 200) and debt_v3 == 0)
    if protocol == "v3" or (protocol != "v4-layout" and v3_like and coll_v3 < (1 << 90)):
        hf = hf_v3 if hf_v3 else ((1 << 256) - 1 if not debt_v3 else 0)
        return {
            "protocol": "v3" if protocol == "v3" else protocol,
            "hf": hf,
            "coll_base": coll_v3,
            "debt_base": debt_v3,
            "liq_thresh": words[3],
            "ltv": words[4],
            "coll_usd": coll_v3 / 1e8,
            "debt_usd": debt_v3 / 1e8,
            "layout": "v3",
        }
    # V4 stub layout: riskPremium, avgCF, healthFactor, collValue (USD*1e26)
    hf = words[2] if words[2] else ((1 << 256) - 1)
    coll = words[3]
    avg_cf = words[1] or 10**18
    debt = 0
    if coll and hf and hf < (1 << 250):
        debt = coll * avg_cf // hf
    return {
        "protocol": "v4",
        "hf": hf,
        "coll_base": coll,
        "debt_base": debt,
        "avg_cf": avg_cf,
        "coll_usd": coll / 1e26 if coll else 0.0,
        "debt_usd": debt / 1e26 if debt else 0.0,
        "layout": "v4",
    }


def get_v3_account(user: str) -> dict | None:
    try:
        raw = call(RPC_CALL, V3_POOL, SEL["getUserAccountData"] + _pad_addr(user)[2:])
    except (requests.RequestException, KeyError, ValueError):
        return None
    return _parse_account_raw(raw, "v3")


def get_v4_account(user: str) -> dict | None:
    try:
        raw = call(RPC_CALL, SPOKE, SEL["getUserAccountData"] + _pad_addr(user)[2:])
    except (requests.RequestException, KeyError, ValueError):
        return None
    parsed = _parse_account_raw(raw, "v4")
    if parsed and (parsed.get("debt_usd") or 0) > 0:
        parsed["protocol"] = "v4"
        return parsed
    return None


def get_account_data(user: str):
    """Legacy tuple for dashboard._sweep compatibility.

    [riskPremium, avgCF, healthFactor, collValue, debtValue, ...]
    V3 accounts are projected into this shape (coll/debt as USD*1e26).
    """
    acc = get_v3_account(user) or get_v4_account(user)
    if not acc:
        return [0, 0, (1 << 256) - 1, 0, 0, 0, 0]
    coll_v4 = int((acc.get("coll_usd") or 0) * 1e26)
    debt_v4 = int((acc.get("debt_usd") or 0) * 1e26)
    hf = int(acc.get("hf") or 0) or ((1 << 256) - 1)
    avg_cf = int(acc.get("avg_cf") or acc.get("liq_thresh") or 0)
    if acc.get("layout") == "v3" and acc.get("liq_thresh"):
        # V3 liq thresh is bps (8250); avgCF as 1e18 * thresh/10000
        avg_cf = int(acc["liq_thresh"]) * 10**14
    return [0, avg_cf, hf, coll_v4, debt_v4, 0, 0]


def _estimate_from_account(acc: dict, gas_gwei: float, eth_usd: float) -> dict:
    hf = int(acc.get("hf") or 0)
    hf_f = hf / 1e18 if hf and hf < (1 << 200) else 999.0
    debt_usd = float(acc.get("debt_usd") or 0)
    coll_usd = float(acc.get("coll_usd") or 0)
    close = 1.0 if hf_f < CLOSE_FACTOR_HF else 0.5
    cover = min(debt_usd * close, max(coll_usd / (1.0 + DEFAULT_BONUS), 0))
    bonus_usd = cover * DEFAULT_BONUS
    proto = bonus_usd * PROTOCOL_FEE
    gas_usd = (GAS_UNITS_FLASH * float(gas_gwei or 1.0) * 1e-9) * float(eth_usd or 0)
    net = bonus_usd - proto - gas_usd
    return {
        "close_factor": close,
        "cover_usd": round(cover, 2),
        "bonus_usd": round(bonus_usd, 2),
        "gas_usd": round(gas_usd, 2),
        "net_usd": round(net, 2),
        "profit_usd": round(net, 2),
        "profitUsd": int(max(net, 0) * 1e18),
    }


def build_plan(user: str, gas_gwei: float | None = None, eth_usd: float | None = None,
               allow_near: bool = False, max_hf: float = 1.05):
    """HF + estimated flash-liq net.

    Default: None if not liquidatable (HF>=1) or dust.
    allow_near=True keeps closest-book plans (HF < max_hf) so the hot path
    can sign as soon as HF crosses 1 instead of harvesting from zero.
    """
    acc = get_v3_account(user)
    proto = "v3"
    if not acc or (acc.get("debt_usd") or 0) <= 0:
        acc = get_v4_account(user)
        proto = "v4"
    if not acc:
        return None
    hf = int(acc.get("hf") or 0)
    hf_f = hf / 1e18 if hf and hf < (1 << 200) else 999.0
    liquidatable = hf < HEALTH_THRESHOLD
    if not liquidatable:
        if not allow_near or hf_f > float(max_hf):
            return None
    debt_usd = float(acc.get("debt_usd") or 0)
    if debt_usd < DUST_USD:
        return None
    gas = float(gas_gwei or 0) or 1.0
    eth = float(eth_usd or 0)
    est = _estimate_from_account(acc, gas, eth)
    pair = user_liq_pair(user) if proto == "v3" else {
        "coll_addr": "", "debt_addr": "", "coll_sym": "?", "debt_sym": "?",
    }
    plan = {
        "user": (user or "").lower(),
        "protocol": acc.get("protocol") or proto,
        "healthFactor": str(hf),
        "hf": str(hf),
        "coll_usd": round(float(acc.get("coll_usd") or 0), 2),
        "debt_usd": round(debt_usd, 2),
        "coll_sym": pair.get("coll_sym") or "?",
        "debt_sym": pair.get("debt_sym") or "?",
        "collateralAsset": pair.get("coll_addr") or "",
        "debtAsset": pair.get("debt_addr") or "",
        "coll_addr": pair.get("coll_addr") or "",
        "debt_addr": pair.get("debt_addr") or "",
        "debtToCover": str((1 << 256) - 1),
        "liquidatable": liquidatable,
        "collateralReserveId": None,
        "debtReserveId": None,
        "gas_gwei": gas,
        **est,
    }
    return plan


def load_borrower_cache() -> list[str]:
    if not os.path.exists(_CACHE_PATH):
        return []
    try:
        with open(_CACHE_PATH, encoding="utf-8") as f:
            data = json.load(f) or {}
        rows = data.get("users") or []
        out = []
        for r in rows:
            if isinstance(r, str) and r.startswith("0x"):
                out.append(r.lower())
            elif isinstance(r, dict) and str(r.get("addr") or "").startswith("0x"):
                out.append(str(r["addr"]).lower())
        log.debug("Loaded %d borrowers from cache", len(out))
        return out
    except (json.JSONDecodeError, OSError, ValueError):
        return []


def save_borrower_cache(users, extras: dict | None = None) -> None:
    rows = []
    seen = set()
    extra_map = extras or {}
    for u in users or []:
        a = str(u or "").lower()
        if not a.startswith("0x") or a in seen:
            continue
        seen.add(a)
        rec = {"addr": a, **(extra_map.get(a) or {})}
        rows.append(rec)
    payload = {"updated": int(time.time()), "users": rows[:400]}
    try:
        with open(_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f)
    except OSError:
        pass


def _get_logs(from_block: int, to_block: int, address, topics: list) -> list:
    params = {
        "fromBlock": hex(int(from_block)),
        "toBlock": hex(int(to_block)),
        "topics": topics,
    }
    if isinstance(address, (list, tuple)):
        params["address"] = [str(a).lower() for a in address]
    elif address:
        params["address"] = str(address).lower()
    got = jrpc(RPC_CALL, "eth_getLogs", [params])
    return got or []


def get_logs_chunked(from_block: int, to_block: int, address, topics: list,
                     chunk: int = 400) -> tuple[list, list]:
    """eth_getLogs in RPC-friendly windows. Returns (logs, errors)."""
    logs, errs = [], []
    start, end = int(from_block), int(to_block)
    if end < start:
        return [], []
    cur = start
    while cur <= end:
        hi = min(end, cur + chunk - 1)
        try:
            logs.extend(_get_logs(cur, hi, address, topics))
        except Exception as e:  # noqa: BLE001
            log.warning("getLogs chunk error %s-%s: %s", start, end, e)
            errs.append(f"{cur}-{hi}: {e}")
        cur = hi + 1
    return logs, errs


def harvest_pool_users(pool: str, from_block: int, to_block: int) -> dict:
    """Unique users from Supply/Borrow/Withdraw/Repay/LiquidationCall."""
    users: dict[str, int] = {}
    n_logs = 0
    errors = []
    for topic, idx in V3_USER_LOG_TOPICS:
        logs, err = get_logs_chunked(from_block, to_block, pool, [topic])
        errors.extend(err)
        n_logs += len(logs)
        for lg in logs:
            topics = lg.get("topics") or []
            if len(topics) > idx:
                u = _addr_from_word(str(topics[idx]).replace("0x", "").rjust(64, "0"))
                if u.startswith("0x") and int(u, 16):
                    users[u] = int(lg.get("blockNumber") or "0x0", 16)
    return {
        "users": list(users.keys()),
        "seen_block": users,
        "n_logs": n_logs,
        "errors": errors[:8],
        "from_block": from_block,
        "to_block": to_block,
    }


def harvest_event_users(from_block: int, to_block: int) -> dict:
    """Aave V3 pool users from Supply/Borrow/Withdraw/Repay/LiquidationCall + cache."""
    out = harvest_pool_users(V3_POOL, from_block, to_block)
    seen = {str(u).lower() for u in (out.get("users") or [])}
    extra = []
    for a in load_borrower_cache():
        al = str(a or "").lower()
        if al.startswith("0x") and al not in seen:
            extra.append(al)
            seen.add(al)
    if extra:
        out["users"] = list(out.get("users") or []) + extra
    return out


def pending_event_users() -> dict:
    """Best-effort pending Aave V3 Borrow/Liq logs. Fail-open, never stall."""
    users: dict[str, int] = {}
    errors = []
    pending_ok = False

    def ingest(logs):
        for lg in logs or []:
            topics = lg.get("topics") or []
            for idx in (2, 3):
                if len(topics) > idx:
                    u = _addr_from_word(str(topics[idx]).replace("0x", "").rjust(64, "0"))
                    if u.startswith("0x") and int(u, 16):
                        users[u] = 0

    urls = list(RPC_CALL or [])[:2]
    for url in urls:
        for topic in (V3_BORROW_TOPIC, V3_LIQ_TOPIC):
            try:
                r = requests.post(
                    url,
                    json={"jsonrpc": "2.0", "id": 1, "method": "eth_getLogs",
                          "params": [{
                              "fromBlock": "pending",
                              "toBlock": "pending",
                              "address": V3_POOL.lower(),
                              "topics": [topic],
                          }]},
                    headers={"Content-Type": "application/json",
                             "User-Agent": _UA},
                    timeout=1.5,
                )
                js = r.json()
                if js.get("error"):
                    errors.append(str(js["error"])[:80])
                    continue
                ingest(js.get("result") or [])
                pending_ok = True
            except Exception as e:  # noqa: BLE001
                errors.append(str(e)[:80])
        if pending_ok:
            break
    log.debug("Pending event users: %d", len(users))
    return {
        "users": list(users.keys()),
        "pending_ok": pending_ok,
        "errors": errors[:4],
        "n": len(users),
    }


def parse_v3_liq_log(lg) -> dict | None:
    topics = lg.get("topics") or []
    if len(topics) < 4:
        return None
    coll = _addr_from_word(str(topics[1]).replace("0x", "").rjust(64, "0"))
    debt = _addr_from_word(str(topics[2]).replace("0x", "").rjust(64, "0"))
    user = _addr_from_word(str(topics[3]).replace("0x", "").rjust(64, "0"))
    words = _words(lg.get("data"))
    debt_cover = int(words[0], 16) if words else 0
    coll_seized = int(words[1], 16) if len(words) > 1 else 0
    searcher = _addr_from_word(words[2]) if len(words) > 2 else ""
    return {
        "protocol": "v3",
        "coll_addr": coll,
        "debt_addr": debt,
        "coll_sym": token_sym(coll),
        "debt_sym": token_sym(debt),
        "user": user,
        "searcher": searcher,
        "debt_to_cover": debt_cover,
        "coll_seized": coll_seized,
        "spoke": (lg.get("address") or V3_POOL).lower(),
        "tx": (lg.get("transactionHash") or "").lower(),
        "block": int(lg.get("blockNumber") or "0x0", 16),
        "log_index": int(lg.get("logIndex") or "0x0", 16),
        "topic0": str(topics[0]).lower() if topics else "",
    }


def parse_v4_liq_log(lg) -> dict | None:
    topics = lg.get("topics") or []
    if len(topics) < 4:
        return None
    coll_rid = int(topics[1], 16)
    debt_rid = int(topics[2], 16)
    user = _addr_from_word(str(topics[3]).replace("0x", "").rjust(64, "0"))
    words = _words(lg.get("data"))
    searcher = _addr_from_word(words[0]) if words else ""
    debt_restored = int(words[2], 16) if len(words) > 2 else 0
    coll_removed = int(words[5], 16) if len(words) > 5 else 0
    return {
        "protocol": "v4",
        "coll_rid": coll_rid,
        "debt_rid": debt_rid,
        "user": user,
        "searcher": searcher,
        "debt_to_cover": debt_restored,
        "coll_seized": coll_removed,
        "spoke": (lg.get("address") or "").lower(),
        "tx": (lg.get("transactionHash") or "").lower(),
        "block": int(lg.get("blockNumber") or "0x0", 16),
        "log_index": int(lg.get("logIndex") or "0x0", 16),
        "topic0": str(topics[0]).lower() if topics else "",
    }


def scan_liquidation_logs(from_block: int, to_block: int,
                          extra_addrs: list | None = None) -> dict:
    """Recent on-chain Aave liquidations (V3 Pool + optional V4 spokes)."""
    logs = []
    errors = []
    v3, e1 = get_logs_chunked(from_block, to_block, V3_POOL, [V3_LIQ_TOPIC])
    errors.extend(e1)
    parsed = []
    for lg in v3:
        p = parse_v3_liq_log(lg)
        if p:
            parsed.append(p)
        logs.append(lg)
    addrs = []
    for a in extra_addrs or []:
        al = str(a).lower()
        if al and al != V3_POOL.lower():
            addrs.append(al)
    if SPOKE.lower() not in addrs:
        addrs.append(SPOKE.lower())
    for i in range(0, len(addrs), 4):
        chunk = addrs[i:i + 4]
        got, e2 = get_logs_chunked(from_block, to_block, chunk, [V4_LIQ_TOPIC])
        errors.extend(e2)
        for lg in got:
            p = parse_v4_liq_log(lg)
            if p:
                parsed.append(p)
            logs.append(lg)
    parsed.sort(key=lambda r: (r.get("block") or 0, r.get("log_index") or 0), reverse=True)
    return {
        "events": parsed,
        "n_logs": len(logs),
        "errors": errors[:8],
        "from_block": from_block,
        "to_block": to_block,
    }


def liq_event_profit(ev: dict) -> tuple[float | None, float | None]:
    """Gross bonus USD from seized coll vs covered debt (oracle).

    Only when BOTH sides price. A one-sided or negative spread is a
    decode miss — not competitor PnL (they already got paid on-chain).
    """
    if ev.get("protocol") == "v3" or ev.get("coll_addr"):
        coll_usd = amount_usd(ev.get("coll_addr"), ev.get("coll_seized") or 0)
        debt_usd = amount_usd(ev.get("debt_addr"), ev.get("debt_to_cover") or 0)
        if coll_usd > 0 and debt_usd > 0:
            bonus = coll_usd - debt_usd
            if bonus > 0:
                return round(bonus, 2), round(coll_usd, 2)
    return None, None


def sweep_users(users, *, gas_gwei: float = 1.0, eth_usd: float = 0.0,
                contested=None, recent_comp=None, max_users: int = 140) -> dict:
    """HF-check harvested borrowers. Live feed = HF<1, dust-filtered, +EV net."""
    seen, uniq = set(), []
    for u in users or []:
        a = str(u or "").lower()
        if a.startswith("0x") and len(a) >= 42 and a not in seen:
            seen.add(a)
            uniq.append(a[:42])
    uniq = uniq[: max(40, int(max_users))]
    contested = {str(x).lower() for x in (contested or [])}
    recent_comp = {str(x).lower() for x in (recent_comp or [])}
    log.info("Sweep: %d users", len(uniq))

    def one(u):
        try:
            acc = get_v3_account(u) or get_v4_account(u)
            return u, acc, None
        except Exception as e:  # noqa: BLE001
            log.warning("User %s sweep error: %s", u, e)
            return u, None, str(e)[:80]

    rows = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for item in ex.map(one, uniq):
            rows.append(item)

    opps, watch, skipped = [], [], {
        "dust": 0, "healthy": 0, "no_account": 0, "negative": 0, "rpc": 0,
    }
    meta_users = {}
    for u, acc, err in rows:
        if err:
            skipped["rpc"] += 1
            continue
        if not acc:
            skipped["no_account"] += 1
            continue
        hf = int(acc.get("hf") or 0)
        coll_usd = float(acc.get("coll_usd") or 0)
        debt_usd = float(acc.get("debt_usd") or 0)
        hf_f = hf / 1e18 if hf and hf < (1 << 200) else 999.0
        meta_users[u] = {
            "hf": hf_f, "debt_usd": round(debt_usd, 2),
            "coll_usd": round(coll_usd, 2),
        }
        watch.append({
            "user": u,
            "hf": str(hf),
            "coll": str(int(coll_usd * 1e26)),
            "debt": str(int(debt_usd * 1e26)),
            "coll_usd": round(coll_usd, 2),
            "debt_usd": round(debt_usd, 2),
            "protocol": acc.get("protocol") or "v3",
            "avg_cf": str(acc.get("avg_cf") or acc.get("liq_thresh") or 0),
        })
        if debt_usd < DUST_USD:
            skipped["dust"] += 1
            continue
        if hf >= HEALTH_THRESHOLD:
            skipped["healthy"] += 1
            continue
        plan = build_plan(u, gas_gwei=gas_gwei, eth_usd=eth_usd)
        if not plan:
            skipped["no_account"] += 1
            continue
        net = plan.get("net_usd")
        if net is not None and net <= 0:
            skipped["negative"] += 1
            continue
        race = u in contested or u[:10] in contested
        rec_comp = u in recent_comp or u[:10] in recent_comp
        opps.append({
            "user": u,
            "hf": plan.get("healthFactor") or str(hf),
            "coll_sym": plan.get("coll_sym") or "?",
            "debt_sym": plan.get("debt_sym") or "?",
            "coll_usd": plan.get("coll_usd"),
            "debt_usd": plan.get("debt_usd"),
            "cover_usd": plan.get("cover_usd"),
            "repay_usd": plan.get("cover_usd"),
            "close_factor": plan.get("close_factor"),
            "bonus_usd": plan.get("bonus_usd"),
            "gas_usd": plan.get("gas_usd"),
            "profit_usd": plan.get("profit_usd") or plan.get("net_usd"),
            "net_usd": plan.get("net_usd"),
            "protocol": plan.get("protocol") or "v3",
            "coll_addr": plan.get("coll_addr") or "",
            "debt_addr": plan.get("debt_addr") or "",
            "contested": race,
            "recent_competitor": rec_comp,
            "race": race or rec_comp,
            "source": "sweep",
        })
    watch.sort(key=lambda r: int(r.get("hf") or 0))
    save_borrower_cache([w["user"] for w in watch[:200]] + uniq[:80], meta_users)
    return {
        "opps": opps,
        "watch": watch[:CLOSEST_SWEEP],
        "scanned": len(uniq),
        "skipped": skipped,
        "n_hf_ok": skipped["healthy"],
    }
