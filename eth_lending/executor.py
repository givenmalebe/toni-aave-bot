"""Encode live Aave-flash liquidation calldata for Spark / Compound / Morpho.

Calldata targets GenericFlashLiquidator (contracts/GenericFlashLiquidator.sol).
KIND() == keccak256("toni.genericFlashLiq.v1") proves the bytecode is wired.
"""
from __future__ import annotations

import os
import time

import liquidation_bot as lb

from . import util as u

# keccak256("toni.genericFlashLiq.v1") via foundry cast keccak
GENERIC_KIND = "0x8caa2b9a42135cb026f57f48dfc7f1d565f83039807016026fa2fdfe883d27d1"
SEL_KIND = "0xc872da3c"
SEL_GET_POOL = "0x1698ee82"

UNI_V3_FACTORY = "0x1F98431c8aD98523631AE4a59f267346ea31F984"
WETH = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
AAVE_V3_POOL = lb.V3_POOL

SIG_POOL = "flashLiquidatePool(address,address,address,address,uint256,bytes)"
SIG_COMET = "flashLiquidateComet(address,address,address,uint256,bytes)"
SIG_MORPHO = (
    "flashLiquidateMorpho(address,address,address,address,uint256,address,"
    "uint256,uint256,uint256,bytes)"
)
SIG_AAVE = "flashLiquidate(address,address,address,uint256)"

FEES = (500, 3000, 100, 10000)
U256_MAX = (1 << 256) - 1

LIVE_BLOCK_NEED_GENERIC = (
    "Spark/Compound/Morpho: deploy GenericFlashLiquidator (contracts/DEPLOY.md), "
    "paste LIQ_GENERIC_CONTRACT into .env, restart — KIND() not on chain yet"
)
LIVE_BLOCK_NO_PATH = (
    "no Uni V3 coll→debt pool decoded (direct or via WETH) — cannot repay Aave flash"
)

_kind_cache = {}
_pool_cache = {}


def generic_contract() -> str:
    for key in ("LIQ_GENERIC_CONTRACT", "LIQ_CONTRACT"):
        a = (os.environ.get(key) or "").strip()
        if a.startswith("0x") and len(a) >= 42:
            return a
    return ""


def contract_is_generic(addr: str) -> bool:
    a = (addr or "").lower()
    if not a.startswith("0x"):
        return False
    hit = _kind_cache.get(a)
    now = time.time()
    if hit and now - hit[0] < 120:
        return bool(hit[1])
    ok = False
    try:
        raw = u.call(a, SEL_KIND)
        if raw:
            n = int(raw, 16)
            ok = n == int(GENERIC_KIND, 16)
    except Exception:
        ok = False
    _kind_cache[a] = (now, ok)
    return ok


def usd_to_wei(token: str, usd: float) -> int | None:
    if not token or not str(token).startswith("0x"):
        return None
    try:
        px = lb.asset_price_usd(token)
    except Exception:
        px = 0.0
    if not px or px <= 0:
        return None
    try:
        dec = int(lb.token_decimals(token))
    except Exception:
        dec = 18
    amt = int(float(usd) / px * (10 ** dec))
    return amt if amt > 0 else None


def _get_pool(a: str, b: str, fee: int) -> str:
    key = f"{a}:{b}:{fee}"
    hit = _pool_cache.get(key)
    if hit is not None:
        return hit
    addr = ""
    try:
        data = (
            SEL_GET_POOL
            + u.pad_addr(a)[2:]
            + u.pad_addr(b)[2:]
            + u.pad32(int(fee))
        )
        raw = u.call(UNI_V3_FACTORY, data)
        ws = u.words(raw)
        if ws:
            p = u.addr_word(ws[0])
            if p and int(p, 16):
                addr = p.lower()
    except Exception:
        addr = ""
    _pool_cache[key] = addr
    return addr


def _fee_for(a: str, b: str) -> int | None:
    a = (a or "").lower()
    b = (b or "").lower()
    if not a.startswith("0x") or not b.startswith("0x"):
        return None
    for fee in FEES:
        if _get_pool(a, b, fee):
            return int(fee)
    return None


def encode_v3_path(tokens: list[str], fees: list[int]) -> str:
    out = bytearray()
    for i, tok in enumerate(tokens):
        t = (tok or "").lower().replace("0x", "")
        if len(t) != 40:
            return ""
        out += bytes.fromhex(t)
        if i < len(fees):
            out += int(fees[i]).to_bytes(3, "big")
    return "0x" + out.hex() if out else ""


def uni_v3_path(token_in: str, token_out: str) -> str:
    a = (token_in or "").lower()
    b = (token_out or "").lower()
    if not a.startswith("0x") or not b.startswith("0x"):
        return ""
    if a == b:
        return "0x"
    fee = _fee_for(a, b)
    if fee is not None:
        return encode_v3_path([a, b], [fee])
    if a != WETH and b != WETH:
        f1 = _fee_for(a, WETH)
        f2 = _fee_for(WETH, b)
        if f1 is not None and f2 is not None:
            return encode_v3_path([a, WETH, b], [f1, f2])
    return ""


