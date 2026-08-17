"""Shared ETH lending helpers: RPC, Aave-flash fee, token math.

Flash liquidity for every venue is Aave V3 Pool `flashLoan` (~9 bps). Premium is
decoded from `FLASHLOAN_PREMIUM_TOTAL()` when RPC allows; otherwise 9 bps.

Selectors / topics are Foundry `cast sig` / `cast keccak` values — not NIST SHA-3.
"""
from __future__ import annotations

import json
import os
import time

import liquidation_bot as lb

SEL = {
    "FLASHLOAN_PREMIUM_TOTAL": "0x074b2e43",
    "ADDRESSES_PROVIDER": "0x0542975c",
    "getPriceOracle": "0xfca513a8",
    "getConfiguration": "0xc44b11f7",
    "isLiquidatable": "0x042e02cf",
    "isAbsorbPaused": "0x8d5d814c",
    "isBuyPaused": "0xd8e5f611",
    "getAssetInfo": "0xc8c7fe6b",
    "numAssets": "0xa46fe83b",
    "baseToken": "0xc55dae63",
    "baseTokenPriceFeed": "0xe7dad6bd",
    "baseScale": "0x44c1e5eb",
    "borrowBalanceOf": "0x374c49b4",
    "userCollateral": "0x2b92a07d",
    "getPrice": "0x41976e09",
    "storeFrontPriceFactor": "0x1f5954bd",
    "getCollateralReserves": "0x9ff567f8",
    "quoteCollateral": "0x7ac88ed1",
    "position": "0x93c52062",
    "market": "0x5c60e39a",
    "idToMarketParams": "0x2c3c9157",
    "price": "0xa035b1fe",
    "liquidationCall": "0x00a718a9",
    "absorb": "0xc3cecfd2",
    "buyCollateral": "0xe4e6e779",
}

TOPIC = {
    "v3_liq": lb.V3_LIQ_TOPIC,
    "v3_borrow": lb.V3_BORROW_TOPIC,
    "comet_absorb_coll": "0x9850ab1af75177e4a9201c65a2cf7976d5d28e40ef63494b44366f86b2f9412e",
    "comet_absorb_debt": "0x1547a878dc89ad3c367b6338b4be6a65a5dd74fb77ae044da1e8747ef1f4f62f",
    "comet_buy": "0xf891b2a411b0e66a5f0a6ff1368670fefa287a13f541eb633a386a1a9cc7046b",
    "comet_withdraw": "0x9b1bfa7fa9ee420a16e124f794c35ac9f90472acc99140eb2f6447c714cad8eb",
    "comet_supply_coll": "0xfa56f7b24f17183d81894d3ac2ee654e3c26388d17a28dbd9549b8114304e1f4",
    "morpho_liq": "0xa4946ede45d0c6f06a0f5ce92c9ad3b4751452d2fe0e25010783bcab57a67e41",
    "morpho_borrow": "0x570954540bed6b1304a87dfe815a5eda4a648f7097a16240dcd85c9b5fd42a43",
}

AAVE_V3_POOL = lb.V3_POOL
AAVE_FLASH_BPS_FALLBACK = 9
GAS_UNITS_FLASH = 550_000
GAS_UNITS_COMET = 700_000
DUST_USD = float(getattr(lb, "DUST_USD", 25.0) or 25.0)
CLOSE_FACTOR_HF = 0.95
DEFAULT_BONUS = 0.05
LIVE_BLOCK_AAVE_ONLY = (
    "Spark/Compound/Morpho: deploy GenericFlashLiquidator (contracts/DEPLOY.md), "
    "paste LIQ_GENERIC_CONTRACT into .env, restart — KIND() not on chain yet"
)

CACHE_DIR = os.path.dirname(os.path.abspath(__file__))
_flash_bps_cache = {"ts": 0, "bps": AAVE_FLASH_BPS_FALLBACK}
_code_cache = {}
_cfg_cache = {}


def call(to: str, data: str):
    return lb.call(lb.RPC_CALL, to, data)


def words(raw) -> list[str]:
    return lb._words(raw)


def pad_addr(user: str) -> str:
    return lb._pad_addr(user)


def pad32(n: int) -> str:
    return hex(int(n) & ((1 << 256) - 1))[2:].rjust(64, "0")


def addr_word(w: str) -> str:
    return lb._addr_from_word(w)


def has_code(addr: str) -> bool:
    a = (addr or "").lower()
    if not a.startswith("0x"):
        return False
    hit = _code_cache.get(a)
    if hit is not None:
        return hit
    ok = False
    try:
        raw = lb.jrpc(lb.RPC_CALL, "eth_getCode", [a, "latest"])
        ok = bool(raw) and str(raw) not in ("0x", "0x0")
    except Exception:
        ok = False
    _code_cache[a] = ok
    return ok


def aave_flash_bps() -> int:
    """Aave V3 Pool FLASHLOAN_PREMIUM_TOTAL — units are bps (9 → 9/10000)."""
    now = time.time()
    if now - _flash_bps_cache["ts"] < 300:
        return int(_flash_bps_cache["bps"])
    bps = AAVE_FLASH_BPS_FALLBACK
    try:
        raw = call(AAVE_V3_POOL, SEL["FLASHLOAN_PREMIUM_TOTAL"])
        n = int(raw, 16)
        if 0 < n <= 100:  # 1–100 bps is the live Aave band
            bps = int(n)
    except Exception:
        pass
    _flash_bps_cache["ts"] = now
    _flash_bps_cache["bps"] = bps
    return bps


def gas_usd(gwei: float, eth_usd: float, units: int = GAS_UNITS_FLASH) -> float:
    return (int(units) * float(gwei or 1.0) * 1e-9) * float(eth_usd or 0)


def apply_flash_fee(cover_usd: float, bonus_usd: float, proto_fee_usd: float,
                    gas: float, flash_bps: int | None = None) -> dict:
    bps = int(flash_bps if flash_bps is not None else aave_flash_bps())
    flash_usd = float(cover_usd or 0) * bps / 10_000.0
    net = float(bonus_usd or 0) - float(proto_fee_usd or 0) - float(gas or 0) - flash_usd
    return {
        "flash_fee_bps": bps,
        "flash_fee_usd": round(flash_usd, 4),
        "flash_fee_src": "aave-v3",
        "flash_note": f"Aave V3 flashLoan {bps} bps",
        "net_usd": round(net, 2),
        "profit_usd": round(net, 2),
    }


def parse_reserve_config(data: int) -> dict:
    """Aave V3 ReserveConfigurationMap. Bonus 10500 = 5%; proto fee 1000 = 10%."""
    bonus_raw = (int(data) >> 32) & 0xFFFF
    proto_raw = (int(data) >> 152) & 0xFFFF
    bonus = (bonus_raw / 10000.0 - 1.0) if bonus_raw >= 10000 else None
    proto = (proto_raw / 10000.0) if proto_raw else 0.0
    return {
        "liq_bonus_raw": bonus_raw,
        "liq_bonus": bonus,
        "protocol_fee": proto,
        "liq_thresh_bps": (int(data) >> 16) & 0xFFFF,
        "ltv_bps": int(data) & 0xFFFF,
    }


def get_reserve_config(pool: str, asset: str) -> dict:
    key = f"{(pool or '').lower()}:{(asset or '').lower()}"
    hit = _cfg_cache.get(key)
    now = time.time()
    if hit and now - hit[0] < 300:
        return hit[1]
    out = {"liq_bonus": None, "protocol_fee": 0.0}
    try:
        data = SEL["getConfiguration"] + pad_addr(asset)[2:]
        raw = call(pool, data)
        ws = words(raw)
        n = int(ws[0], 16) if ws else 0
        if len(ws) >= 2 and int(ws[0], 16) == 32:
            n = int(ws[1], 16)
        out = parse_reserve_config(n)
    except Exception:
        pass
    _cfg_cache[key] = (now, out)
    return out


def load_users(name: str) -> list[str]:
    path = os.path.join(CACHE_DIR, f"{name}_users.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f) or {}
        rows = data.get("users") or []
        out = []
        for r in rows:
            if isinstance(r, str) and r.startswith("0x"):
                out.append(r.lower()[:42])
            elif isinstance(r, dict) and str(r.get("addr") or "").startswith("0x"):
                out.append(str(r["addr"]).lower()[:42])
        return out
    except Exception:
        return []


def save_users(name: str, users) -> None:
    path = os.path.join(CACHE_DIR, f"{name}_users.json")
    seen, rows = set(), []
    for u in users or []:
        a = str(u or "").lower()[:42]
        if a.startswith("0x") and a not in seen:
            seen.add(a)
            rows.append(a)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"updated": int(time.time()), "users": rows[:400]}, f)
    except OSError:
        pass


def uniq_addrs(seq, limit: int = 140) -> list[str]:
    seen, out = set(), []
    for u in seq or []:
        a = str(u or "").lower()[:42]
        if a.startswith("0x") and len(a) >= 42 and a not in seen:
            seen.add(a)
            out.append(a)
        if len(out) >= limit:
            break
    return out


def opp_base(*, protocol_id: str, protocol_label: str, user: str,
             live_ok: bool, leftover: str = "") -> dict:
    return {
        "protocol_id": protocol_id,
        "protocol": protocol_label,
        "protocol_label": protocol_label,
        "user": (user or "").lower(),
        "live_ok": bool(live_ok),
        "live_block_reason": "" if live_ok else (leftover or LIVE_BLOCK_AAVE_ONLY),
        "leftover": leftover,
        "flash_fee_src": "aave-v3",
        "flash_note": "Aave V3 flashLoan",
    }


# leftover catalogue (honest skips — do not ship a stub scanner)
PROTOCOL_LEFTOVERS = [
    "Maker/Sky vault Dog/clipper auctions skipped (different auction model; Spark covers SparkLend only)",
    "crvUSD / Curve LlamaLend skipped (LLAMMA band liquidation not decoded)",
    "Euler v2 EVC skipped (liquidator ABI/addresses not wired)",
    "Fluid Instant Liquidity skipped (vault liquidation decode not verified)",
    "Liquity v2 / Bold skipped (trove redemption vs liquidation mix)",
]