def _path_or_block(coll: str, debt: str) -> tuple[str, str]:
    coll = (coll or "").lower()
    debt = (debt or "").lower()
    if coll and debt and coll == debt:
        return "0x", ""
    path = uni_v3_path(coll, debt)
    if path:
        return path, ""
    return "", LIVE_BLOCK_NO_PATH


def plan_aave_like(*, pool: str, user: str, coll: str, debt: str,
                   cover_usd: float, cover_wei: int | None = None) -> dict:
    leftover = ""
    path, path_err = _path_or_block(coll, debt)
    amt = cover_wei
    if amt is None:
        amt = usd_to_wei(debt, cover_usd)
    if amt is None or amt <= 0:
        leftover = "flash wei from cover_usd not decoded (oracle/decimals)"
        live = False
        amt = 0
    elif path_err:
        leftover = path_err
        live = False
    else:
        live = True
    return {
        "flash_src": "aave-v3",
        "flash_pool": AAVE_V3_POOL,
        "flash_asset": debt,
        "flash_amount": str(amt),
        "target": pool,
        "fn": "flashLiquidatePool",
        "liq_sig": SIG_POOL,
        "liq_args": [pool, coll, debt, user, str(amt or U256_MAX), path or "0x"],
        "swap_path": path,
        "gas_limit": 1_600_000,
        "live_ok": live,
        "note": leftover,
    }


def plan_comet(*, comet: str, user: str, coll: str, base: str,
               base_wei: int) -> dict:
    leftover = ""
    path, path_err = _path_or_block(coll, base)
    amt = int(base_wei or 0)
    if amt <= 0:
        leftover = "comet base wei not decoded"
        live = False
    elif path_err:
        leftover = path_err
        live = False
    else:
        live = True
    return {
        "flash_src": "aave-v3",
        "flash_pool": AAVE_V3_POOL,
        "flash_asset": base,
        "flash_amount": str(amt),
        "target": comet,
        "fn": "absorb+buyCollateral",
        "liq_sig": SIG_COMET,
        "liq_args": [comet, user, coll, str(amt), path or "0x"],
        "swap_path": path,
        "gas_limit": 1_800_000,
        "live_ok": live,
        "note": leftover,
    }


def plan_morpho(*, user: str, coll: str, loan: str, oracle: str, irm: str,
                lltv: int, seized: int, repaid_shares: int,
                flash_wei: int) -> dict:
    leftover = ""
    path, path_err = _path_or_block(coll, loan)
    amt = int(flash_wei or 0)
    if not (oracle and irm and int(lltv or 0) > 0):
        leftover = "Morpho market params (oracle/irm/lltv) not on-chain"
        live = False
    elif amt <= 0:
        leftover = "Morpho flash wei (borrow assets) not decoded"
        live = False
    elif int(seized or 0) == 0 and int(repaid_shares or 0) == 0:
        leftover = "Morpho seizedAssets and repaidShares both 0 — position not decoded"
        live = False
    elif int(seized or 0) > 0 and int(repaid_shares or 0) > 0:
        leftover = "Morpho liquidate needs seized=0 or repaidShares=0"
        live = False
    elif path_err:
        leftover = path_err
        live = False
    else:
        live = True
    return {
        "flash_src": "aave-v3",
        "flash_pool": AAVE_V3_POOL,
        "flash_asset": loan,
        "flash_amount": str(amt),
        "target": "0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb",
        "fn": "liquidate",
        "liq_sig": SIG_MORPHO,
        "liq_args": [
            loan, coll, oracle, irm, str(int(lltv or 0)), user,
            str(int(seized or 0)), str(int(repaid_shares or 0)),
            str(amt), path or "0x",
        ],
        "swap_path": path,
        "gas_limit": 1_700_000,
        "live_ok": live,
        "note": leftover,
        "seized_assets": str(int(seized or 0)),
        "repaid_shares": str(int(repaid_shares or 0)),
    }


def apply_plan(rec: dict, flash_plan: dict) -> dict:
    rec["flash_plan"] = flash_plan
    rec["live_ok"] = bool(flash_plan.get("live_ok"))
    rec["live_block_reason"] = "" if rec["live_ok"] else (
        flash_plan.get("note") or LIVE_BLOCK_NEED_GENERIC)
    rec["liq_sig"] = flash_plan.get("liq_sig") or ""
    rec["liq_args"] = list(flash_plan.get("liq_args") or [])
    rec["debtToCover"] = flash_plan.get("flash_amount") or rec.get("debtToCover")
    rec["gas_limit"] = flash_plan.get("gas_limit") or 1_500_000
    if flash_plan.get("note") and not rec.get("leftover"):
        rec["leftover"] = flash_plan["note"]
    if rec["live_ok"]:
        rec["leftover"] = rec.get("leftover") or ""
        if rec["leftover"] in (LIVE_BLOCK_NEED_GENERIC, u.LIVE_BLOCK_AAVE_ONLY):
            rec["leftover"] = ""
    return rec
