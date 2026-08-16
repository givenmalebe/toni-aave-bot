"""Solana mainnet probes for the TONI SOL dashboard twin.

Primary lending target: Solend (Save) — public markets/reserves API.
Arb: discover from on-chain Raydium AMM v4 / Orca Whirlpool accounts; execute
via Jupiter /swap + Jito bundle. Live list is +EV only (net after CU + tip +
slip, at/above the dynamic USD floor). Jupiter lite is fallback for tokens
we cannot decode — not the only edge hunt.
Landing stream: Solend program sigs (processed/confirmed) + Jito tip accounts
as a public-RPC stand-in for mempool liquidation / MEV races.
Honest feeds only: empty lists when nothing liquidatable; no fake MEV.
"""
from __future__ import annotations

import base64
import json
import math
import os
import socket
import struct
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import requests

# IPv4-first: several public Solana RPCs advertise unroutable AAAA records.
if not getattr(socket.getaddrinfo, "_toni_ipv4", False):
    _orig_gai = socket.getaddrinfo

    def _ipv4_only(host, port, family=0, type=0, proto=0, flags=0):
        return _orig_gai(host, port, socket.AF_INET, type, proto, flags)

    _ipv4_only._toni_ipv4 = True  # type: ignore[attr-defined]
    socket.getaddrinfo = _ipv4_only

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

SOL_RPCS = [
    os.environ.get("SOLANA_RPC") or "",
    "https://api.mainnet-beta.solana.com",
    "https://solana-rpc.publicnode.com",
    "https://solana.publicnode.com",
    "https://rpc.ankr.com/solana",
]
SOL_RPCS = [u for u in SOL_RPCS if u]

# Persistent HTTP — reuse TCP, fail fast on public RPC stalls.
_HTTP = requests.Session()
_HTTP.headers.update({
    "User-Agent": _UA,
    "Accept": "application/json",
})

SOLEND_MARKETS = "https://api.solend.fi/v1/markets/configs"
SOLEND_RESERVES = "https://api.solend.fi/v1/reserves"
SOLEND_OBLIGATION = "https://api.solend.fi/v1/obligation"
SOLEND_OBLIGATIONS_ALT = (
    "https://api.solend.fi/v1/obligations",
    "https://api.save.finance/v1/obligation",
)
JUP_QUOTE = "https://lite-api.jup.ag/swap/v1/quote"
JUP_SWAP = "https://lite-api.jup.ag/swap/v1/swap"
JUP_SWAP_IX = "https://lite-api.jup.ag/swap/v1/swap-instructions"
# Mainnet Jupiter aggregator — send this, never the Arb1111 stub.
JUPITER_V6 = "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4"
JITO_BLOCK_ENGINE = "https://mainnet.block-engine.jito.wtf/api/v1/getTipAccounts"
JITO_BUNDLES = "https://bundles.jito.wtf/api/v1/getTipAccounts"
JITO_BUNDLE_URLS = [
    os.environ.get("JITO_BLOCK_ENGINE") or "",
    "https://mainnet.block-engine.jito.wtf/api/v1/bundles",
    "https://amsterdam.mainnet.block-engine.jito.wtf/api/v1/bundles",
    "https://frankfurt.mainnet.block-engine.jito.wtf/api/v1/bundles",
    "https://ny.mainnet.block-engine.jito.wtf/api/v1/bundles",
    "https://tokyo.mainnet.block-engine.jito.wtf/api/v1/bundles",
]
JITO_BUNDLE_URLS = [u for u in JITO_BUNDLE_URLS if u]
JITO_MIN_TIP_LAMPORTS = 1000
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
ATA_PROGRAM = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
SYSTEM_PROGRAM = "11111111111111111111111111111111"
COMPUTE_BUDGET_PROGRAM = "ComputeBudget111111111111111111111111111111"
# Solend (Save) instruction tags — token-lending packed enum
SOLEND_IX_REFRESH_RESERVE = 3
SOLEND_IX_REFRESH_OBLIGATION = 7
SOLEND_IX_LIQUIDATE_AND_REDEEM = 17
# Official solend-sdk LendingInstruction tags (not the deprecated ix 13 FlashLoan)
SOLEND_IX_FLASH_BORROW = 19
SOLEND_IX_FLASH_REPAY = 20
SYSVAR_INSTRUCTIONS = "Sysvar1nstructions1111111111111111111111111"
LOOKUP_TABLE_META = 56
# Solend default flashLoanFeeWad 0.3% unless a reserve config overrides
SOLEND_FLASH_FEE_BPS_DEFAULT = 30
# Solend SDK ReserveLayout offsets — verified against mainnet USDC reserve
# BgxfHJD… (619 bytes): fee_receiver@339, supply@75, availableAmount@171.
_RES_MARKET = 10
_RES_MINT = 42
_RES_DECIMALS = 74
_RES_SUPPLY = 75
_RES_PYTH = 107
_RES_SWITCHBOARD = 139
_RES_AVAIL = 171
_RES_COLL_MINT = 227
_RES_COLL_SUPPLY = 267
_RES_FLASH_WAD = 314
_RES_FEE_RECV = 339
# LendingMarket: 5 pubkeys after version/bump (162) + RateLimiter (56)
# → COption<Pubkey> whitelisted_liquidator at 218 (verified len=290).
_MKT_WHITELIST_TAG = 218

# Stub program IDs from solana/programs — never treat as live-ready
STUB_LIQ_PROGRAM = "Liq1111111111111111111111111111111111111112"
STUB_ARB_PROGRAM = "Arb1111111111111111111111111111111111111112"
BPF_LOADERS = (
    "BPFLoaderUpgradeab1e11111111111111111111111",
    "BPFLoader2111111111111111111111111111111111",
    "BPFLoader1111111111111111111111111111111111",
)

# Known program/sys accounts to skip when fishing obligation pubkeys from txs
_SKIP_ACCOUNTS = {
    "11111111111111111111111111111111",
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",
    "ComputeBudget111111111111111111111111111111",
    "SysvarC1ock11111111111111111111111111111111",
    "SysvarRent111111111111111111111111111111111",
    "Sysvar1nstructions1111111111111111111111111",
    "So1endDq2YkqhipRh3WViPa8hdiSpxWy6z3Z6tMCpAo",
    "4UpD2fh7xH3VP9QQaXtsS1YY3bxzWhtfpks7FatyKvdY",  # Solend oracles / pyth-ish
}

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
MINT_JUP = "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN"
MINT_JTO = "jtojtomepa8beP8AuQc6eXt5FriJwfFMwQx2v2f9mCL"
MINT_WETH = "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs"
MINT_CBBTC = "cbbtcf3aa214zXHbiAZQwf4122FBYbeASUMEwKEjQnVR"
MINT_ORCA = "orcaEKTdK7LKz57vaAYr9QeNsVEPfiu6QeMU1kektZE"
MINT_RENDER = "rndrizKT3MK1iimdxRdWusYqGeYnRcuNexjzKWDQsMtQ"

# decimals for size grids
DECIMALS = {
    MINT_SOL: 9, MINT_USDC: 6, MINT_USDT: 6,
    MINT_MSOL: 9, MINT_JITOSOL: 9, MINT_BONK: 5,
    MINT_RAY: 6, MINT_WIF: 6, MINT_PYTH: 6,
    MINT_JUP: 6, MINT_JTO: 9, MINT_WETH: 8, MINT_CBBTC: 8,
    MINT_ORCA: 6, MINT_RENDER: 8,
}
SYM = {
    MINT_SOL: "SOL", MINT_USDC: "USDC", MINT_USDT: "USDT",
    MINT_MSOL: "mSOL", MINT_JITOSOL: "JitoSOL", MINT_BONK: "BONK",
    MINT_RAY: "RAY", MINT_WIF: "WIF", MINT_PYTH: "PYTH",
    MINT_JUP: "JUP", MINT_JTO: "JTO", MINT_WETH: "WETH",
    MINT_CBBTC: "cbBTC", MINT_ORCA: "ORCA", MINT_RENDER: "RENDER",
}
STABLE_MINTS = {MINT_USDC, MINT_USDT}
TAIL_MINTS = {MINT_BONK, MINT_WIF, MINT_PYTH, MINT_ORCA, MINT_RENDER}

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

def _arb_pair(a: str, b: str) -> tuple[str, str, str]:
    return (a, b, f"{SYM.get(a, '?')}-{SYM.get(b, '?')}")


# Directed hops for Jupiter fallback. Preferred live set is LST + stables;
# local account quotes cover these first. Tails are not the hot path.
ARB_PAIRS = [
    _arb_pair(MINT_SOL, MINT_USDC), _arb_pair(MINT_USDC, MINT_SOL),
    _arb_pair(MINT_SOL, MINT_USDT), _arb_pair(MINT_USDT, MINT_SOL),
    _arb_pair(MINT_USDC, MINT_USDT), _arb_pair(MINT_USDT, MINT_USDC),
    _arb_pair(MINT_SOL, MINT_MSOL), _arb_pair(MINT_MSOL, MINT_SOL),
    _arb_pair(MINT_SOL, MINT_JITOSOL), _arb_pair(MINT_JITOSOL, MINT_SOL),
]
RAYDIUM_AMM_V4 = "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"
ORCA_WHIRLPOOL = "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc"
Q64 = 1 << 64
WHIRL_FEE_DEN = 1_000_000
# Hardcoded mainnet watch set (pool accounts). Vaults are fetched after decode.
WATCH_POOLS: list[dict[str, str]] = [
    {"pk": "58oQChx4yWmvKdwLLZzBi4ChoCc2fqCUWBkwMihLYQo2",
     "dex": "raydium", "kind": "amm_v4", "pair": "SOL-USDC", "jup": "Raydium"},
    {"pk": "7XawhbbxtsRcQA8KTkHT9f9nc6d69UwqCDh6U5EEbEmX",
     "dex": "raydium", "kind": "amm_v4", "pair": "SOL-USDT", "jup": "Raydium"},
    {"pk": "EGyhb2uLAsRUbRx9dNFBjMVYnFaASWMvD6RE1aEf2LxL",
     "dex": "raydium", "kind": "amm_v4", "pair": "mSOL-SOL", "jup": "Raydium"},
    {"pk": "7TbGqz32RsuwXbXY7EyBCiAnMbJq1gm1wKmfjQjuwoyF",
     "dex": "raydium", "kind": "amm_v4", "pair": "USDT-USDC", "jup": "Raydium"},
    {"pk": "Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE",
     "dex": "orca", "kind": "whirlpool", "pair": "SOL-USDC", "jup": "Whirlpool"},
    {"pk": "HJPjoWUrhoZzkNfRpHuieeFk9WcZWjwy6PBjZ81ngndJ",
     "dex": "orca", "kind": "whirlpool", "pair": "SOL-USDC", "jup": "Whirlpool"},
    {"pk": "FwewVm8u6tFPGewAyHmWAqad9hmF7mvqxK4mJ7iNqqGC",
     "dex": "orca", "kind": "whirlpool", "pair": "SOL-USDT", "jup": "Whirlpool"},
    {"pk": "5zVKUoZcQkFCGcRHVHiyGAJRrZj1by67pW3dmvguFvwd",
     "dex": "orca", "kind": "whirlpool", "pair": "SOL-USDT", "jup": "Whirlpool"},
    {"pk": "HQcY5n2zP6rW74fyFEhWeBd3LnJpBcZechkvJpmdb8cx",
     "dex": "orca", "kind": "whirlpool", "pair": "SOL-mSOL", "jup": "Whirlpool"},
    {"pk": "Hp53XEtt4S8SvPCXarsLSdGfZBuUr5mMmZmX2DRNXQKp",
     "dex": "orca", "kind": "whirlpool", "pair": "SOL-JitoSOL", "jup": "Whirlpool"},
    {"pk": "4fuUiYxTQ6QCrdSq9ouBYcTM7bqSwYTSyLueGZLTy4T4",
     "dex": "orca", "kind": "whirlpool", "pair": "USDC-USDT", "jup": "Whirlpool"},
]
ARB_MAJOR = {
    (MINT_SOL, MINT_USDC), (MINT_USDC, MINT_SOL),
    (MINT_SOL, MINT_USDT), (MINT_USDT, MINT_SOL),
    (MINT_USDC, MINT_USDT), (MINT_USDT, MINT_USDC),
}
# Triangle cycles: A→B→C→A (skip same-pool two-hop dead ends)
ARB_TRIANGLES = [
    (MINT_SOL, MINT_USDC, MINT_USDT, "SOL-USDC-USDT"),
    (MINT_USDC, MINT_SOL, MINT_USDT, "USDC-SOL-USDT"),
]

# Size grid in *native* units of the input mint. Notionals are large enough
# that CU + Jito tip (~cents) cannot dominate a real bps edge.
SIZE_GRID = {
    MINT_SOL: [500_000_000, 2_000_000_000, 5_000_000_000],  # 0.5 / 2 / 5 SOL
    MINT_USDC: [200_000_000, 1_000_000_000, 5_000_000_000],  # 200 / 1k / 5k
    MINT_USDT: [200_000_000, 1_000_000_000, 5_000_000_000],
    MINT_MSOL: [500_000_000, 2_000_000_000],
    MINT_JITOSOL: [500_000_000, 2_000_000_000],
    MINT_JUP: [200_000_000],                         # 200 JUP
    MINT_JTO: [50_000_000_000],                      # 50 JTO (9 dec)
    MINT_WETH: [5_000_000],                          # 0.05 WETH (8 dec)
    MINT_CBBTC: [100_000],                           # 0.001 cbBTC (8 dec)
    MINT_RAY: [50_000_000],                          # 50 RAY
}

# Jupiter program-id-to-label names used to force a *different* venue on the
# return hop (same-pool A→B→A is structurally fee-negative).
_DEX_EXCLUDE_MAX = 6

# Suggested fund amounts (mainnet SOL)
SPONSOR_TARGET_SOL = 0.08   # Jito tips + prio for bundles
BOT_TARGET_SOL = 0.25       # CU + rent; Solend flash sizes liq/arb not inventory

KEYS_DIR = os.path.join(_HERE, "solana", "keys")
SPONSOR_KEY_PATH = os.path.join(KEYS_DIR, "sponsor.json")
BOT_KEY_PATH = os.path.join(KEYS_DIR, "bot.json")


def _hdr():
    return {"User-Agent": _UA, "Content-Type": "application/json",
            "Accept": "application/json"}


def _http_get(url: str, params: dict | None = None, timeout: float = 10.0):
    r = _HTTP.get(url, params=params, headers={"User-Agent": _UA}, timeout=timeout)
    r.raise_for_status()
    return r


def _http_post(url: str, payload: dict, timeout: float = 8.0):
    r = _HTTP.post(url, json=payload, headers=_hdr(), timeout=timeout)
    r.raise_for_status()
    return r


def sol_rpc(method: str, params: list | None = None, timeout: float = 6.0):
    """JSON-RPC against first healthy public Solana endpoint."""
    body = {"jsonrpc": "2.0", "id": 1, "method": method,
            "params": params if params is not None else []}
    last = None
    for url in SOL_RPCS:
        try:
            r = _http_post(url, body, timeout=timeout)
            out = r.json()
            if "error" in out:
                last = out["error"]
                continue
            return out.get("result"), url
        except Exception as e:  # noqa: BLE001
            last = e
            continue
    raise RuntimeError(f"sol rpc {method} failed: {last}")


def latest_blockhash() -> dict[str, Any]:
    """Recent blockhash for user-initiated browser SOL transfers (not a send)."""
    res, url = sol_rpc("getLatestBlockhash", [{"commitment": "confirmed"}])
    val = (res or {}).get("value") if isinstance(res, dict) else {}
    val = val or {}
    return {
        "ok": bool(val.get("blockhash")),
        "blockhash": val.get("blockhash") or "",
        "lastValidBlockHeight": val.get("lastValidBlockHeight"),
        "rpc": url,
    }


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
        r = _http_get(
            "https://api.binance.com/api/v3/ticker/price",
            params={"symbol": "SOLUSDT"}, timeout=5,
        )
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


_QUOTE_FAILS = 0
_QUOTE_LOCK = threading.Lock()
_JUP_GATE = threading.Lock()
_JUP_NEXT = 0.0


def _bump_quote_fail() -> None:
    global _QUOTE_FAILS
    with _QUOTE_LOCK:
        _QUOTE_FAILS += 1


def _quote_fail_count() -> int:
    with _QUOTE_LOCK:
        return int(_QUOTE_FAILS)


def _jup_throttle() -> None:
    """Space lite-API quotes so a 3-wide pool does not 429 the whole cycle."""
    global _JUP_NEXT
    with _JUP_GATE:
        now = time.time()
        wait = _JUP_NEXT - now
        if wait > 0:
            time.sleep(wait)
            now = time.time()
        _JUP_NEXT = now + 0.09


def _jup_quote(input_mint: str, output_mint: str, amount: int,
               slippage_bps: int = 50, *,
               dexes: str | None = None,
               exclude_dexes: str | None = None,
               only_direct: bool = False,
               max_accounts: int | None = None) -> dict | None:
    try:
        params: dict[str, Any] = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": int(amount),
            "slippageBps": int(slippage_bps),
            "restrictIntermediateTokens": "true",
            "onlyDirectRoutes": "true" if only_direct else "false",
            "maxAccounts": int(max_accounts if max_accounts else 40),
        }
        if dexes:
            params["dexes"] = dexes
        if exclude_dexes:
            params["excludeDexes"] = exclude_dexes
        for attempt in range(3):
            _jup_throttle()
            r = _HTTP.get(
                JUP_QUOTE, params=params,
                headers={"User-Agent": _UA}, timeout=8,
            )
            if r.status_code == 200:
                data = r.json()
                if not isinstance(data, dict):
                    _bump_quote_fail()
                    return None
                try:
                    out_amt = int(data.get("outAmount") or 0)
                except (TypeError, ValueError):
                    out_amt = 0
                if out_amt <= 0 or not (data.get("routePlan") or data.get("route")):
                    return None
                return data
            if r.status_code in (429, 502, 503) and attempt < 2:
                time.sleep(0.5 * (attempt + 1))
                continue
            _bump_quote_fail()
            return None
        _bump_quote_fail()
        return None
    except Exception:
        _bump_quote_fail()
        return None


def _quote_illiquid(q: dict | None, tail: bool = False) -> bool:
    """Skip empty / high-impact Jupiter quotes — do not treat as +EV."""
    if not q:
        return True
    if int(q.get("outAmount") or 0) <= 0:
        return True
    try:
        impact = abs(float(q.get("priceImpactPct") or 0))
    except (TypeError, ValueError):
        return True
    cap = 0.05 if tail else 0.015
    return impact > cap


def _route_labels(quote: dict) -> list[str]:
    labels = []
    for hop in (quote.get("routePlan") or []):
        lab = ((hop or {}).get("swapInfo") or {}).get("label") or "jup"
        if lab not in labels:
            labels.append(lab)
    return labels or ["jup"]


def _compact_route_plan(quote: dict | None) -> list[dict]:
    """Trim Jupiter routePlan to label + sizes — never invent hops."""
    hops = []
    if not quote:
        return hops
    for hop in (quote.get("routePlan") or []):
        si = (hop or {}).get("swapInfo") or {}
        lab = si.get("label") or "jup"
        try:
            out_amt = int(si.get("outAmount") or quote.get("outAmount") or 0)
        except (TypeError, ValueError):
            out_amt = 0
        hops.append({
            "label": lab,
            "inMint": si.get("inputMint") or "",
            "outMint": si.get("outputMint") or "",
            "outAmount": out_amt,
        })
    if not hops and quote.get("outAmount"):
        hops.append({
            "label": "jup",
            "inMint": quote.get("inputMint") or "",
            "outMint": quote.get("outputMint") or "",
            "outAmount": int(quote.get("outAmount") or 0),
        })
    return hops


def arb_universe(jobs_n: int | None = None) -> dict[str, Any]:
    toks = sorted({SYM.get(a, "?") for a, _b, _l in ARB_PAIRS}
                  | {SYM.get(b, "?") for _a, b, _l in ARB_PAIRS})
    venues = sorted({w["dex"] for w in WATCH_POOLS} | {"jupiter"})
    return {
        "pairs": len(ARB_PAIRS) + len(ARB_TRIANGLES),
        "jobs": int(jobs_n) if jobs_n is not None else (
            len(WATCH_POOLS) + len(ARB_PAIRS) + len(ARB_TRIANGLES)),
        "venues": venues,
        "tokens": toks,
        "quote_src": "raydium-account+orca-account",
        "paths": sorted({p[2] for p in ARB_PAIRS}),
        "watch_pools": len(WATCH_POOLS),
        "geyser": False,
        "preferred": ["SOL-mSOL", "SOL-JitoSOL", "SOL-USDC", "SOL-USDT",
                      "USDC-USDT"],
    }


def _cu_estimate(hops: int) -> int:
    return min(1_400_000, 200_000 + max(int(hops or 2), 2) * 80_000)


# Jito tip receivers (bundle tip tx when LIVE; metadata-only while sim_only)
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


def _jito_tip_sol(pressure: str | None = None) -> float:
    """Floor tip from env; bump when landing pressure is hot."""
    base = float(os.environ.get("SOL_JITO_TIP_SOL", "0.00001") or 0)
    if pressure == "hot":
        return max(base, 0.00005)
    if pressure == "elevated":
        return max(base, 0.00002)
    return base


def _jito_tip_share() -> float:
    try:
        s = float(os.environ.get("SOL_JITO_TIP_SHARE", "0.15") or 0.15)
    except (TypeError, ValueError):
        s = 0.15
    return min(max(s, 0.0), 0.45)


def _cu_fee_usd(median_ul: int | None, sol_px: float,
                cu_estimate: int = 400_000) -> float:
    med = int(median_ul or 0)
    fee_sol = max(med * cu_estimate / 1e15, 0.000005)
    return fee_sol * max(float(sol_px or 0.0), 0.0)


def _dynamic_jito_lamports(pre_tip_usd: float, sol_px: float,
                           pressure: str | None = None,
                           floor_usd: float = 0.0) -> int:
    """Tip = min(share of expected net, net - floor - eps).

    Remaining net stays ≥ floor after tip. A 15% share must not eat a
    floor-sized ($0.05) trade. Min Jito tip is used only when that still
    leaves net at/above the floor.
    """
    px = max(float(sol_px or 0.0), 1e-9)
    base_sol = _jito_tip_sol(pressure)
    min_lam = max(int(base_sol * 1e9), JITO_MIN_TIP_LAMPORTS)
    pre = float(pre_tip_usd or 0.0)
    floor = max(float(floor_usd or 0.0), 0.0)
    eps = 1e-6
    if pre <= 0:
        return min_lam
    room = pre - floor - eps
    if room <= 0:
        return min_lam
    tip_usd = min(pre * _jito_tip_share(), room)
    min_usd = (min_lam / 1e9) * px
    if tip_usd < min_usd and (pre - min_usd) >= floor + eps:
        tip_usd = min_usd
    lam = int(tip_usd / px * 1e9)
    return max(lam, JITO_MIN_TIP_LAMPORTS)


def _prio_cost_usd(median_ul: int | None, sol_px: float,
                   cu_estimate: int = 400_000,
                   pressure: str | None = None,
                   pre_tip_usd: float | None = None,
                   floor_usd: float = 0.0) -> float:
    """CU priority + Jito tip in USD. Dynamic tip when pre-tip net is known."""
    px = max(float(sol_px or 0.0), 0.0)
    cu_usd = _cu_fee_usd(median_ul, px, cu_estimate)
    if pre_tip_usd is None:
        return cu_usd + _jito_tip_sol(pressure) * px
    jito_lam = _dynamic_jito_lamports(pre_tip_usd, px, pressure, floor_usd)
    return cu_usd + (jito_lam / 1e9) * px


def min_sol_liq_usd() -> float:
    return float(os.environ.get("MIN_SOL_LIQ_USD", "0.50") or 0.50)


def min_sol_arb_usd() -> float:
    return float(os.environ.get("MIN_SOL_ARB_USD", "0.05") or 0.05)


def sol_arb_plus_ev(net_usd, min_usd: float) -> bool:
    """Live SOL arb gate: strictly +EV after costs, at/above the dynamic floor."""
    try:
        net = float(net_usd)
        floor = float(min_usd or 0)
    except (TypeError, ValueError):
        return False
    return net > 0 and net >= floor


def _bonus_pct(raw) -> float:
    """Solend bonus is usually percent (5) or bps (500)."""
    try:
        v = float(raw or 0)
    except (TypeError, ValueError):
        return 0.05
    if v <= 0:
        return 0.05
    if v <= 20:
        return v / 100.0
    if v <= 2000:
        return v / 10_000.0
    return 0.05


def _protocol_fee_share(raw) -> float:
    try:
        v = float(raw or 0)
    except (TypeError, ValueError):
        return 0.0
    if v <= 0:
        return 0.0
    if v <= 1:
        return v
    if v <= 100:
        return v / 100.0
    return min(v / 10_000.0, 0.5)


# Reserve config cache (mint/symbol → bonus, thresh, supplies) filled by watchlist fetch
_RESERVE_INDEX: dict[str, dict] = {}
_LENDING_MARKET: dict[str, str] = {
    "address": "4UpD2fh7xH3VP9QQaXtsS1YY3bxzWhtfpks7FatyKvdY",
    "authority": "",
    "lookup_table": "",
    "whitelist": "",  # "none" | pubkey | "" (unknown — leftover from sim)
}
_INDEX_TS = 0.0
_FLASH_DISABLED: set[str] = set()
_SEEN_SIGS: deque[str] = deque(maxlen=400)
_LANDING_CACHE: dict[str, Any] = {"ts": 0, "hits": []}
# Real obligation pubkeys + last Solend-API hydrates (no fake HF).
_OBLIGATION_KEYS: deque[str] = deque(maxlen=120)
_HYDRATE_CACHE: dict[str, dict] = {}


def _is_pubkey(pk: str | None) -> bool:
    s = (pk or "").strip()
    return len(s) >= 32 and "…" not in s and "..." not in s


def remember_obligation_keys(keys) -> None:
    for k in keys or []:
        pk = (k or "").strip()
        if not _is_pubkey(pk):
            continue
        if pk in _SKIP_ACCOUNTS or pk in JITO_TIP_ACCOUNTS:
            continue
        if pk not in _OBLIGATION_KEYS:
            _OBLIGATION_KEYS.append(pk)


def remember_hydrates(rows: list[dict] | None) -> None:
    for o in rows or []:
        pk = o.get("obligation") or o.get("user") or ""
        if not _is_pubkey(pk):
            continue
        if o.get("proxy") or o.get("hf") is None:
            continue
        prev = _HYDRATE_CACHE.get(pk)
        if prev is None or (o.get("hf") is not None):
            row = dict(o)
            row["user"] = pk
            row["obligation"] = pk
            _HYDRATE_CACHE[pk] = row
    if len(_HYDRATE_CACHE) > 80:
        keep = closest_to_stress(50)
        keys = {r.get("obligation") or r.get("user") for r in keep}
        for k in list(_HYDRATE_CACHE):
            if k not in keys:
                _HYDRATE_CACHE.pop(k, None)


def closest_to_stress(n: int = 10) -> list[dict]:
    """Top-N real hydrates nearest HF=1 (lowest HF first). Never uses util proxy."""
    rows = []
    for o in _HYDRATE_CACHE.values():
        if o.get("proxy") or o.get("hf") is None:
            continue
        rows.append(_watch_row(o))
    rows.sort(key=lambda o: (
        o.get("hf") is None, o.get("hf") or 99,
        -(float(o.get("debt_usd") or 0)),
    ))
    return rows[: max(1, int(n))]


def _watch_row(o: dict) -> dict:
    pk = o.get("obligation") or o.get("user") or ""
    hf = o.get("hf")
    return {
        "user": pk,
        "obligation": pk,
        "hf": hf,
        "coll_usd": o.get("coll_usd"),
        "debt_usd": o.get("debt_usd"),
        "coll_sym": o.get("coll_sym") or o.get("collateral_sym"),
        "debt_sym": o.get("debt_sym"),
        "collateral_sym": o.get("collateral_sym") or o.get("coll_sym"),
        "urgency": _hf_urg_label(hf),
        "source": o.get("source") or "hydrate",
        "proxy": False,
        "note": o.get("note") or "obligation hydrate",
    }


def _hf_urg_label(hf) -> str:
    try:
        h = float(hf)
    except (TypeError, ValueError):
        return "—"
    if h < 1.0:
        return "liq"
    if h < 1.05:
        return "hot"
    if h < 1.1:
        return "warm"
    if h < 1.25:
        return "ok"
    return "safe"


def _dust_obligation(o: dict, min_debt_usd: float = 10.0) -> bool:
    try:
        debt = float(o.get("debt_usd") or 0)
        coll = float(o.get("coll_usd") or 0)
    except (TypeError, ValueError):
        return True
    return debt < min_debt_usd and coll < min_debt_usd


def _flash_fee_bps_from_cfg(cfg: dict | None, prev: dict | None = None) -> int:
    """Solend flash fee in bps. Use reserve config wad when present; else 30."""
    fees = (cfg or {}).get("fees") if isinstance(cfg, dict) else None
    wad = None
    if isinstance(fees, dict):
        wad = fees.get("flashLoanFeeWad") or fees.get("flash_loan_fee_wad")
    if wad is None and isinstance(cfg, dict):
        wad = cfg.get("flashLoanFeeWad") or cfg.get("flash_loan_fee_wad")
    if wad is not None:
        try:
            return max(0, int(round(float(wad) / 1e18 * 10_000)))
        except (TypeError, ValueError):
            pass
    if isinstance(cfg, dict) and cfg.get("flash_loan_fee_bps") is not None:
        try:
            return max(0, int(cfg.get("flash_loan_fee_bps")))
        except (TypeError, ValueError):
            pass
    if prev and prev.get("flash_loan_fee_bps") is not None:
        try:
            return max(0, int(prev.get("flash_loan_fee_bps")))
        except (TypeError, ValueError):
            pass
    return SOLEND_FLASH_FEE_BPS_DEFAULT


def _flash_fee_bps(cfg: dict | None) -> int:
    return _flash_fee_bps_from_cfg(cfg or {}, None)


def _drop_placeholder_pk(pk: str | None) -> str:
    """Drop System Program placeholders. Keep Solend's nu1111… oracle stub."""
    s = (pk or "").strip()
    if not _is_pubkey(s):
        return ""
    if s == SYSTEM_PROGRAM:
        return ""
    return s


def _ensure_solend_index() -> bool:
    """Prefetch Solend markets so arb flash is not waiting on the watchlist loop."""
    global _INDEX_TS
    if _RESERVE_INDEX and _LENDING_MARKET.get("address") and (
            time.time() - _INDEX_TS) < 180:
        return True
    try:
        fetch_solend_watchlist()
    except Exception:
        pass
    return bool(_RESERVE_INDEX)


def _merge_reserve_row(keys: list[str], patch: dict) -> dict:
    """Fill blanks from a decoded/API row. Never overwrite a real remaining."""
    prev: dict = {}
    for k in keys:
        if k and k in _RESERVE_INDEX:
            prev = dict(_RESERVE_INDEX[k])
            break
    row = dict(prev)
    for k, v in (patch or {}).items():
        if k == "extra_oracle":
            v = _drop_placeholder_pk(v)
        if v in (None, ""):
            continue
        if k == "available_amount":
            row[k] = v
            continue
        if k == "flash_loan_fee_bps" and row.get(k) in (
                None, "", SOLEND_FLASH_FEE_BPS_DEFAULT):
            row[k] = v
            continue
        if not row.get(k):
            row[k] = v
    addr = row.get("address") or (keys[0] if keys else "")
    mint = row.get("mint") or ""
    if mint:
        _RESERVE_INDEX[mint] = row
    if addr:
        _RESERVE_INDEX[addr] = row
    if row.get("symbol"):
        _RESERVE_INDEX[row["symbol"]] = row
        _RESERVE_INDEX[str(row["symbol"]).upper()] = row
    return row


def _parse_reserve_account(pk: str, raw: bytes) -> dict | None:
    """Decode a Solend reserve. Offsets from SDK layout, live-checked on USDC."""
    if not raw or len(raw) < _RES_FEE_RECV + 32 or raw[0] == 0:
        return None
    try:
        from solders.pubkey import Pubkey
        market = str(Pubkey.from_bytes(raw[_RES_MARKET:_RES_MARKET + 32]))
        mint = str(Pubkey.from_bytes(raw[_RES_MINT:_RES_MINT + 32]))
        supply = str(Pubkey.from_bytes(raw[_RES_SUPPLY:_RES_SUPPLY + 32]))
        pyth = str(Pubkey.from_bytes(raw[_RES_PYTH:_RES_PYTH + 32]))
        switchboard = str(Pubkey.from_bytes(
            raw[_RES_SWITCHBOARD:_RES_SWITCHBOARD + 32]))
        coll_mint = str(Pubkey.from_bytes(
            raw[_RES_COLL_MINT:_RES_COLL_MINT + 32]))
        coll_supply = str(Pubkey.from_bytes(
            raw[_RES_COLL_SUPPLY:_RES_COLL_SUPPLY + 32]))
        fee_recv = str(Pubkey.from_bytes(
            raw[_RES_FEE_RECV:_RES_FEE_RECV + 32]))
        avail = int.from_bytes(raw[_RES_AVAIL:_RES_AVAIL + 8], "little")
        wad = int.from_bytes(raw[_RES_FLASH_WAD:_RES_FLASH_WAD + 8], "little")
        decimals = int(raw[_RES_DECIMALS])
    except Exception:
        return None
    if not (_is_pubkey(market) and _is_pubkey(mint) and _is_pubkey(supply)):
        return None
    prev = _RESERVE_INDEX.get(pk) or _RESERVE_INDEX.get(mint) or {}
    if prev.get("mint") and prev["mint"] != mint:
        return None  # layout mismatch — do not invent
    if prev.get("liquidity_supply") and prev["liquidity_supply"] != supply:
        return None
    if prev.get("fee_receiver") and prev["fee_receiver"] != fee_recv:
        return None
    bps = max(0, int(round(wad / 1e18 * 10_000))) if wad else (
        prev.get("flash_loan_fee_bps") or SOLEND_FLASH_FEE_BPS_DEFAULT)
    return {
        "address": pk,
        "mint": mint,
        "decimals": decimals,
        "lending_market": market,
        "liquidity_supply": supply,
        "pyth": pyth,
        "switchboard": switchboard,
        "available_amount": avail,
        "collateral_mint": coll_mint,
        "collateral_supply": coll_supply,
        "fee_receiver": fee_recv,
        "flash_loan_fee_bps": bps,
    }


def _hydrate_reserves_onchain(pks: list[str] | None) -> None:
    need = []
    seen: set[str] = set()
    for p in pks or []:
        if not _is_pubkey(p) or p in seen:
            continue
        seen.add(p)
        row = _RESERVE_INDEX.get(p) or {}
        if (row.get("liquidity_supply") and row.get("fee_receiver")
                and row.get("available_amount") is not None):
            continue
        need.append(p)
    if not need:
        return
    accs = _get_multiple_accounts(need)
    for p in need:
        acc = accs.get(p)
        if not acc or (acc.get("owner") or "") != SOLEND_PROGRAM:
            continue
        parsed = _parse_reserve_account(p, _acc_bytes(acc))
        if parsed:
            _merge_reserve_row([p, parsed.get("mint") or ""], parsed)
            if parsed.get("lending_market") and not _LENDING_MARKET.get(
                    "address"):
                _LENDING_MARKET["address"] = parsed["lending_market"]


def _hydrate_market_onchain(market: str | None = None) -> None:
    """Decode COption whitelist from the lending-market account. No invented PDA."""
    mk = (market or _LENDING_MARKET.get("address") or "").strip()
    if not _is_pubkey(mk):
        return
    val = _account_info(mk)
    if not val or (val.get("owner") or "") != SOLEND_PROGRAM:
        return
    raw = _acc_bytes(val)
    if len(raw) < _MKT_WHITELIST_TAG + 36:
        return
    tag = int.from_bytes(raw[_MKT_WHITELIST_TAG:_MKT_WHITELIST_TAG + 4],
                         "little")
    if tag == 0:
        _LENDING_MARKET["whitelist"] = "none"
        return
    if tag != 1:
        return
    try:
        from solders.pubkey import Pubkey
        pk = str(Pubkey.from_bytes(
            raw[_MKT_WHITELIST_TAG + 4:_MKT_WHITELIST_TAG + 36]))
    except Exception:
        return
    if _is_pubkey(pk) and pk != SYSTEM_PROGRAM:
        _LENDING_MARKET["whitelist"] = pk


def _index_reserve(item: dict, picked: dict | None = None) -> None:
    res = (item or {}).get("reserve") or item or {}
    liq = res.get("liquidity") or {}
    cfg = res.get("config") or {}
    mint = liq.get("mintPubkey") or (picked or {}).get("mint") or ""
    sym = ((picked or {}).get("symbol")
           or (liq.get("mint") or {}).get("symbol")
           or "")
    addr = (res.get("pubkey") or res.get("address")
            or (picked or {}).get("address") or "")
    prev = (_RESERVE_INDEX.get(addr) or _RESERVE_INDEX.get(mint)
            or picked or {})
    row = {
        "mint": mint or prev.get("mint"),
        "symbol": sym or prev.get("symbol"),
        "address": addr or prev.get("address"),
        "liq_bonus": cfg.get("liquidationBonus") or prev.get("liq_bonus"),
        "liq_thresh": cfg.get("liquidationThreshold") or prev.get("liq_thresh"),
        "liq_protocol_fee": cfg.get("liquidationProtocolFee") or prev.get("liq_protocol_fee"),
        "ltv": cfg.get("loanToValueRatio") or prev.get("ltv"),
        "decimals": (picked or {}).get("decimals") or liq.get("mintDecimals") or prev.get("decimals"),
        "liquidity_supply": (liq.get("supplyPubkey") or prev.get("liquidity_supply")
                             or (picked or {}).get("liquidity_supply")),
        "collateral_mint": ((res.get("collateral") or {}).get("mintPubkey")
                            or prev.get("collateral_mint")
                            or (picked or {}).get("collateral_mint")),
        "collateral_supply": ((res.get("collateral") or {}).get("supplyPubkey")
                              or prev.get("collateral_supply")
                              or (picked or {}).get("collateral_supply")),
        "fee_receiver": (cfg.get("feeReceiver") or prev.get("fee_receiver")
                         or (picked or {}).get("fee_receiver")),
        "pyth": liq.get("pythOracle") or prev.get("pyth") or (picked or {}).get("pyth"),
        "switchboard": (liq.get("switchboardOracle") or prev.get("switchboard")
                        or (picked or {}).get("switchboard")),
        "extra_oracle": _drop_placeholder_pk(
            liq.get("extraOracle") or cfg.get("extraOracle")
            or prev.get("extra_oracle") or (picked or {}).get("extra_oracle")),
        "available_amount": (
            int(liq.get("availableAmount") or 0)
            if liq.get("availableAmount") not in (None, "")
            else prev.get("available_amount")),
        "flash_loan_fee_bps": _flash_fee_bps_from_cfg(cfg, prev),
    }
    if mint:
        _RESERVE_INDEX[mint] = row
    if addr:
        _RESERVE_INDEX[addr] = row
    if row.get("symbol"):
        _RESERVE_INDEX[row["symbol"].upper()] = row
        _RESERVE_INDEX[row["symbol"]] = row


def _reserve_for(opp: dict) -> dict:
    for key in (opp.get("coll_mint"), opp.get("debt_mint"),
                (opp.get("collateral_sym") or "").upper(),
                (opp.get("debt_sym") or "").upper(),
                (opp.get("symbol") or "").upper()):
        if key and key in _RESERVE_INDEX:
            return _RESERVE_INDEX[key]
    return {}


def score_liq_profit(opp: dict, sol_px: float | None = None,
                     priority_median: int | None = None,
                     pressure: str | None = None) -> dict:
    """Net liquidation profit after bonus, protocol cut, slip, CU, Jito tip.

    Does not invent borrower HF. Uses reserve bonus when known; otherwise 5%.
    Jupiter quote is used when both mints + a native seize size are present.
    """
    out = dict(opp)
    borrowed = float(out.get("debt_usd") or 0)
    deposited = float(out.get("coll_usd") or 0)
    hf = out.get("hf")
    cfg = _reserve_for(out)
    debt_cfg = (
        _RESERVE_INDEX.get(out.get("debt_reserve") or "")
        or _RESERVE_INDEX.get(out.get("debt_mint") or "")
        or cfg
    )
    bonus = _bonus_pct(cfg.get("liq_bonus") if cfg else 5)
    proto = _protocol_fee_share(cfg.get("liq_protocol_fee") if cfg else 0)
    # Close factor: 50% near the line, 100% when deeply underwater
    if hf is not None and hf > 0.92:
        close = 0.5
    elif hf is not None and hf > 0.80:
        close = 0.75
    else:
        close = 1.0
    repay = 0.0
    if borrowed > 0:
        cap = deposited / max(1.0 + bonus, 1.01) if deposited > 0 else borrowed
        repay = min(borrowed * close, cap, borrowed)
    seized = repay * (1.0 + bonus * (1.0 - proto)) if repay else 0.0
    slip_usd = 0.0
    jup_used = False
    debt_mint = out.get("debt_mint") or ""
    coll_mint = out.get("coll_mint") or ""
    debt_amt = int(out.get("debt_amount") or 0)
    if (coll_mint and debt_mint and coll_mint != debt_mint and debt_amt > 0
            and repay > 0):
        # Approximate seize native from debt native * (1+bonus)
        seize_native = int(debt_amt * (1.0 + bonus * (1.0 - proto)))
        q = _jup_quote(coll_mint, debt_mint, max(seize_native, 1),
                       slippage_bps=50)
        if q:
            jup_used = True
            out_amt = int(q.get("outAmount") or 0)
            dec_d = DECIMALS.get(debt_mint, 6)
            out_usd = out_amt / (10 ** dec_d)
            # repay native ≈ debt_amount * close
            repay_native_usd = repay
            slip_usd = max(0.0, repay_native_usd - out_usd + (seized - repay))
            impact = abs(float(q.get("priceImpactPct") or 0))
            slip_usd = max(slip_usd, seized * min(impact, 0.08))
    if not jup_used:
        slip_usd = seized * 0.003  # 30 bps conservative if no route
    px = float(sol_px or 0.0)
    flash_bps = _flash_fee_bps(debt_cfg)
    flash_usd = repay * (flash_bps / 10_000.0) if repay else 0.0
    cu = int(out.get("compute_units") or 900_000)
    cu_usd = _cu_fee_usd(priority_median, px, cu)
    gross = seized - repay
    pre_tip = gross - slip_usd - cu_usd - flash_usd
    floor = min_sol_liq_usd()
    jito_lam = _dynamic_jito_lamports(pre_tip, px, pressure, floor)
    jito_usd = (jito_lam / 1e9) * px
    fee_usd = cu_usd + jito_usd + flash_usd
    net = pre_tip - jito_usd
    out["jito_tip_lamports"] = jito_lam
    out["jito_tip_usd"] = round(jito_usd, 6)
    floor = min_sol_liq_usd()
    out["liq_bonus_pct"] = round(bonus * 100.0, 3)
    out["liq_protocol_fee_pct"] = round(proto * 100.0, 3)
    out["close_factor"] = close
    out["repay_usd"] = round(repay, 4)
    out["seized_usd"] = round(seized, 4)
    out["gross_usd"] = round(gross, 4)
    out["slip_usd"] = round(slip_usd, 6)
    out["gas_usd"] = round(fee_usd, 6)
    out["profit_usd"] = round(net, 4)
    out["net_usd"] = round(net, 4)
    out["actionable"] = bool(
        hf is not None and hf < 1.0 and net > floor
    )
    out["edge"] = bool(
        (hf is not None and hf < 1.0)
        or str(out.get("collateral_sym") or "").upper() in (
            "BONK", "WIF", "PYTH", "RAY", "CBBTC")
    )
    out["jup_priced"] = jup_used
    out["min_floor_usd"] = floor
    out["sol_px"] = px
    out["flash_fee_bps"] = flash_bps
    out["flash_fee_usd"] = round(flash_usd, 6)
    out["flash_fee_src"] = "solend"
    out["flash"] = True
    out["compute_units"] = cu
    return out


def build_arb_plan(row: dict, priority_median: int | None = None,
                   pressure: str | None = None) -> dict:
    """Broadcast plan — LIVE sends Jupiter swap txs + Jito tip (not Arb1111)."""
    w = current_wallets()
    hops = int(row.get("hops") or 2)
    cu = int(row.get("compute_units") or _cu_estimate(hops))
    bot = w.get("bot") or ""
    sponsor = w.get("sponsor") or ""
    tip_lam = int(row.get("jito_tip_lamports") or 0)
    if tip_lam <= 0:
        px = float(row.get("sol_px") or 0.0)
        pre = float(row.get("net_usd") or 0) + _jito_tip_sol(pressure) * px
        tip_lam = _dynamic_jito_lamports(
            pre, px, pressure, float(row.get("min_floor_usd") or min_sol_arb_usd()))
    stub = (os.environ.get("SOL_ARB_PROGRAM") or "").strip()
    if stub and (stub in (STUB_ARB_PROGRAM,) or stub.startswith("Arb1111")):
        stub = ""
    return {
        "kind": "arb",
        "mode": "dry-run",
        "scan_mode": row.get("quote_src") or "local+jup",
        "execute": "jupiter-jito",
        "quote_src": row.get("quote_src") or "jupiter",
        "program": JUPITER_V6,
        "arb_stub": stub,
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
        "jito_tip_lamports": tip_lam,
        "jito_bundle": False,
        "venue": row.get("venue"),
        "dexes": row.get("labels") or [],
        "legs": row.get("legs") or [],
        "sol_px": row.get("sol_px"),
        "same_pool": bool(row.get("same_pool")),
        "pressure": pressure or row.get("pressure"),
        "min_floor_usd": row.get("min_floor_usd") or min_sol_arb_usd(),
        "use_flash": bool(row.get("use_flash")),
        "flash_fee_bps": row.get("flash_fee_bps") or 0,
        "note": "Jupiter /swap + Jito when armed; Solend flash sizes large +EV",
    }


def build_liq_plan(opp: dict, priority_median: int | None = None,
                   pressure: str | None = None) -> dict:
    w = current_wallets()
    bot = w.get("bot") or ""
    sponsor = w.get("sponsor") or ""
    tip_lam = int(opp.get("jito_tip_lamports") or 0)
    if tip_lam <= 0:
        px = float(opp.get("sol_px") or 0.0)
        pre = float(opp.get("profit_usd") or opp.get("net_usd") or 0)
        tip_lam = _dynamic_jito_lamports(pre, px, pressure, min_sol_liq_usd())
    _ensure_solend_index()
    repay_r = (opp.get("debt_reserve") or "").strip()
    withdraw_r = (opp.get("coll_reserve") or "").strip()
    _hydrate_reserves_onchain([repay_r, withdraw_r])
    gaps = _liq_account_gaps(opp)
    stub = (os.environ.get("SOL_LIQ_PROGRAM") or "").strip()
    if stub and (stub in (STUB_LIQ_PROGRAM,) or stub.startswith("Liq1111")):
        stub = ""
    return {
        "kind": "liq",
        "mode": "dry-run",
        "scan_mode": opp.get("source") or "solend-landing",
        "execute": "solend-jito",
        "program": SOLEND_PROGRAM,
        "liq_stub": stub,
        "obligation": opp.get("obligation") or opp.get("user"),
        "owner": opp.get("owner") or opp.get("user"),
        "repay_mint": opp.get("debt_mint") or "",
        "withdraw_mint": opp.get("coll_mint") or "",
        "debt_reserve": opp.get("debt_reserve") or "",
        "coll_reserve": opp.get("coll_reserve") or "",
        "deposit_reserves": opp.get("deposit_reserves") or [],
        "borrow_reserves": opp.get("borrow_reserves") or [],
        "debt_amount": opp.get("debt_amount") or 0,
        "expected_profit_usd": opp.get("profit_usd") or opp.get("net_usd"),
        "hf": opp.get("hf"),
        "repay_usd": opp.get("repay_usd"),
        "seized_usd": opp.get("seized_usd"),
        "slip_usd": opp.get("slip_usd"),
        "gas_usd": opp.get("gas_usd"),
        "liq_bonus_pct": opp.get("liq_bonus_pct"),
        "close_factor": opp.get("close_factor"),
        "flash": True,
        "flash_fee_bps": opp.get("flash_fee_bps"),
        "flash_fee_usd": opp.get("flash_fee_usd"),
        "flash_fee_src": "solend",
        "borrow_slots": opp.get("borrow_slots") or [],
        "leftover": gaps,
        "contested": bool(opp.get("contested")),
        "source": opp.get("source"),
        "priority_fee_ul": priority_median,
        "compute_units": int(opp.get("compute_units") or 900_000),
        "fee_payer": bot,
        "tip_payer": sponsor,
        "jito_tip_account": _jito_tip_account(bot or sponsor),
        "jito_tip_lamports": tip_lam,
        "jito_bundle": False,
        "sol_px": opp.get("sol_px"),
        "pressure": pressure,
        "account_gaps": gaps,
        "note": (
            "Solend flash borrow + liquidate + Jito when armed"
            if not gaps else
            "liq plan — remaining accounts: " + ", ".join(gaps)
        ),
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
        "hf_note": ("borrower HF from on-chain Solend obligation accounts; "
                    "reserve util is not used as HF"),
    }
    global _INDEX_TS
    try:
        r = _http_get(
            SOLEND_MARKETS,
            params={"scope": "all", "deployment": "production"},
            timeout=18,
        )
        markets = r.json()
        main = next((m for m in markets
                     if m.get("isPrimary") or m.get("name") == "main"), None)
        if not main:
            out["error"] = "no primary Solend market"
            return out
        out["market"] = main.get("address")
        if main.get("address"):
            _LENDING_MARKET["address"] = main.get("address") or ""
            _LENDING_MARKET["authority"] = main.get("authorityAddress") or ""
            lut = (main.get("lookupTableAddress") or "").strip()
            if _is_pubkey(lut):
                _LENDING_MARKET["lookup_table"] = lut
            _hydrate_market_onchain(main.get("address"))
        picked = []
        for res in main.get("reserves") or []:
            tok = res.get("liquidityToken") or {}
            sym = tok.get("symbol") or ""
            addr = res.get("address") or ""
            mint = tok.get("mint") or ""
            if addr or mint:
                row_cfg = {
                    "symbol": sym, "mint": mint, "address": addr,
                    "decimals": tok.get("decimals"),
                    "liq_bonus": (res.get("config") or {}).get("liquidationBonus"),
                    "liq_thresh": (res.get("config") or {}).get("liquidationThreshold"),
                    "liq_protocol_fee": (res.get("config") or {}).get("liquidationProtocolFee"),
                    "pyth": res.get("pythOracle") or "",
                    "switchboard": res.get("switchboardOracle") or "",
                    "extra_oracle": _drop_placeholder_pk(
                        res.get("extraOracle") or ""),
                    "collateral_mint": res.get("collateralMintAddress") or "",
                    "collateral_supply": res.get("collateralSupplyAddress") or "",
                    "liquidity_supply": res.get("liquidityAddress") or "",
                    "fee_receiver": res.get("liquidityFeeReceiverAddress") or "",
                    "flash_loan_fee_bps": _flash_fee_bps_from_cfg(
                        res.get("config") or {}, None),
                }
                _RESERVE_INDEX[addr or mint] = row_cfg
                if mint:
                    _RESERVE_INDEX[mint] = _RESERVE_INDEX[addr or mint]
                if sym:
                    _RESERVE_INDEX[sym] = _RESERVE_INDEX[addr or mint]
                    _RESERVE_INDEX[sym.upper()] = _RESERVE_INDEX[addr or mint]
            if sym in WATCH_SYMS:
                picked.append({
                    "symbol": sym,
                    "address": res.get("address"),
                    "mint": tok.get("mint"),
                    "decimals": tok.get("decimals"),
                })
        out["reserves_n"] = len(main.get("reserves") or [])
        _INDEX_TS = time.time()
        if not picked:
            out["ok"] = True
            out["error"] = "no watched reserves"
            return out
        ids = ",".join(p["address"] for p in picked if p.get("address"))
        rr = _http_get(SOLEND_RESERVES, params={"ids": ids}, timeout=18)
        by_addr = {}
        for item in (rr.json().get("results") or []):
            res = item.get("reserve") or {}
            mint = ((res.get("liquidity") or {}).get("mintPubkey") or "")
            by_addr[mint] = item
            _index_reserve(item)

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
            # Reserve util is market stress metadata only — never a borrower HF.
            watch.append({
                "mint": p.get("mint"),
                "symbol": p["symbol"],
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
        watch.sort(key=lambda w: -(w.get("util_pct") or 0))
        out["reserves"] = watch
        out["watchlist"] = []  # closest-10 comes from obligation hydrates
        out["ok"] = True
        out["opportunities"] = []
        _INDEX_TS = time.time()
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


def _b58(raw: bytes) -> str:
    import base58  # type: ignore
    return base58.b58encode(raw).decode("ascii")


def _u128_wad(b: bytes) -> float:
    return int.from_bytes(b, "little") / 1e18


# Solend SDK ObligationLayout.span == 1300
_OBL_COLLATERAL_SPAN = 88
_OBL_LIQUIDITY_SPAN = 112


def parse_solend_obligation_bytes(pk: str, raw: bytes) -> dict | None:
    """Decode on-chain Solend obligation (SDK ObligationLayout, 1300 bytes).

    HF = unhealthyBorrowValue / borrowedValue (liquidation when HF < 1).
    Values are WAD (1e18) USD-like market units from the program.
    """
    if not raw or len(raw) < 204:
        return None
    if raw[0] == 0:
        return None
    slot = int.from_bytes(raw[1:9], "little")
    if slot == 0:
        return None
    try:
        owner = _b58(raw[42:74])
        deposited = _u128_wad(raw[74:90])
        borrowed = _u128_wad(raw[90:106])
        unhealthy = _u128_wad(raw[122:138])
        deposits_len = raw[202]
        borrows_len = raw[203]
    except Exception:
        return None
    if borrowed <= 0:
        return None
    # WAD USD sanity — garbage layouts (reserves / other program accounts) explode.
    if deposited > 5e11 or borrowed > 5e11 or deposited < 0 or borrowed < 0:
        return None
    if unhealthy > 0:
        hf = round(unhealthy / borrowed, 4)
    elif deposited > 0:
        hf = round(deposited / borrowed, 4)
    else:
        return None
    if hf <= 0 or hf > 500:
        return None
    data = raw[204:]
    coll_sym = "?"
    debt_sym = "?"
    coll_mint = ""
    debt_mint = ""
    coll_reserve = ""
    debt_reserve = ""
    deposit_reserves: list[str] = []
    borrow_reserves: list[str] = []
    borrow_slots: list[dict] = []
    debt_amount = 0
    off = 0
    for _ in range(min(int(deposits_len), 9)):
        chunk = data[off:off + _OBL_COLLATERAL_SPAN]
        off += _OBL_COLLATERAL_SPAN
        if len(chunk) < _OBL_COLLATERAL_SPAN:
            break
        res_pk = _b58(chunk[0:32])
        if res_pk:
            deposit_reserves.append(res_pk)
        cfg = _RESERVE_INDEX.get(res_pk) or {}
        if cfg.get("symbol") and coll_sym == "?":
            coll_sym = cfg["symbol"]
            coll_mint = cfg.get("mint") or ""
            coll_reserve = res_pk
    if not coll_reserve and deposit_reserves:
        coll_reserve = deposit_reserves[0]
        cfg = _RESERVE_INDEX.get(coll_reserve) or {}
        coll_mint = coll_mint or cfg.get("mint") or ""
        coll_sym = coll_sym if coll_sym != "?" else (cfg.get("symbol") or "?")
    for _ in range(min(int(borrows_len), 9)):
        chunk = data[off:off + _OBL_LIQUIDITY_SPAN]
        off += _OBL_LIQUIDITY_SPAN
        if len(chunk) < _OBL_LIQUIDITY_SPAN:
            break
        res_pk = _b58(chunk[0:32])
        if res_pk:
            borrow_reserves.append(res_pk)
        cfg = _RESERVE_INDEX.get(res_pk) or {}
        wads = int.from_bytes(chunk[48:64], "little")
        amt = int(wads / 1e18) if wads else 0
        borrow_slots.append({
            "reserve": res_pk,
            "mint": cfg.get("mint") or "",
            "symbol": cfg.get("symbol") or "",
            "amount": amt,
        })
        if (cfg.get("symbol") or res_pk) and debt_sym == "?":
            debt_sym = cfg.get("symbol") or debt_sym
            debt_mint = cfg.get("mint") or debt_mint
            debt_reserve = res_pk
            debt_amount = amt
    if not debt_reserve and borrow_reserves:
        debt_reserve = borrow_reserves[0]
        cfg = _RESERVE_INDEX.get(debt_reserve) or {}
        debt_mint = debt_mint or cfg.get("mint") or ""
        debt_sym = debt_sym if debt_sym != "?" else (cfg.get("symbol") or "?")
    row = {
        "user": owner or pk,
        "owner": owner or pk,
        "obligation": pk,
        "hf": hf,
        "collateral_sym": coll_sym,
        "coll_sym": coll_sym,
        "debt_sym": debt_sym,
        "coll_mint": coll_mint,
        "debt_mint": debt_mint,
        "coll_reserve": coll_reserve,
        "debt_reserve": debt_reserve,
        "deposit_reserves": deposit_reserves,
        "borrow_reserves": borrow_reserves,
        "borrow_slots": borrow_slots,
        "lending_market": _LENDING_MARKET.get("address") or "",
        "coll_usd": round(deposited, 4) if deposited else None,
        "debt_usd": round(borrowed, 4) if borrowed else None,
        "debt_amount": debt_amount,
        "urgency": _hf_urg_label(hf),
        "note": "on-chain obligation",
        "proxy": False,
        "source": "rpc",
        "kind": "liq",
        "solscan": f"https://solscan.io/account/{pk}",
    }
    return score_liq_profit(row)


def _hydrate_from_rpc(pubkeys: list[str], source: str = "rpc") -> list[dict]:
    """getMultipleAccounts + Solend layout decode. Skips non-obligation accounts."""
    uniq = [p for p in pubkeys if _is_pubkey(p)]
    opps = []
    for i in range(0, min(len(uniq), 40), 20):
        chunk = uniq[i:i + 20]
        try:
            res, _ = sol_rpc(
                "getMultipleAccounts",
                [chunk, {"encoding": "base64"}],
                timeout=12,
            )
        except Exception:
            continue
        vals = (res or {}).get("value") if isinstance(res, dict) else (res or [])
        for pk, acc in zip(chunk, vals or []):
            if not isinstance(acc, dict):
                continue
            owner = acc.get("owner") or ""
            if owner != SOLEND_PROGRAM:
                continue
            data = acc.get("data")
            raw = b""
            if isinstance(data, list) and data:
                import base64
                try:
                    raw = base64.b64decode(data[0])
                except Exception:
                    continue
            parsed = parse_solend_obligation_bytes(pk, raw)
            if not parsed:
                continue
            parsed["source"] = source
            parsed["kind"] = "liq"
            opps.append(parsed)
    remember_obligation_keys([o.get("obligation") for o in opps])
    remember_hydrates(opps)
    return opps


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
        "watch": [], "sample_pubkeys": [], "error": None, "method": None,
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

    remembered = [k for k in list(_OBLIGATION_KEYS) if _is_pubkey(k)]
    if remembered:
        seen = set(accounts)
        for k in remembered:
            if k not in seen:
                accounts.append(k)
                seen.add(k)
        if not method:
            method = f"cached landing keys={len(remembered)}"

    out["probed"] = len(accounts)
    out["sample_pubkeys"] = accounts[:12]
    out["method"] = method
    remember_obligation_keys(accounts)

    if not accounts:
        watch = closest_to_stress(10)
        out["ok"] = True  # probe ran; nothing new
        out["error"] = last_err
        out["watch"] = watch
        out["note"] = (
            "getProgramAccounts blocked/empty on public RPC — "
            "waiting for Solend landing sigs to seed obligation hydrates. "
            "Set SOLANA_RPC to a GPA-capable provider for a full sweep."
        )
        return out

    # On-chain decode (Solend HTTP obligation API 404s; layout is public)
    opps = _hydrate_from_rpc(accounts[:max_accounts], source="gpa")
    out["hydrated"] = len(opps)
    opps.sort(key=lambda o: (o.get("hf") is None, o.get("hf") or 99,
                             -(o.get("profit_usd") or 0)))
    liq = [o for o in opps
           if o.get("hf") is not None and o["hf"] < 1.0
           and not _dust_obligation(o)]
    watch = closest_to_stress(10)
    if not watch:
        watch = [_watch_row(o) for o in opps if o.get("hf") is not None][:10]
    out["opportunities"] = liq[:25]
    out["watch"] = watch
    out["ok"] = True
    out["note"] = (
        f"probed {len(accounts)} accounts; hydrated {out['hydrated']} on-chain; "
        f"liquidatable (HF<1)={len(liq)}; closest={len(watch)}."
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

    borrows = obl.get("borrows") or item.get("borrows") or []
    deposits = obl.get("deposits") or item.get("deposits") or []
    debt_sym = "?"
    coll_sym = "?"
    debt_mint = ""
    coll_mint = ""
    debt_amount = 0
    if borrows and isinstance(borrows[0], dict):
        debt_sym = (borrows[0].get("symbol")
                    or (borrows[0].get("mintAddress") or "")[:4] or "?")
        debt_mint = (borrows[0].get("mintAddress")
                     or borrows[0].get("mint") or "")
        try:
            debt_amount = int(borrows[0].get("amount")
                              or borrows[0].get("borrowedAmount") or 0)
        except (TypeError, ValueError):
            debt_amount = 0
    if deposits and isinstance(deposits[0], dict):
        coll_sym = (deposits[0].get("symbol")
                    or (deposits[0].get("mintAddress") or "")[:4] or "?")
        coll_mint = (deposits[0].get("mintAddress")
                     or deposits[0].get("mint") or "")

    hf = None
    if borrow_limit > 0 and borrowed > 0:
        # Solend: liquidatable when borrowed >= unhealthyBorrowValue/borrowLimit
        hf = round(borrow_limit / borrowed, 4)
    elif deposited > 0 and borrowed > 0:
        thresh = 0.85
        cfg = _reserve_for({"coll_mint": coll_mint, "collateral_sym": coll_sym})
        raw_th = (cfg or {}).get("liq_thresh")
        try:
            t = float(raw_th or 0)
            if 0 < t <= 1:
                thresh = t
            elif 1 < t <= 100:
                thresh = t / 100.0
            elif t > 100:
                thresh = t / 10_000.0
        except (TypeError, ValueError):
            pass
        hf = round((deposited * thresh) / borrowed, 4)

    if hf is None and borrowed <= 0:
        return None

    row = {
        "user": addr,
        "obligation": addr,
        "hf": hf,
        "collateral_sym": coll_sym,
        "coll_sym": coll_sym,
        "debt_sym": debt_sym,
        "coll_mint": coll_mint,
        "debt_mint": debt_mint,
        "coll_usd": deposited or None,
        "debt_usd": borrowed or None,
        "debt_amount": debt_amount,
        "urgency": _hf_urg_label(hf),
        "note": "obligation hydrate",
        "proxy": False,
        "source": item.get("_source") or "hydrate",
        "kind": "liq",
        "solscan": f"https://solscan.io/account/{addr}" if addr else "",
    }
    return score_liq_profit(row)


def _sizes_for(inp: str, mid: str, amounts_lamports: list[int] | None) -> list[int]:
    if amounts_lamports and inp == MINT_SOL:
        return amounts_lamports
    grid = SIZE_GRID.get(inp) or SIZE_GRID[MINT_SOL]
    major = (inp, mid) in ARB_MAJOR
    if inp == MINT_SOL and mid in TAIL_MINTS:
        return [1_000_000_000]  # 1 SOL — tiny sizes get eaten by tip
    if not major:
        return grid[:1]
    return grid


def _native_usd(mint: str, native: int, sol_px: float) -> float:
    dec = DECIMALS.get(mint, 9)
    amt = native / (10 ** dec)
    if mint in STABLE_MINTS:
        return amt
    return amt * max(float(sol_px or 0.0), 0.0)


def _slip_bps(inp: str, *mints: str) -> int:
    tail = any(m in TAIL_MINTS for m in (inp, *mints))
    return 30 if tail else 15


def _exclude_csv(labels: list[str]) -> str:
    uniq = []
    for lab in labels:
        if lab and lab not in uniq and lab != "jup":
            uniq.append(lab)
        if len(uniq) >= _DEX_EXCLUDE_MAX:
            break
    return ",".join(uniq)


def _arb_legs_from_quotes(inp: str, quotes: list[dict],
                          hop_dexes: list[str] | None = None) -> list[dict]:
    """Compact hop descriptors so LIVE can re-quote + /swap immediately."""
    legs = []
    seen: list[str] = []
    for i, q in enumerate(quotes or []):
        excl = _exclude_csv(seen) or None
        try:
            amt = int(q.get("inAmount") or 0)
        except (TypeError, ValueError):
            amt = 0
        dex = ""
        if hop_dexes and i < len(hop_dexes):
            dex = hop_dexes[i] or ""
        legs.append({
            "input_mint": q.get("inputMint") or (inp if i == 0 else ""),
            "output_mint": q.get("outputMint") or "",
            "amount": amt,
            "exclude": excl,
            "only_direct": True,
            "dexes": dex or None,
            "labels": _route_labels(q)[:6],
        })
        seen.extend(_route_labels(q))
    return legs


def _pack_arb_row(
    *, inp: str, hops_mints: list[str], amt: int, back: int,
    quotes: list[dict], min_usd: float, sol_px: float,
    priority_median: int | None, pressure: str | None,
    mid_label: str, same_pool: bool, skip_reason: str | None,
    quote_src: str = "jupiter", hop_dexes: list[str] | None = None,
) -> dict:
    labs: list[str] = []
    impact = 0.0
    hops = 0
    for q in quotes:
        for lab in _route_labels(q):
            if lab not in labs:
                labs.append(lab)
        try:
            impact += abs(float(q.get("priceImpactPct") or 0))
        except (TypeError, ValueError):
            pass
        hops += len(q.get("routePlan") or []) or 1
    q_last = quotes[-1]
    back_i = int(back)
    thresh = int(q_last.get("otherAmountThreshold") or back_i)
    notional = _native_usd(inp, amt, sol_px)
    gross_usd = _native_usd(inp, back_i - amt, sol_px)
    # Executable haircut: last-hop threshold, capped at quoted slippage * hops.
    slip_cap = notional * (_slip_bps(inp, *hops_mints) / 10_000.0) * min(hops, 2)
    thresh_hair = _native_usd(inp, max(0, back_i - thresh), sol_px)
    slip_usd = min(thresh_hair if thresh_hair > 0 else slip_cap, slip_cap)
    cu = _cu_estimate(hops)
    cu_usd = _cu_fee_usd(priority_median, sol_px, cu)
    pre_tip = gross_usd - slip_usd - cu_usd
    floor = float(min_usd or 0)
    jito_lam = _dynamic_jito_lamports(pre_tip, sol_px, pressure, floor)
    jito_usd = (jito_lam / 1e9) * max(float(sol_px or 0.0), 0.0)
    fee_usd = cu_usd + jito_usd
    net_usd = pre_tip - jito_usd
    flash_bps = 0
    flash_usd = 0.0
    grid = SIZE_GRID.get(inp) or []
    if grid and amt >= grid[-1] and inp in (MINT_SOL, MINT_USDC, MINT_USDT):
        _ensure_solend_index()
        cfg = _RESERVE_INDEX.get(inp) or {}
        if cfg.get("liquidity_supply") and cfg.get("fee_receiver"):
            flash_bps = int(cfg.get("flash_loan_fee_bps")
                            or SOLEND_FLASH_FEE_BPS_DEFAULT)
            flash_usd = notional * (flash_bps / 10_000.0)
            net_usd -= flash_usd
            fee_usd += flash_usd
    dex_n = max(len(set(labs)), 1)
    cross = dex_n > 1 and not same_pool
    plus = sol_arb_plus_ev(net_usd, min_usd) and not same_pool
    score = (
        net_usd
        * (1.0 + 0.08 * max(dex_n - 1, 0))
        / (1.0 + hops * 0.04)
        / (1.0 + min(impact, 0.08) * 8)
    )
    dec = DECIMALS.get(inp, 9)
    sym_in = SYM.get(inp, "?")
    path = "→".join([sym_in] + [SYM.get(m, "?") for m in hops_mints] + [sym_in])
    venue = "→".join(labs[:4]) or quote_src
    mid_out = hops_mints[0] if hops_mints else inp
    src = quote_src or "jupiter"
    local = src != "jupiter" and "jupiter" not in src.split("+")
    row = {
        "venue": venue[:48],
        "path": path,
        "route": f"{amt / (10 ** dec):.4g} {sym_in}",
        "hops": hops,
        "borrow": f"{amt / (10 ** dec):.4g} {sym_in}",
        "gross_usd": round(gross_usd, 6),
        "slip_usd": round(slip_usd, 6),
        "gas_usd": round(fee_usd, 6),
        "net_usd": round(net_usd, 6),
        "score": round(score, 8),
        "flags": (
            ("cross" if cross else ("local" if local else "jup"))
            + (" multi" if hops > 2 else "")
            + (" same-pool" if same_pool else "")
        ),
        "dex": src,
        "mid": mid_label,
        "cross_dex": cross,
        "dex_n": dex_n,
        "actionable": plus,
        "price_impact_pct": impact,
        "amount_in": amt,
        "input_mint": inp,
        "output_mint": mid_out,
        "min_amount_out": thresh,
        "labels": labs,
        "compute_units": cu,
        "kind": "mev",
        "source": src,
        "quote_src": src,
        "same_pool": same_pool,
        "min_floor_usd": min_usd,
        "notional_usd": round(notional, 4),
        "route_plan": [_compact_route_plan(q) for q in quotes],
        "hop_src": labs[:4],
        "jito_tip_lamports": jito_lam,
        "jito_tip_usd": round(jito_usd, 6),
        "flash_fee_bps": flash_bps,
        "flash_fee_usd": round(flash_usd, 6),
        "use_flash": bool(flash_bps),
        "legs": _arb_legs_from_quotes(inp, quotes, hop_dexes),
        "sol_px": round(float(sol_px or 0.0), 6),
        "priority_median": priority_median,
        "pressure": pressure,
    }
    if skip_reason:
        row["skip_reason"] = skip_reason
        row["debug"] = True
    if not plus:
        row["gap_usd"] = round(max(0.0, float(min_usd) - net_usd), 6)
        row["roi"] = round(gross_usd / max(notional, 1e-9), 6)
        row["debug"] = True
    return row


def _leg(inp: str, out: str, amt: int, slip_bps: int, tail: bool, *,
         exclude: str | None = None, only_direct: bool = False,
         dexes: str | None = None,
         max_accounts: int | None = None) -> tuple[dict | None, int]:
    """One Jupiter hop. Aggregator first; one retry without exclude if needed."""
    nq = 0
    q = _jup_quote(inp, out, amt, slippage_bps=slip_bps,
                   dexes=dexes, exclude_dexes=exclude, only_direct=only_direct,
                   max_accounts=max_accounts)
    nq += 1
    if _quote_illiquid(q, tail) and only_direct:
        q = _jup_quote(inp, out, amt, slippage_bps=slip_bps,
                       dexes=dexes, exclude_dexes=exclude, only_direct=False,
                       max_accounts=max_accounts)
        nq += 1
    if _quote_illiquid(q, tail) and dexes:
        q = _jup_quote(inp, out, amt, slippage_bps=slip_bps,
                       exclude_dexes=exclude, only_direct=only_direct,
                       max_accounts=max_accounts)
        nq += 1
    if _quote_illiquid(q, tail):
        return None, nq
    return q, nq


def _roundtrip_row(
    inp: str, mid: str, mid_label: str, amt: int,
    sol_px: float, priority_median: int | None, min_usd: float,
    pressure: str | None = None,
) -> tuple[dict | None, int]:
    """Cross-DEX A→B→A. Return hop excludes hop-1 venues. Never same-pool as live."""
    tail = inp in TAIL_MINTS or mid in TAIL_MINTS
    slip_bps = _slip_bps(inp, mid)
    quotes_n = 0
    # Direct hops first so hop-1 vs hop-2 labels are real AMM names, not mixed
    # aggregator soup. Illiquid direct retries aggregator inside _leg.
    q1, n = _leg(inp, mid, amt, slip_bps, tail, only_direct=True)
    quotes_n += n
    if not q1:
        return None, quotes_n
    mid_out = int(q1.get("outAmount") or 0)
    labs1 = _route_labels(q1)
    excl = _exclude_csv(labs1)
    q2, n = _leg(mid, inp, mid_out, slip_bps, tail, exclude=excl or None,
                 only_direct=True)
    quotes_n += n
    if not q2:
        # Measure the structural same-pool loss for debug — never emit as live.
        q2s, n = _leg(mid, inp, mid_out, slip_bps, tail, exclude=None,
                      only_direct=True)
        quotes_n += n
        if not q2s:
            return None, quotes_n
        back = int(q2s.get("outAmount") or 0)
        row = _pack_arb_row(
            inp=inp, hops_mints=[mid], amt=amt, back=back,
            quotes=[q1, q2s], min_usd=min_usd, sol_px=sol_px,
            priority_median=priority_median, pressure=pressure,
            mid_label=mid_label, same_pool=True, skip_reason="same-pool",
        )
        return row, quotes_n
    back = int(q2.get("outAmount") or 0)
    labs2 = _route_labels(q2)
    same = bool(set(labs1) & set(labs2)) and not (
        set(labs2) - set(labs1) or set(labs1) - set(labs2)
    )
    # Overlap of one label is OK if the other hop also used a different DEX.
    same_pool = set(labs1) == set(labs2) and len(set(labs1)) <= 1
    skip = None
    if same_pool:
        skip = "same-pool"
    row = _pack_arb_row(
        inp=inp, hops_mints=[mid], amt=amt, back=back,
        quotes=[q1, q2], min_usd=min_usd, sol_px=sol_px,
        priority_median=priority_median, pressure=pressure,
        mid_label=mid_label, same_pool=same_pool, skip_reason=skip,
    )
    if same:
        row["flags"] = (row.get("flags") or "") + " overlap"
    if not sol_arb_plus_ev(row["net_usd"], min_usd) or same_pool:
        if row["net_usd"] <= 0:
            row["skip_reason"] = row.get("skip_reason") or "non-positive-net"
        else:
            row["skip_reason"] = row.get("skip_reason") or "below-floor"
        row["debug"] = True
        row["actionable"] = False
    return row, quotes_n


def _triangle_row(
    a: str, b: str, c: str, label: str, amt: int,
    sol_px: float, priority_median: int | None, min_usd: float,
    pressure: str | None = None,
) -> tuple[dict | None, int]:
    """A→B→C→A. Force hop venues to differ when possible."""
    tail = any(m in TAIL_MINTS for m in (a, b, c))
    slip_bps = _slip_bps(a, b, c)
    quotes_n = 0
    q1, n = _leg(a, b, amt, slip_bps, tail)
    quotes_n += n
    if not q1:
        return None, quotes_n
    out1 = int(q1.get("outAmount") or 0)
    excl1 = _exclude_csv(_route_labels(q1))
    q2, n = _leg(b, c, out1, slip_bps, tail, exclude=excl1 or None)
    quotes_n += n
    if not q2:
        q2, n = _leg(b, c, out1, slip_bps, tail, exclude=None)
        quotes_n += n
    if not q2:
        return None, quotes_n
    out2 = int(q2.get("outAmount") or 0)
    seen = _route_labels(q1) + _route_labels(q2)
    excl2 = _exclude_csv(seen)
    q3, n = _leg(c, a, out2, slip_bps, tail, exclude=excl2 or None)
    quotes_n += n
    if not q3:
        q3, n = _leg(c, a, out2, slip_bps, tail, exclude=None)
        quotes_n += n
    if not q3:
        return None, quotes_n
    back = int(q3.get("outAmount") or 0)
    labs = set(_route_labels(q1) + _route_labels(q2) + _route_labels(q3))
    same_pool = len(labs) <= 1
    row = _pack_arb_row(
        inp=a, hops_mints=[b, c], amt=amt, back=back,
        quotes=[q1, q2, q3], min_usd=min_usd, sol_px=sol_px,
        priority_median=priority_median, pressure=pressure,
        mid_label=label, same_pool=same_pool,
        skip_reason="same-pool" if same_pool else None,
    )
    if not sol_arb_plus_ev(row["net_usd"], min_usd) or same_pool:
        if row["net_usd"] <= 0:
            row["skip_reason"] = row.get("skip_reason") or "non-positive-net"
        else:
            row["skip_reason"] = row.get("skip_reason") or "below-floor"
        row["debug"] = True
        row["actionable"] = False
    return row, quotes_n


def _slim_near(row: dict) -> dict:
    """Debug-only skip row — keep hop labels, drop bulky plan fields."""
    hops = row.get("route_plan") or []
    hop_labs = []
    for hop_list in hops:
        if hop_list and isinstance(hop_list, list) and hop_list[0].get("label"):
            hop_labs.append(hop_list[0]["label"])
    return {
        "path": row.get("path"),
        "mid": row.get("mid"),
        "venue": row.get("venue"),
        "net_usd": row.get("net_usd"),
        "gap_usd": row.get("gap_usd"),
        "skip_reason": row.get("skip_reason") or "skip",
        "same_pool": bool(row.get("same_pool")),
        "quote_src": row.get("quote_src") or "jupiter",
        "source": row.get("source") or row.get("quote_src") or "jupiter",
        "labels": (row.get("labels") or [])[:4],
        "hop_src": hop_labs or (row.get("hop_src") or [])[:4],
        "cross_dex": bool(row.get("cross_dex")),
    }


def _sample_from_row(row: dict | None) -> dict | None:
    if not row:
        return None
    hops = row.get("route_plan") or []
    srcs = []
    for hop_list in hops:
        if not hop_list:
            continue
        lab = (hop_list[0] or {}).get("label")
        if lab and lab not in srcs:
            srcs.append(lab)
    if not srcs:
        srcs = list(row.get("labels") or row.get("hop_src") or [])
    if not srcs:
        return None
    first = None
    for hop_list in hops:
        if hop_list:
            first = hop_list[0]
            break
    return {
        "path": row.get("path"),
        "quote_src": row.get("quote_src") or "jupiter",
        "source": row.get("source") or row.get("quote_src") or "jupiter",
        "labels": srcs,
        "outAmount": (first or {}).get("outAmount"),
        "routePlan": hops[:4],
        "cross_dex": bool(row.get("cross_dex")),
        "same_pool": bool(row.get("same_pool")),
    }


def _b58pk(raw: bytes) -> str:
    from solders.pubkey import Pubkey
    return str(Pubkey.from_bytes(bytes(raw[:32])))


def _u64le(buf: bytes, off: int) -> int:
    return struct.unpack_from("<Q", buf, off)[0]


def _u128le(buf: bytes, off: int) -> int:
    lo, hi = struct.unpack_from("<QQ", buf, off)
    return lo + (hi << 64)


def _i32le(buf: bytes, off: int) -> int:
    return struct.unpack_from("<i", buf, off)[0]


def _u16le(buf: bytes, off: int) -> int:
    return struct.unpack_from("<H", buf, off)[0]


def _acc_bytes(acc: dict | None) -> bytes:
    if not acc:
        return b""
    data = acc.get("data")
    blob = data[0] if isinstance(data, list) and data else data
    if not isinstance(blob, str):
        return b""
    try:
        return base64.b64decode(blob)
    except Exception:
        return b""


def _get_multiple_accounts(pks: list[str], timeout: float = 10.0) -> dict[str, dict]:
    out: dict[str, dict] = {}
    uniq, seen = [], set()
    for p in pks:
        if p and p not in seen:
            seen.add(p)
            uniq.append(p)
    for i in range(0, len(uniq), 20):
        chunk = uniq[i:i + 20]
        res = None
        for extra in (
            {"encoding": "base64", "commitment": "processed"},
            {"encoding": "base64"},
        ):
            try:
                res, _ = sol_rpc(
                    "getMultipleAccounts", [chunk, extra], timeout=timeout)
                break
            except Exception:
                res = None
        if res is None:
            continue
        vals = (res or {}).get("value") if isinstance(res, dict) else (res or [])
        for pk, acc in zip(chunk, vals or []):
            if isinstance(acc, dict):
                out[pk] = acc
    return out


def _spl_vault_amount(raw: bytes) -> int | None:
    if len(raw) < 72:
        return None
    return int(_u64le(raw, 64))


# LiquidityStateV4: 32×u64 then swap counters (80 bytes) then vaults/mints.
_RAY_FEE_NUM, _RAY_FEE_DEN = 176, 184
_RAY_BASE_PNL, _RAY_QUOTE_PNL = 192, 200
_RAY_BASE_DEC, _RAY_QUOTE_DEC = 32, 40
_RAY_BASE_VAULT, _RAY_QUOTE_VAULT = 336, 368
_RAY_BASE_MINT, _RAY_QUOTE_MINT = 400, 432
_RAY_LP_MINT, _RAY_OPEN_ORDERS = 464, 496
_RAY_MARKET, _RAY_MARKET_PROG = 528, 560
_RAY_TARGET_ORDERS = 592
_RAY_NONCE = 8


def _decode_raydium_amm(pk: str, acc: dict, spec: dict) -> dict | None:
    if (acc.get("owner") or "") != RAYDIUM_AMM_V4:
        return None
    raw = _acc_bytes(acc)
    if len(raw) < 464:
        return None
    fee_num, fee_den = _u64le(raw, _RAY_FEE_NUM), _u64le(raw, _RAY_FEE_DEN)
    if fee_den <= 0 or fee_num >= fee_den:
        return None
    try:
        base_mint = _b58pk(raw[_RAY_BASE_MINT:_RAY_BASE_MINT + 32])
        quote_mint = _b58pk(raw[_RAY_QUOTE_MINT:_RAY_QUOTE_MINT + 32])
        base_vault = _b58pk(raw[_RAY_BASE_VAULT:_RAY_BASE_VAULT + 32])
        quote_vault = _b58pk(raw[_RAY_QUOTE_VAULT:_RAY_QUOTE_VAULT + 32])
    except Exception:
        return None
    extra = {}
    if len(raw) >= 624:
        try:
            extra = {
                "nonce": int(_u64le(raw, _RAY_NONCE) & 0xFF),
                "lp_mint": _b58pk(raw[_RAY_LP_MINT:_RAY_LP_MINT + 32]),
                "open_orders": _b58pk(raw[_RAY_OPEN_ORDERS:_RAY_OPEN_ORDERS + 32]),
                "market": _b58pk(raw[_RAY_MARKET:_RAY_MARKET + 32]),
                "market_program": _b58pk(raw[_RAY_MARKET_PROG:_RAY_MARKET_PROG + 32]),
                "target_orders": _b58pk(raw[_RAY_TARGET_ORDERS:_RAY_TARGET_ORDERS + 32]),
            }
        except Exception:
            extra = {}
    return {
        "pk": pk, "dex": "raydium", "kind": "amm_v4",
        "quote_src": "raydium-account", "jup": spec.get("jup") or "Raydium",
        "label": "Raydium", "mint_a": base_mint, "mint_b": quote_mint,
        "vault_a": base_vault, "vault_b": quote_vault,
        "dec_a": int(_u64le(raw, _RAY_BASE_DEC)),
        "dec_b": int(_u64le(raw, _RAY_QUOTE_DEC)),
        "fee_num": fee_num, "fee_den": fee_den,
        "pnl_a": _u64le(raw, _RAY_BASE_PNL), "pnl_b": _u64le(raw, _RAY_QUOTE_PNL),
        "reserve_a": 0, "reserve_b": 0,
        "pair": spec.get("pair") or (
            f"{SYM.get(base_mint, '?')}-{SYM.get(quote_mint, '?')}"),
        **extra,
    }


def _decode_whirlpool(pk: str, acc: dict, spec: dict) -> dict | None:
    """Decode sqrt_price + in-range L. Tick arrays are not fabricated."""
    if (acc.get("owner") or "") != ORCA_WHIRLPOOL:
        return None
    raw = _acc_bytes(acc)
    if len(raw) < 245:
        return None
    tick_spacing = _u16le(raw, 41)
    fee_rate = _u16le(raw, 45)
    liq = _u128le(raw, 49)
    sqrt_p = _u128le(raw, 65)
    tick = _i32le(raw, 81)
    if tick_spacing <= 0 or sqrt_p <= 0:
        return None
    try:
        mint_a = _b58pk(raw[101:133])
        mint_b = _b58pk(raw[181:213])
        vault_a = _b58pk(raw[133:165]) if len(raw) >= 245 else ""
        vault_b = _b58pk(raw[213:245]) if len(raw) >= 245 else ""
    except Exception:
        return None
    # WhirlpoolRewardInfo[3] starts at disc+261 = 269. Omit if account too short.
    reward_infos = None
    off = 269
    if len(raw) >= off + 128:
        reward_infos = []
        for _i in range(3):
            if len(raw) < off + 128:
                break
            try:
                mint_r = _b58pk(raw[off:off + 32])
            except Exception:
                off += 128
                continue
            em_x64 = _u128le(raw, off + 96)
            if em_x64 > 0:
                reward_infos.append({"mint": mint_r, "emissions_x64": em_x64})
            off += 128
    return {
        "pk": pk, "dex": "orca", "kind": "whirlpool",
        "quote_src": "orca-account", "jup": spec.get("jup") or "Whirlpool",
        "label": "Orca", "mint_a": mint_a, "mint_b": mint_b,
        "vault_a": vault_a, "vault_b": vault_b,
        "reserve_a": 0, "reserve_b": 0,
        "tick_spacing": tick_spacing, "fee_rate": fee_rate,
        "liquidity": liq, "sqrt_price": sqrt_p, "tick": tick,
        "reward_infos": reward_infos,
        "pair": spec.get("pair") or (
            f"{SYM.get(mint_a, '?')}-{SYM.get(mint_b, '?')}"),
    }


def _amm_quote(pool: dict, mint_in: str, mint_out: str, amount_in: int) -> int | None:
    if pool.get("kind") != "amm_v4" or amount_in <= 0:
        return None
    a, b = pool.get("mint_a"), pool.get("mint_b")
    if mint_in == a and mint_out == b:
        rin, rout = int(pool.get("reserve_a") or 0), int(pool.get("reserve_b") or 0)
    elif mint_in == b and mint_out == a:
        rin, rout = int(pool.get("reserve_b") or 0), int(pool.get("reserve_a") or 0)
    else:
        return None
    if rin <= 0 or rout <= 0:
        return None
    fee_num, fee_den = int(pool.get("fee_num") or 0), int(pool.get("fee_den") or 0)
    if fee_den <= 0:
        return None
    in_nf = amount_in * (fee_den - fee_num)
    den = rin * fee_den + in_nf
    if den <= 0:
        return None
    out = rout * in_nf // den
    if out <= 0 or out >= rout:
        return None
    return int(out)


def _tick_sqrt_x64(tick: int) -> int:
    try:
        v = math.pow(1.0001, tick / 2.0) * float(Q64)
    except (OverflowError, ValueError):
        return 0
    if v <= 0 or not math.isfinite(v):
        return 0
    return int(v)


def _delta_a(sqrt_0: int, sqrt_1: int, liq: int, round_up: bool) -> int:
    if sqrt_0 > sqrt_1:
        sqrt_0, sqrt_1 = sqrt_1, sqrt_0
    if sqrt_0 <= 0 or liq <= 0:
        return 0
    num = liq * (sqrt_1 - sqrt_0) * Q64
    den = sqrt_0 * sqrt_1
    if den <= 0:
        return 0
    return (num + den - 1) // den if round_up else num // den


def _delta_b(sqrt_0: int, sqrt_1: int, liq: int, round_up: bool) -> int:
    if sqrt_0 > sqrt_1:
        sqrt_0, sqrt_1 = sqrt_1, sqrt_0
    if liq <= 0:
        return 0
    num = liq * (sqrt_1 - sqrt_0)
    return (num + Q64 - 1) // Q64 if round_up else num // Q64


def _whirl_in_tick_quote(pool: dict, mint_in: str, mint_out: str,
                         amount_in: int) -> int | None:
    """In-current-tick only. Crossing a tick array → no number."""
    if pool.get("kind") != "whirlpool" or amount_in <= 0:
        return None
    liq = int(pool.get("liquidity") or 0)
    sqrt_p = int(pool.get("sqrt_price") or 0)
    spacing = int(pool.get("tick_spacing") or 0)
    tick = int(pool.get("tick") or 0)
    fee_rate = int(pool.get("fee_rate") or 0)
    a, b = pool.get("mint_a"), pool.get("mint_b")
    if liq <= 0 or sqrt_p <= 0 or spacing <= 0:
        return None
    if mint_in == a and mint_out == b:
        a_to_b = True
    elif mint_in == b and mint_out == a:
        a_to_b = False
    else:
        return None
    if a_to_b:
        rem = tick % spacing
        bound_tick = tick - (rem if rem else spacing)
    else:
        bound_tick = (tick // spacing + 1) * spacing
    bound_sqrt = _tick_sqrt_x64(bound_tick)
    if bound_sqrt <= 0:
        return None
    fee = (amount_in * fee_rate + WHIRL_FEE_DEN - 1) // WHIRL_FEE_DEN
    leftover = amount_in - fee
    if leftover <= 0:
        return None
    if a_to_b:
        if bound_sqrt >= sqrt_p:
            return None
        max_in = _delta_a(bound_sqrt, sqrt_p, liq, True)
        if leftover > max_in * 9 // 10:
            return None
        denom = liq + leftover * sqrt_p // Q64
        if denom <= 0:
            return None
        next_sqrt = (liq * sqrt_p) // denom
        if next_sqrt <= bound_sqrt or next_sqrt >= sqrt_p:
            return None
        out = _delta_b(next_sqrt, sqrt_p, liq, False)
    else:
        if bound_sqrt <= sqrt_p:
            return None
        max_in = _delta_b(sqrt_p, bound_sqrt, liq, True)
        if leftover > max_in * 9 // 10:
            return None
        next_sqrt = sqrt_p + leftover * Q64 // liq
        if next_sqrt >= bound_sqrt or next_sqrt <= sqrt_p:
            return None
        out = _delta_a(sqrt_p, next_sqrt, liq, False)
    if out <= 0:
        return None
    return int(out)


def _pool_quote(pool: dict, mint_in: str, mint_out: str, amount_in: int) -> int | None:
    kind = pool.get("kind")
    if kind == "amm_v4":
        return _amm_quote(pool, mint_in, mint_out, amount_in)
    if kind == "whirlpool":
        return _whirl_in_tick_quote(pool, mint_in, mint_out, amount_in)
    return None


def _synth_quote(inp: str, out: str, amt_in: int, amt_out: int,
                 label: str, slip_bps: int) -> dict:
    thresh = max(0, int(amt_out * (10_000 - slip_bps) // 10_000))
    return {
        "inputMint": inp, "outputMint": out,
        "inAmount": amt_in, "outAmount": amt_out,
        "otherAmountThreshold": thresh,
        "priceImpactPct": 0.0,
        "routePlan": [{"swapInfo": {
            "label": label, "inputMint": inp, "outputMint": out,
            "inAmount": amt_in, "outAmount": amt_out,
        }}],
    }


def _quote_src_for(pools: list[dict]) -> str:
    srcs: list[str] = []
    for p in pools:
        s = p.get("quote_src") or ""
        if s and s not in srcs:
            srcs.append(s)
    if not srcs:
        return "jupiter"
    return srcs[0] if len(srcs) == 1 else "+".join(srcs)


def _load_pool_specs(specs: list[dict]) -> tuple[list[dict], dict]:
    """getMultipleAccounts on pool specs, then Raydium vaults. No Geyser."""
    spec_by: dict[str, dict] = {}
    for w in specs or []:
        pk = (w or {}).get("pk")
        if pk and pk not in spec_by:
            spec_by[pk] = w
    accs = _get_multiple_accounts(list(spec_by.keys()), timeout=10)
    decoded: list[dict] = []
    skipped: list[dict] = []
    for spec in spec_by.values():
        pk = spec["pk"]
        acc = accs.get(pk)
        if not acc:
            skipped.append({"pk": pk, "pair": spec.get("pair"), "why": "no-account"})
            continue
        kind = spec.get("kind")
        owner = (acc.get("owner") or "")
        if not kind:
            if owner == RAYDIUM_AMM_V4:
                kind = "amm_v4"
            elif owner == ORCA_WHIRLPOOL:
                kind = "whirlpool"
        if kind == "amm_v4":
            pool = _decode_raydium_amm(pk, acc, spec)
        elif kind == "whirlpool":
            pool = _decode_whirlpool(pk, acc, spec)
        else:
            skipped.append({"pk": pk, "pair": spec.get("pair"),
                            "why": "undecodable-kind"})
            continue
        if not pool:
            skipped.append({"pk": pk, "pair": spec.get("pair"), "why": "decode-fail"})
            continue
        if spec.get("discovered"):
            pool["discovered"] = True
        decoded.append(pool)
    vaults: list[str] = []
    for p in decoded:
        if p.get("kind") == "amm_v4":
            vaults.extend([p["vault_a"], p["vault_b"]])
    v_accs = _get_multiple_accounts(vaults, timeout=8) if vaults else {}
    good: list[dict] = []
    for p in decoded:
        if p.get("kind") == "amm_v4":
            ra = _spl_vault_amount(_acc_bytes(v_accs.get(p["vault_a"])))
            rb = _spl_vault_amount(_acc_bytes(v_accs.get(p["vault_b"])))
            if ra is None or rb is None:
                skipped.append({"pk": p["pk"], "pair": p.get("pair"),
                                "why": "vault-decode-fail"})
                continue
            p["reserve_a"] = max(ra - int(p.get("pnl_a") or 0), 0)
            p["reserve_b"] = max(rb - int(p.get("pnl_b") or 0), 0)
            if p["reserve_a"] <= 0 or p["reserve_b"] <= 0:
                skipped.append({"pk": p["pk"], "pair": p.get("pair"),
                                "why": "empty-reserves"})
                continue
        good.append(p)
    meta = {
        "watch": len(spec_by), "decoded": len(good),
        "skipped": skipped, "rpc_accounts": len(accs) + len(v_accs),
        "geyser": False,
        "note": "public RPC; no Geyser — getMultipleAccounts polling",
        "by_dex": {},
    }
    for p in good:
        d = p.get("dex") or "?"
        meta["by_dex"][d] = meta["by_dex"].get(d, 0) + 1
    return good, meta


def _load_watch_pools() -> tuple[list[dict], dict]:
    """Arb/quote venues: hardcoded WATCH_POOLS (Raydium AMM v4 + Orca)."""
    return _load_pool_specs(WATCH_POOLS)


def _pools_for_pair(pools: list[dict], x: str, y: str) -> list[dict]:
    key = frozenset((x, y))
    return [p for p in pools if frozenset((p["mint_a"], p["mint_b"])) == key]


def _local_roundtrip_row(
    p1: dict, p2: dict, inp: str, mid: str, amt: int,
    sol_px: float, prio: int | None, floor: float, pressure: str | None,
) -> dict | None:
    if p1.get("pk") == p2.get("pk"):
        return None
    q1_out = _pool_quote(p1, inp, mid, amt)
    if q1_out is None:
        return None
    q2_out = _pool_quote(p2, mid, inp, q1_out)
    if q2_out is None:
        return None
    labs = [p1.get("label") or p1["dex"], p2.get("label") or p2["dex"]]
    src = _quote_src_for([p1, p2])
    slip = _slip_bps(inp, mid)
    quotes = [
        _synth_quote(inp, mid, amt, q1_out, labs[0], slip),
        _synth_quote(mid, inp, q1_out, q2_out, labs[1], slip),
    ]
    row = _pack_arb_row(
        inp=inp, hops_mints=[mid], amt=amt, back=q2_out,
        quotes=quotes, min_usd=floor, sol_px=sol_px,
        priority_median=prio, pressure=pressure,
        mid_label=f"{SYM.get(inp, '?')}-{SYM.get(mid, '?')}",
        same_pool=False, skip_reason=None,
        quote_src=src,
        hop_dexes=[p1.get("jup") or "", p2.get("jup") or ""],
    )
    if not sol_arb_plus_ev(row["net_usd"], floor):
        row["skip_reason"] = (
            "non-positive-net" if row["net_usd"] <= 0 else "below-floor")
        row["debug"] = True
        row["actionable"] = False
    row["cross_dex"] = True
    flags = row.get("flags") or ""
    if "cross" not in flags:
        row["flags"] = ("cross " + flags).strip()
    return row


def _local_triangle_row(
    p_ab: dict, p_bc: dict, p_ca: dict, a: str, b: str, c: str,
    amt: int, sol_px: float, prio: int | None, floor: float,
    pressure: str | None, label: str,
) -> dict | None:
    out1 = _pool_quote(p_ab, a, b, amt)
    if out1 is None:
        return None
    out2 = _pool_quote(p_bc, b, c, out1)
    if out2 is None:
        return None
    back = _pool_quote(p_ca, c, a, out2)
    if back is None:
        return None
    pools = [p_ab, p_bc, p_ca]
    src = _quote_src_for(pools)
    labs = [p.get("label") or p["dex"] for p in pools]
    slip = _slip_bps(a, b, c)
    quotes = [
        _synth_quote(a, b, amt, out1, labs[0], slip),
        _synth_quote(b, c, out1, out2, labs[1], slip),
        _synth_quote(c, a, out2, back, labs[2], slip),
    ]
    same = len({p["pk"] for p in pools}) <= 1
    row = _pack_arb_row(
        inp=a, hops_mints=[b, c], amt=amt, back=back,
        quotes=quotes, min_usd=floor, sol_px=sol_px,
        priority_median=prio, pressure=pressure,
        mid_label=label, same_pool=same,
        skip_reason="same-pool" if same else None,
        quote_src=src, hop_dexes=[p.get("jup") or "" for p in pools],
    )
    if not sol_arb_plus_ev(row["net_usd"], floor) or same:
        row["skip_reason"] = row.get("skip_reason") or (
            "non-positive-net" if row["net_usd"] <= 0 else "below-floor")
        row["debug"] = True
        row["actionable"] = False
    return row


def fetch_local_pool_arbs(
    amounts_lamports: list[int] | None = None,
    priority_median: int | None = None,
    min_usd: float | None = None,
    pressure: str | None = None,
    sol_px: float | None = None,
) -> dict[str, Any]:
    """Two-pool / triangle edges from on-chain pool accounts (+EV after costs)."""
    now = int(time.time())
    uni = arb_universe()
    out: dict[str, Any] = {
        "ok": False, "opps": [], "near": [], "error": None,
        "ts": now, "last_scan": now, "quotes": 0, "quoted": 0,
        "pairs_tried": 0, "pairs": uni["pairs"], "mode": "local-pool",
        "skipped": 0, "min_usd": None, "quote_errors": 0,
        "quote_src": "raydium-account+orca-account",
        "skipped_quote_fail": 0, "skipped_no_route": 0,
        "skipped_same_pool": 0, "skipped_negative": 0,
        "skipped_below_floor": 0, "universe": uni,
        "sample_route": None, "dexes": [], "by_dex": {},
        "geyser": False, "pools_decoded": 0, "pools_watch": len(WATCH_POOLS),
        "covered_pairs": [],
    }
    try:
        px = float(sol_px) if sol_px else (fetch_sol_price() or 0.0)
        floor = float(min_usd) if min_usd is not None else min_sol_arb_usd()
        out["min_usd"] = floor
        pools, pmeta = _load_watch_pools()
        out["pools_decoded"] = pmeta.get("decoded") or 0
        out["pools_meta"] = pmeta
        out["geyser"] = False
        live, near = [], []
        quotes_n = 0
        skip_n = skip_neg = skip_floor = skip_none = 0
        covered: set[frozenset] = set()
        # Same-pair two-venue cycles
        seen_pair: set[tuple] = set()
        for inp, mid, _lab in ARB_PAIRS:
            key = frozenset((inp, mid))
            group = _pools_for_pair(pools, inp, mid)
            if len(group) >= 2:
                covered.add(key)
            if key in seen_pair:
                continue
            seen_pair.add(key)
            if len(group) < 2:
                continue
            sizes = _sizes_for(inp, mid, amounts_lamports)
            for i, p1 in enumerate(group):
                for p2 in group[i + 1:]:
                    out["pairs_tried"] += 1
                    for amt in sizes:
                        quotes_n += 2
                        row = _local_roundtrip_row(
                            p1, p2, inp, mid, amt, px, priority_median,
                            floor, pressure)
                        if row is None:
                            # Try the other direction with the same size grid
                            # of `mid` as input on the reverse hop start.
                            continue
                        if (row.get("actionable")
                                and sol_arb_plus_ev(row.get("net_usd"), floor)):
                            live.append(row)
                        else:
                            skip_n += 1
                            if (row.get("net_usd") or 0) <= 0:
                                skip_neg += 1
                            elif row.get("skip_reason") == "below-floor":
                                skip_floor += 1
                            near.append(row)
                    for amt in _sizes_for(mid, inp, amounts_lamports):
                        quotes_n += 2
                        row = _local_roundtrip_row(
                            p1, p2, mid, inp, amt, px, priority_median,
                            floor, pressure)
                        if row is None:
                            skip_none += 1
                            continue
                        if (row.get("actionable")
                                and sol_arb_plus_ev(row.get("net_usd"), floor)):
                            live.append(row)
                        else:
                            skip_n += 1
                            if (row.get("net_usd") or 0) <= 0:
                                skip_neg += 1
                            elif row.get("skip_reason") == "below-floor":
                                skip_floor += 1
                            near.append(row)
        # SOL-USDC-USDT triangle using best decoded pool per hop
        for a, b, c, lab in ARB_TRIANGLES:
            p_ab = _pools_for_pair(pools, a, b)
            p_bc = _pools_for_pair(pools, b, c)
            p_ca = _pools_for_pair(pools, c, a)
            if not (p_ab and p_bc and p_ca):
                continue
            covered.add(frozenset((a, b, c)))
            combos = [
                (p_ab[0], p_bc[0], p_ca[0]),
            ]
            if len(p_ab) > 1:
                combos.append((p_ab[1], p_bc[0], p_ca[0]))
            if len(p_bc) > 1:
                combos.append((p_ab[0], p_bc[1], p_ca[0]))
            amt = (amounts_lamports[0] if amounts_lamports
                   else (SIZE_GRID.get(a) or SIZE_GRID[MINT_SOL])[0])
            for pa, pb, pc in combos:
                out["pairs_tried"] += 1
                quotes_n += 3
                row = _local_triangle_row(
                    pa, pb, pc, a, b, c, amt, px, priority_median,
                    floor, pressure, lab)
                if row is None:
                    skip_none += 1
                    continue
                if (row.get("actionable")
                        and sol_arb_plus_ev(row.get("net_usd"), floor)
                        and not row.get("same_pool")):
                    live.append(row)
                else:
                    skip_n += 1
                    if row.get("same_pool"):
                        out["skipped_same_pool"] = out.get("skipped_same_pool", 0) + 1
                    if (row.get("net_usd") or 0) <= 0:
                        skip_neg += 1
                    elif row.get("skip_reason") == "below-floor":
                        skip_floor += 1
                    near.append(row)
        live.sort(key=lambda r: (r.get("score") or 0, r.get("net_usd") or -1e9),
                  reverse=True)
        near.sort(key=lambda r: r.get("gap_usd") if r.get("gap_usd") is not None
                  else 1e9)
        shown_live, shown_near = live[:16], near[:8]
        dex_counts: dict[str, int] = {}
        atom_mix: dict[str, int] = {}
        for r in shown_live + shown_near:
            s = r.get("quote_src") or ""
            if "raydium-account" in s:
                atom_mix["raydium-account"] = atom_mix.get("raydium-account", 0) + 1
            if "orca-account" in s:
                atom_mix["orca-account"] = atom_mix.get("orca-account", 0) + 1
            for lab in r.get("labels") or []:
                dex_counts[lab] = dex_counts.get(lab, 0) + 1
        sample = None
        for cand in shown_live + shown_near:
            sample = _sample_from_row(cand)
            if sample:
                break
        mix_label = "+".join(
            k for k in ("raydium-account", "orca-account") if atom_mix.get(k)
        ) or "raydium-account+orca-account"
        out["opps"] = shown_live
        out["near"] = [_slim_near(r) for r in shown_near]
        out["quotes"] = quotes_n
        out["quoted"] = quotes_n
        out["skipped"] = skip_n + skip_none
        out["skipped_no_route"] = skip_none
        out["skipped_negative"] = skip_neg
        out["skipped_below_floor"] = skip_floor
        out["quote_src"] = mix_label
        out["quote_src_mix"] = atom_mix
        out["sample_route"] = sample
        out["by_dex"] = dex_counts
        out["dexes"] = list(dex_counts.keys()) or list(
            (pmeta.get("by_dex") or {}).keys())
        out["ok"] = True
        out["last_scan"] = int(time.time())
        out["tip_usd"] = round(
            _prio_cost_usd(priority_median, px, _cu_estimate(4), pressure), 6)
        out["priority_median"] = priority_median
        out["sol_px"] = px
        out["covered_pairs"] = [
            "-".join(sorted(SYM.get(m, m[:4]) for m in k))
            for k in covered if len(k) == 2
        ]
        out["covered_keys"] = covered
        uni2 = arb_universe(out["pairs_tried"])
        uni2["venues"] = out["dexes"]
        uni2["quote_src"] = mix_label
        out["universe"] = uni2
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"
        out["last_scan"] = int(time.time())
    return out


def fetch_jupiter_roundtrips(
    amounts_lamports: list[int] | None = None,
    priority_median: int | None = None,
    min_usd: float | None = None,
    pressure: str | None = None,
    sol_px: float | None = None,
    skip_pairs: set | None = None,
) -> dict[str, Any]:
    """Jupiter fallback cycles for pairs we cannot decode locally.

    Live `opps` are +EV only (net > 0 and >= dynamic floor). Negative / below
    floor / same-pool round-trips are never emitted as live — optional debug
    near-misses are capped and flagged. Quotes are paced (lite API).
    """
    now = int(time.time())
    uni = arb_universe()
    out: dict[str, Any] = {
        "ok": False, "opps": [], "near": [], "error": None,
        "ts": now, "last_scan": now, "quotes": 0, "quoted": 0,
        "pairs_tried": 0, "pairs": uni["pairs"],
        "mode": "jup-+ev", "skipped": 0, "min_usd": None,
        "quote_errors": 0, "quote_src": "jupiter",
        "skipped_quote_fail": 0, "skipped_no_route": 0,
        "skipped_same_pool": 0, "skipped_negative": 0,
        "skipped_below_floor": 0, "universe": uni,
        "sample_route": None, "dexes": ["jupiter"], "by_dex": {},
    }
    global _QUOTE_FAILS
    with _QUOTE_LOCK:
        _QUOTE_FAILS = 0
    try:
        px = float(sol_px) if sol_px else (fetch_sol_price() or 0.0)
        floor = float(min_usd) if min_usd is not None else min_sol_arb_usd()
        out["min_usd"] = floor
        jobs: list[tuple] = []
        skip = skip_pairs or set()
        for inp, mid, mid_label in ARB_PAIRS:
            if frozenset((inp, mid)) in skip:
                continue
            for amt in _sizes_for(inp, mid, amounts_lamports):
                jobs.append(("two", inp, mid, mid_label, amt))
        major_amt = (amounts_lamports[0] if amounts_lamports
                     else SIZE_GRID[MINT_SOL][0])
        for a, b, c, lab in ARB_TRIANGLES:
            if frozenset((a, b, c)) in skip:
                continue
            amt = major_amt if a == MINT_SOL else (
                SIZE_GRID.get(a) or SIZE_GRID[MINT_USDC])[0]
            jobs.append(("tri", a, b, c, lab, amt))
        out["pairs_tried"] = len(jobs)
        out["universe"] = arb_universe(len(jobs))
        if not jobs:
            out["ok"] = True
            out["last_scan"] = int(time.time())
            out["mode"] = "jup-fallback-idle"
            return out
        rows_live, rows_near = [], []
        quotes = 0
        skipped = 0
        skip_no_route = 0
        skip_same = 0
        skip_neg = 0
        skip_floor = 0
        workers = min(2, max(1, len(jobs)))

        def _run_job(job: tuple) -> tuple[dict | None, int]:
            kind = job[0]
            if kind == "tri":
                _, a, b, c, lab, amt = job
                return _triangle_row(
                    a, b, c, lab, amt, px, priority_median, floor, pressure)
            _, inp, mid, lab, amt = job
            return _roundtrip_row(
                inp, mid, lab, amt, px, priority_median, floor, pressure)

        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(_run_job, job) for job in jobs]
            for fut in as_completed(futs):
                try:
                    row, nq = fut.result()
                except Exception:
                    skip_no_route += 1
                    skipped += 1
                    continue
                quotes += nq
                if not row:
                    skip_no_route += 1
                    skipped += 1
                    continue
                if (row.get("actionable")
                        and sol_arb_plus_ev(row.get("net_usd"), floor)
                        and not row.get("same_pool")):
                    rows_live.append(row)
                else:
                    skipped += 1
                    if row.get("same_pool"):
                        skip_same += 1
                    if (row.get("net_usd") or 0) <= 0:
                        skip_neg += 1
                    elif row.get("skip_reason") == "below-floor":
                        skip_floor += 1
                    rows_near.append(row)
        rows_live.sort(
            key=lambda r: (r.get("score") or 0, r.get("net_usd") or -1e9),
            reverse=True)
        # Debug only: closest to the floor, never mixed into live.
        rows_near.sort(key=lambda r: r.get("gap_usd") if r.get("gap_usd") is not None
                       else 1e9)
        rows_near = [
            r for r in rows_near
            if r.get("source") == "jupiter" and int(r.get("min_amount_out") or 0) >= 0
            and r.get("skip_reason")
        ]
        sample = None
        for cand in rows_live + rows_near:
            sample = _sample_from_row(cand)
            if sample:
                break
        dex_counts: dict[str, int] = {}
        for r in rows_live + rows_near:
            for lab in r.get("labels") or []:
                dex_counts[lab] = dex_counts.get(lab, 0) + 1
        qfail = _quote_fail_count()
        out["opps"] = rows_live[:16]
        out["near"] = [_slim_near(r) for r in rows_near[:8]]
        out["quotes"] = quotes
        out["quoted"] = quotes
        out["skipped"] = skipped
        out["skipped_quote_fail"] = qfail
        out["skipped_no_route"] = skip_no_route
        out["skipped_same_pool"] = skip_same
        out["skipped_negative"] = skip_neg
        out["skipped_below_floor"] = skip_floor
        out["quote_errors"] = qfail
        out["quote_src"] = "jupiter"
        out["sample_route"] = sample
        out["by_dex"] = dex_counts
        out["dexes"] = list(dex_counts.keys()) or ["jupiter"]
        out["ok"] = True
        out["last_scan"] = int(time.time())
        out["tip_usd"] = round(
            _prio_cost_usd(priority_median, px, _cu_estimate(4), pressure), 6)
        out["priority_median"] = priority_median
        out["sol_px"] = px
        uni2 = arb_universe(len(jobs))
        uni2["venues"] = out["dexes"]
        out["universe"] = uni2
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"
        out["last_scan"] = int(time.time())
        out["quote_src"] = "jupiter"
        out["universe"] = out.get("universe") or arb_universe()
    return out


def fetch_sol_dex_arbs(
    amounts_lamports: list[int] | None = None,
    priority_median: int | None = None,
    min_usd: float | None = None,
    pressure: str | None = None,
    sol_px: float | None = None,
) -> dict[str, Any]:
    """Primary: local pool accounts. Fallback: Jupiter lite for uncovered pairs."""
    _ensure_solend_index()
    local = fetch_local_pool_arbs(
        amounts_lamports, priority_median, min_usd, pressure, sol_px)
    skip = local.get("covered_keys") or set()
    jup: dict[str, Any] = {
        "ok": True, "opps": [], "near": [], "quotes": 0, "quoted": 0,
        "skipped": 0, "pairs_tried": 0, "quote_src": "jupiter",
        "by_dex": {}, "dexes": [], "sample_route": None,
    }
    # Jupiter only for pairs we could not two-venue-decode on-chain.
    try:
        jup = fetch_jupiter_roundtrips(
            amounts_lamports, priority_median, min_usd, pressure, sol_px,
            skip_pairs=skip)
    except Exception as e:  # noqa: BLE001
        jup["ok"] = False
        jup["error"] = f"{type(e).__name__}: {e}"
    live = list(local.get("opps") or [])
    seen = {(r.get("path"), r.get("amount_in"), r.get("quote_src"))
            for r in live}
    for r in (jup.get("opps") or []):
        key = (r.get("path"), r.get("amount_in"), r.get("quote_src"))
        if key in seen:
            continue
        live.append(r)
        seen.add(key)
    live.sort(key=lambda r: (r.get("score") or 0, r.get("net_usd") or -1e9),
              reverse=True)
    near = list(local.get("near") or []) + list(jup.get("near") or [])
    near.sort(key=lambda r: r.get("gap_usd") if r.get("gap_usd") is not None
              else 1e9)
    src_mix: dict[str, int] = dict(local.get("quote_src_mix") or {})
    jup_rows = list(jup.get("opps") or []) + list(jup.get("near") or [])
    if jup_rows:
        src_mix["jupiter"] = src_mix.get("jupiter", 0) + len(jup_rows)
    local_n = len(local.get("opps") or []) + len(local.get("near") or [])
    jup_n = len(jup_rows)
    dex_counts: dict[str, int] = dict(local.get("by_dex") or {})
    for k, v in (jup.get("by_dex") or {}).items():
        dex_counts[k] = dex_counts.get(k, 0) + v
    src_parts = []
    if local.get("pools_decoded"):
        src_parts.append(local.get("quote_src") or "raydium-account+orca-account")
    if jup.get("pairs_tried"):
        src_parts.append("jupiter")
    quote_src = "+".join(src_parts) if src_parts else (
        local.get("quote_src") or "jupiter")
    sample = local.get("sample_route") or jup.get("sample_route")
    px = local.get("sol_px") or jup.get("sol_px")
    now = int(time.time())
    uni = arb_universe(
        int(local.get("pairs_tried") or 0) + int(jup.get("pairs_tried") or 0))
    uni["venues"] = list(dex_counts.keys()) or uni.get("venues")
    uni["quote_src"] = quote_src
    uni["local_n"] = local_n
    uni["jup_n"] = jup_n
    err = local.get("error") or jup.get("error")
    return {
        "ok": bool(local.get("ok") or jup.get("ok")),
        "opps": live[:16],
        "near": near[:8],
        "error": err,
        "ts": now, "last_scan": now,
        "quotes": int(local.get("quotes") or 0) + int(jup.get("quotes") or 0),
        "quoted": int(local.get("quoted") or 0) + int(jup.get("quoted") or 0),
        "pairs_tried": int(local.get("pairs_tried") or 0) + int(
            jup.get("pairs_tried") or 0),
        "pairs": uni.get("pairs"),
        "mode": "local-pool+jup",
        "skipped": int(local.get("skipped") or 0) + int(jup.get("skipped") or 0),
        "min_usd": local.get("min_usd") or jup.get("min_usd"),
        "quote_errors": int(local.get("quote_errors") or 0) + int(
            jup.get("quote_errors") or 0),
        "quote_src": quote_src,
        "quote_src_mix": src_mix,
        "local_n": local_n,
        "jup_n": jup_n,
        "skipped_quote_fail": int(jup.get("skipped_quote_fail") or 0),
        "skipped_no_route": int(local.get("skipped_no_route") or 0) + int(
            jup.get("skipped_no_route") or 0),
        "skipped_same_pool": int(local.get("skipped_same_pool") or 0) + int(
            jup.get("skipped_same_pool") or 0),
        "skipped_negative": int(local.get("skipped_negative") or 0) + int(
            jup.get("skipped_negative") or 0),
        "skipped_below_floor": int(local.get("skipped_below_floor") or 0) + int(
            jup.get("skipped_below_floor") or 0),
        "universe": uni,
        "sample_route": sample,
        "dexes": list(dex_counts.keys()),
        "by_dex": dex_counts,
        "geyser": False,
        "geyser_note": "public RPC; no Geyser — getMultipleAccounts polling",
        "pools_decoded": int(local.get("pools_decoded") or 0),
        "pools_watch": int(local.get("pools_watch") or len(WATCH_POOLS)),
        "pools_meta": local.get("pools_meta") or {},
        "covered_pairs": local.get("covered_pairs") or [],
        "tip_usd": local.get("tip_usd") or jup.get("tip_usd"),
        "priority_median": priority_median,
        "sol_px": px,
        "jup_jobs": int(jup.get("pairs_tried") or 0),
        "local_jobs": int(local.get("pairs_tried") or 0),
    }


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


def decode_solend_competitors(limit: int = 16) -> dict[str, Any]:
    """Recent Solend txs that actually liquidate — not a dump of all program sigs.

    Empty list is honest: none in this signature window. Does not invent PnL.
    """
    out: dict[str, Any] = {
        "ok": False, "rows": [], "liq_n": 0, "revert_n": 0,
        "scanned": 0, "last_slot": None, "error": None,
        "ts": int(time.time()),
    }
    try:
        sigs = recent_program_sigs(SOLEND_PROGRAM, limit=limit)
        out["scanned"] = len(sigs)
        if sigs:
            out["last_slot"] = sigs[0].get("slot")
        rows = []
        liq_n = revert_n = 0
        for s in sigs:
            sig = s.get("sig")
            if not sig:
                continue
            if s.get("err"):
                revert_n += 1
            try:
                tx, _ = sol_rpc(
                    "getTransaction",
                    [sig, {"encoding": "json",
                            "maxSupportedTransactionVersion": 0}],
                    timeout=12,
                )
            except Exception:
                tx = None
            if not isinstance(tx, dict):
                continue
            meta = tx.get("meta") or {}
            logs = meta.get("logMessages") or []
            kind = _classify_solend_logs(logs)
            if kind != "liq":
                # Still seed hydrates from landing-adjacent accounts.
                remember_obligation_keys(_obligation_candidates(tx))
                continue
            liq_n += 1
            fee_payer = None
            msg = (tx.get("transaction") or {}).get("message") or {}
            keys = msg.get("accountKeys") or []
            if keys:
                k0 = keys[0]
                fee_payer = k0 if isinstance(k0, str) else (k0 or {}).get("pubkey")
            obl = ""
            for cand in _obligation_candidates(tx):
                if cand != fee_payer:
                    obl = cand
                    break
            if obl:
                remember_obligation_keys([obl])
            hyd = _HYDRATE_CACHE.get(obl) if obl else None
            pair = "solend-liq"
            if hyd:
                pair = (
                    f"{hyd.get('coll_sym') or hyd.get('collateral_sym') or '?'}→"
                    f"{hyd.get('debt_sym') or '?'}"
                )
            fee_lamports = int(meta.get("fee") or 0)
            rows.append({
                "sig": sig,
                "tx": sig,
                "solscan": f"https://solscan.io/tx/{sig}",
                "slot": s.get("slot") or tx.get("slot"),
                "err": s.get("err") or meta.get("err"),
                "flags": ("liq revert"
                          if (s.get("err") or meta.get("err")) else "liq"),
                "pair": pair,
                "searcher": fee_payer or "",
                "user": obl or "",
                "gas_usd": None,
                "est": hyd.get("profit_usd") if hyd else None,
                "net": hyd.get("net_usd") if hyd else None,
                "fee_lamports": fee_lamports,
                "kind": "liq",
                "missed": True,
                "edge": True,
                "ts": out["ts"],
            })
        out["rows"] = rows
        out["liq_n"] = liq_n
        out["revert_n"] = revert_n
        out["ok"] = True
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"
    return out


# --------------------------------------------------------------------------- mempool / landing + submit


def _short_pk(pk: str) -> str:
    if not pk or len(pk) < 12:
        return pk or "—"
    return pk[:6] + "…" + pk[-4:]


def _classify_solend_logs(logs: list[str]) -> str:
    joined = "\n".join(logs or [])
    if any(k in joined for k in (
        "LiquidateObligationAndRedeemReserveCollateral",
        "LiquidateObligation", "liquidate_obligation",
        "Instruction: Liquidate",
        "liquidateObligation",
    )):
        return "liq"
    if any(k in joined for k in (
        "RefreshObligation", "refresh_obligation", "RefreshReserve",
        "refresh_reserve", "Instruction: Refresh",
    )):
        return "refresh"
    if "BorrowObligationLiquidity" in joined or "Instruction: Borrow" in joined:
        return "borrow"
    if "RepayObligationLiquidity" in joined or "Instruction: Repay" in joined:
        return "repay"
    if "DepositReserveLiquidity" in joined or "Instruction: Deposit" in joined:
        return "deposit"
    return "other"


def _obligation_candidates(tx: dict) -> list[str]:
    """Pull likely obligation pubkeys from a Solend tx (skip programs/sys)."""
    msg = (tx.get("transaction") or {}).get("message") or {}
    keys = msg.get("accountKeys") or []
    out = []
    for k in keys:
        pk = k if isinstance(k, str) else (k or {}).get("pubkey") or ""
        if not pk or len(pk) < 32:
            continue
        if pk in _SKIP_ACCOUNTS or pk in JITO_TIP_ACCOUNTS:
            continue
        if pk in (MINT_SOL, MINT_USDC, MINT_USDT, MINT_MSOL, MINT_JITOSOL):
            continue
        if pk not in out:
            out.append(pk)
    return out[:8]


def _hydrate_obligations(pubkeys: list[str], source: str = "mempool") -> list[dict]:
    uniq = [p for p in pubkeys if _is_pubkey(p)]
    remember_obligation_keys(uniq)
    opps = _hydrate_from_rpc(uniq[:24], source=source)
    return opps


def fetch_jito_tip_pressure(limit: int = 8) -> dict[str, Any]:
    """Recent Jito tip-account signatures ≈ bundle landing pressure."""
    out: dict[str, Any] = {
        "ok": False, "bundles": 0, "rows": [], "error": None,
        "tip_accounts_live": 0, "ts": int(time.time()),
    }
    rows = []
    try:
        for url in (JITO_BLOCK_ENGINE, JITO_BUNDLES):
            try:
                r = _http_post(url, {"jsonrpc": "2.0", "id": 1,
                                     "method": "getTipAccounts",
                                     "params": []}, timeout=5)
                data = r.json()
                tips = data.get("result") or []
                if isinstance(tips, list) and tips:
                    out["tip_accounts_live"] = len(tips)
                    break
            except Exception:
                continue
        for tip in JITO_TIP_ACCOUNTS[:3]:
            try:
                res, _ = sol_rpc(
                    "getSignaturesForAddress",
                    [tip, {"limit": limit}],
                    timeout=8,
                )
                for s in res or []:
                    rows.append({
                        "kind": "jito",
                        "cls": "mev",
                        "sig": s.get("signature"),
                        "slot": s.get("slot"),
                        "err": s.get("err"),
                        "searcher": _short_pk(tip),
                        "user": "jito-tip",
                        "pair": "jito-bundle",
                        "flags": "jito" if not s.get("err") else "jito revert",
                        "tx": (s.get("signature") or "")[:12] + "…",
                        "solscan": (
                            f"https://solscan.io/tx/{s.get('signature')}"
                            if s.get("signature") else None
                        ),
                    })
            except Exception:
                continue
        rows.sort(key=lambda r: -(int(r.get("slot") or 0)))
        out["rows"] = rows[:20]
        out["bundles"] = len(rows)
        out["ok"] = True
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def watch_solend_landing(
    limit: int = 20,
    sol_px: float | None = None,
    priority_median: int | None = None,
    pressure: str | None = None,
) -> dict[str, Any]:
    """Near-mempool watch: processed/confirmed Solend landing + Jito tips.

    Solana has no public ETH-style mempool. The closest honest feed on public
    RPC is the landing stream (processed commitment) plus Jito tip accounts.
    RefreshObligation / Borrow just before a liq is the race signal; we hydrate
    those obligation accounts and score real net profit.
    """
    out: dict[str, Any] = {
        "ok": False, "hits": [], "opportunities": [], "backruns": [],
        "liq_n": 0, "refresh_n": 0, "mev_n": 0, "contested_n": 0,
        "decoded": 0, "error": None, "method": None, "ts": int(time.time()),
        "note": "",
    }
    try:
        if not _RESERVE_INDEX:
            try:
                fetch_solend_watchlist()
            except Exception:
                pass
        px = sol_px if sol_px is not None else (fetch_sol_price() or 0.0)
        sigs = []
        method = None
        last_err = None
        for commitment in ("processed", "confirmed"):
            try:
                res, url = sol_rpc(
                    "getSignaturesForAddress",
                    [SOLEND_PROGRAM, {"limit": int(limit),
                                      "commitment": commitment}],
                    timeout=10,
                )
                sigs = [s for s in (res or []) if isinstance(s, dict)
                        and s.get("signature")]
                if sigs:
                    method = f"sigs {commitment} via {url}"
                    break
            except Exception as e:  # noqa: BLE001
                last_err = f"{type(e).__name__}: {e}"
                continue
        if not sigs:
            out["ok"] = True
            out["error"] = last_err
            out["note"] = "no Solend signatures on public RPC this cycle"
            return out

        out["method"] = method
        fresh = [s for s in sigs if s["signature"] not in _SEEN_SIGS]
        to_decode = (fresh or sigs)[:12]
        hits = []
        obl_keys: list[str] = []
        contested: set[str] = set()
        liq_n = refresh_n = 0

        def _one(sig_row: dict) -> dict | None:
            sig = sig_row.get("signature")
            try:
                tx, _ = sol_rpc(
                    "getTransaction",
                    [sig, {"encoding": "json",
                           "maxSupportedTransactionVersion": 0}],
                    timeout=10,
                )
            except Exception:
                return None
            if not isinstance(tx, dict):
                return None
            meta = tx.get("meta") or {}
            logs = meta.get("logMessages") or []
            kind = _classify_solend_logs(logs)
            keys = _obligation_candidates(tx)
            msg = (tx.get("transaction") or {}).get("message") or {}
            ak = msg.get("accountKeys") or []
            fee_payer = None
            if ak:
                k0 = ak[0]
                fee_payer = k0 if isinstance(k0, str) else (k0 or {}).get("pubkey")
            fee_lamports = int(meta.get("fee") or 0)
            return {
                "sig": sig,
                "slot": sig_row.get("slot") or tx.get("slot"),
                "err": sig_row.get("err") or meta.get("err"),
                "kind": kind,
                "cls": ("hot" if kind == "liq" else
                        "elevated" if kind == "refresh" else "busy"),
                "searcher": _short_pk(fee_payer or ""),
                "fee_payer": fee_payer or "",
                "obligation_keys": keys,
                "fee_lamports": fee_lamports,
                "flags": (
                    ("liq revert" if kind == "liq" and (sig_row.get("err")
                                                       or meta.get("err"))
                     else kind)
                ),
                "tx": (sig or "")[:12] + "…",
                "solscan": f"https://solscan.io/tx/{sig}" if sig else None,
                "pair": f"solend-{kind}",
            }

        workers = min(4, max(1, len(to_decode)))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(_one, s) for s in to_decode]
            for fut in as_completed(futs):
                try:
                    row = fut.result()
                except Exception:
                    continue
                if not row:
                    continue
                out["decoded"] += 1
                if row.get("sig"):
                    _SEEN_SIGS.append(row["sig"])
                kind = row.get("kind")
                if kind == "liq":
                    liq_n += 1
                    for k in row.get("obligation_keys") or []:
                        contested.add(k)
                elif kind == "refresh":
                    refresh_n += 1
                # Any Solend tx can seed obligation hydrates (GPA is often blocked)
                for k in row.get("obligation_keys") or []:
                    if k not in obl_keys:
                        obl_keys.append(k)
                hits.append(row)

        jito = fetch_jito_tip_pressure(limit=6)
        for jr in jito.get("rows") or []:
            hits.append(jr)

        remember_obligation_keys(obl_keys)
        hyd = _hydrate_obligations(obl_keys[:24], source="mempool")
        by_obl = {h.get("obligation"): h for h in hyd if h.get("obligation")}
        opps = []
        backruns = []
        scored_all = []
        for h in hyd:
            scored = score_liq_profit(
                h, sol_px=px, priority_median=priority_median,
                pressure=pressure,
            )
            if scored.get("obligation") in contested:
                scored["contested"] = True
                scored["urgency"] = "hot"
                scored["note"] = (scored.get("note") or "") + " · mempool race"
            scored["source"] = "mempool"
            scored["kind"] = "liq"
            scored_all.append(scored)
            if scored.get("hf") is not None and scored["hf"] < 1.05:
                opps.append(scored)
            if (scored.get("hf") is not None and scored["hf"] < 1.0
                    and (scored.get("debt_usd") or 0) >= 50):
                backruns.append({
                    "kind": "backrun",
                    "cls": "mev",
                    "source": "mempool",
                    "path": (
                        f"{scored.get('collateral_sym')}→"
                        f"{scored.get('debt_sym')}"
                    ),
                    "obligation": scored.get("obligation"),
                    "user": scored.get("user"),
                    "hf": scored.get("hf"),
                    "notional_usd": scored.get("debt_usd"),
                    "gross_usd": scored.get("gross_usd"),
                    "net_usd": scored.get("profit_usd"),
                    "profit_usd": scored.get("profit_usd"),
                    "flags": "liq-backrun",
                    "note": "Jupiter dump-adjacent after Solend seize",
                })

        opps.sort(key=lambda o: (
            o.get("hf") is None, o.get("hf") or 99,
            -(o.get("profit_usd") or 0),
        ))
        for hit in hits:
            if hit.get("kind") in ("liq", "refresh"):
                for k in hit.get("obligation_keys") or []:
                    scored = by_obl.get(k)
                    if scored:
                        hit["hf"] = scored.get("hf")
                        hit["profit_usd"] = scored.get("profit_usd")
                        hit["user"] = scored.get("user")
                        hit["pair"] = (
                            f"{scored.get('collateral_sym')}→"
                            f"{scored.get('debt_sym')}"
                        )
                        hit["contested"] = k in contested
                        break
            hit.setdefault("user", hit.get("searcher") or "—")

        hits.sort(key=lambda r: -(int(r.get("slot") or 0)))
        remember_hydrates(scored_all)
        liq = [o for o in opps if o.get("hf") is not None
               and o["hf"] < 1.0 and not _dust_obligation(o)]
        watch = closest_to_stress(10)
        out["hits"] = hits[:40]
        out["opportunities"] = liq[:25]
        out["watch"] = watch
        out["near"] = [o for o in scored_all if o.get("hf") is not None
                       and 1.0 <= o["hf"] < 1.1][:12]
        out["backruns"] = backruns[:8]
        out["liq_n"] = liq_n
        out["refresh_n"] = refresh_n
        out["mev_n"] = int(jito.get("bundles") or 0) + len(backruns)
        out["contested_n"] = len(contested)
        out["last_slot"] = sigs[0].get("slot") if sigs else None
        out["ok"] = True
        out["jito"] = {
            "bundles": jito.get("bundles") or 0,
            "tip_accounts_live": jito.get("tip_accounts_live") or 0,
        }
        out["note"] = (
            f"{method or 'landing'}; decoded {out['decoded']}; "
            f"liq={liq_n} refresh={refresh_n} "
            f"jito={jito.get('bundles') or 0} "
            f"hf<1={len(out['opportunities'])} closest={len(watch)}"
        )
        _LANDING_CACHE["ts"] = out["ts"]
        _LANDING_CACHE["hits"] = out["hits"]
        _LANDING_CACHE["watch"] = watch
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def program_is_live(program_id: str) -> bool:
    """True only when getAccountInfo shows a real BPF program (not a stub)."""
    pid = (program_id or "").strip()
    if _is_stub_program(pid):
        return False
    try:
        acc, _ = sol_rpc(
            "getAccountInfo",
            [pid, {"encoding": "base64"}],
            timeout=6,
        )
        val = (acc or {}).get("value") if isinstance(acc, dict) else None
        if not val:
            return False
        owner = val.get("owner") or ""
        return owner in BPF_LOADERS or "BPFLoader" in owner
    except Exception:
        return False


def _is_stub_program(pid: str | None) -> bool:
    p = (pid or "").strip()
    if not p:
        return True
    if p in (STUB_LIQ_PROGRAM, STUB_ARB_PROGRAM):
        return True
    return p.startswith("Liq1111") or p.startswith("Arb1111")


def _solders_ok() -> bool:
    try:
        from solders.keypair import Keypair  # noqa: F401
        from solders.transaction import VersionedTransaction  # noqa: F401
        return True
    except Exception:
        return False


def _bot_keypair_path() -> str:
    p = (os.environ.get("SOL_KEYPAIR") or "").strip()
    if p:
        p = os.path.expanduser(p)
        if os.path.isfile(p):
            return p
    if os.path.isfile(BOT_KEY_PATH):
        return BOT_KEY_PATH
    return ""


def _sponsor_keypair_path() -> str:
    if os.path.isfile(SPONSOR_KEY_PATH):
        return SPONSOR_KEY_PATH
    return ""


def _load_solders_keypair(path: str):
    from solders.keypair import Keypair
    path = os.path.expanduser(path)
    with open(path) as f:
        raw = json.loads(f.read())
    if not (isinstance(raw, list) and len(raw) >= 32):
        raise ValueError("keypair json is not a 64-byte secret array")
    blob = bytes(raw[:64] if len(raw) >= 64 else raw[:32])
    if len(blob) == 64:
        return Keypair.from_bytes(blob)
    return Keypair.from_seed(bytes(raw[:32]))


def _liq_account_gaps(opp: dict | None) -> list[str]:
    """Missing Solend accounts for a Python liquidate ix. Empty = wirable."""
    opp = opp or {}
    _ensure_solend_index()
    gaps = []
    obl = (opp.get("obligation") or "").strip()
    if not _is_pubkey(obl):
        gaps.append("obligation")
    repay_r = (opp.get("debt_reserve") or "").strip()
    withdraw_r = (opp.get("coll_reserve") or "").strip()
    if not repay_r:
        gaps.append("debt_reserve")
    if not withdraw_r:
        gaps.append("coll_reserve")
    _hydrate_reserves_onchain([repay_r, withdraw_r])
    rcfg = _RESERVE_INDEX.get(repay_r) or {}
    wcfg = _RESERVE_INDEX.get(withdraw_r) or {}
    for lab, cfg, keys in (
        ("repay", rcfg, (
            "liquidity_supply", "mint", "pyth", "switchboard", "fee_receiver",
        )),
        ("withdraw", wcfg, (
            "liquidity_supply", "collateral_mint", "collateral_supply",
            "fee_receiver", "mint", "pyth", "switchboard",
        )),
    ):
        for k in keys:
            if not (cfg.get(k) or ""):
                gaps.append(f"{lab}.{k}")
    market = (opp.get("lending_market")
              or _LENDING_MARKET.get("address") or "")
    if not _is_pubkey(market):
        gaps.append("lending_market")
    if int(opp.get("debt_amount") or 0) <= 0:
        gaps.append("debt_amount")
    wl = _LENDING_MARKET.get("whitelist") or ""
    bot = (opp.get("fee_payer") or current_wallets().get("bot") or "")
    if _is_pubkey(wl) and bot and wl != bot:
        gaps.append("liquidator_whitelist")
    return gaps


def live_submit_blockers(kind: str = "liq") -> list[str]:
    """Honest gates for LIVE send. Caller still requires arm + not sim_only.

    Arb/liq execute via Python (Jupiter /swap + Jito, or Solend ix + Jito).
    Stub Arb1111/Liq1111 programs are never a reason to send — they are
    ignored, not required. Keypair + solders are the real prerequisites.
    """
    reasons = []
    if not _solders_ok():
        reasons.append("solders missing — pip install solders to sign txs")
    if not _bot_keypair_path():
        reasons.append("bot keypair missing — SOL_KEYPAIR or solana/keys/bot.json")
    if kind == "liq":
        pid = (os.environ.get("SOL_LIQ_PROGRAM") or "").strip()
        if pid and _is_stub_program(pid):
            # stub id is ignored; Python sends to Solend itself
            pass
    else:
        pid = (os.environ.get("SOL_ARB_PROGRAM") or "").strip()
        if pid and _is_stub_program(pid):
            pass
    return reasons


def wallets_funded_enough(funds: dict | None = None) -> tuple[bool, list[str]]:
    """Sponsor needs tip dust; bot needs CU + inventory."""
    reasons = []
    try:
        bal = funds or fetch_wallet_balances(current_wallets())
    except Exception as e:  # noqa: BLE001
        return False, [f"wallet balance probe failed: {type(e).__name__}"]
    sponsor = bal.get("sponsor") or {}
    bot = bal.get("bot") or {}
    if not sponsor.get("configured"):
        reasons.append("sponsor pubkey missing")
    elif sponsor.get("sol") is None:
        reasons.append("sponsor balance unknown")
    elif float(sponsor.get("sol") or 0) < 0.01:
        reasons.append(
            f"sponsor underfunded ({sponsor.get('sol'):.4f} SOL < 0.01)"
        )
    if not bot.get("configured"):
        reasons.append("bot pubkey missing")
    elif bot.get("sol") is None:
        reasons.append("bot balance unknown")
    elif float(bot.get("sol") or 0) < 0.05:
        reasons.append(
            f"bot underfunded ({bot.get('sol'):.4f} SOL < 0.05)"
        )
    return (len(reasons) == 0), reasons


def _pk(s: str):
    from solders.pubkey import Pubkey
    return Pubkey.from_string(s)


def _sign_versioned_b64(swap_b64: str, *kps) -> bytes:
    from solders.transaction import VersionedTransaction
    raw = base64.b64decode(swap_b64)
    tx = VersionedTransaction.from_bytes(raw)
    signed = VersionedTransaction(tx.message, list(kps))
    return bytes(signed)


def _b58encode(raw: bytes) -> str:
    import base58  # type: ignore
    return base58.b58encode(raw).decode("ascii")


def _ata_addr(owner: str, mint: str, token_program: str = TOKEN_PROGRAM) -> str:
    from solders.pubkey import Pubkey
    ata, _bump = Pubkey.find_program_address(
        [bytes(_pk(owner)), bytes(_pk(token_program)), bytes(_pk(mint))],
        _pk(ATA_PROGRAM),
    )
    return str(ata)


def _market_authority(market: str) -> str:
    auth = (_LENDING_MARKET.get("authority") or "").strip()
    if _is_pubkey(auth):
        return auth
    from solders.pubkey import Pubkey
    pk, _bump = Pubkey.find_program_address(
        [bytes(_pk(market))],
        _pk(SOLEND_PROGRAM),
    )
    _LENDING_MARKET["authority"] = str(pk)
    return str(pk)


def _account_info(pk: str) -> dict | None:
    try:
        acc, _ = sol_rpc("getAccountInfo", [pk, {"encoding": "base64"}], timeout=6)
        val = (acc or {}).get("value") if isinstance(acc, dict) else None
        return val if isinstance(val, dict) else None
    except Exception:
        return None


def _mint_token_program(mint: str) -> str:
    if mint == MINT_SOL:
        return TOKEN_PROGRAM
    val = _account_info(mint)
    owner = (val or {}).get("owner") or TOKEN_PROGRAM
    if owner == TOKEN_2022_PROGRAM:
        return TOKEN_2022_PROGRAM
    return TOKEN_PROGRAM


def _spl_amount(ata: str) -> int:
    try:
        acc, _ = sol_rpc("getTokenAccountBalance", [ata], timeout=6)
        val = (acc or {}).get("value") if isinstance(acc, dict) else {}
        return int((val or {}).get("amount") or 0)
    except Exception:
        return 0


def _create_ata_ix(payer: str, owner: str, mint: str, token_program: str,
                   ata: str):
    from solders.instruction import AccountMeta, Instruction
    return Instruction(
        _pk(ATA_PROGRAM),
        bytes([1]),  # CreateIdempotent
        [
            AccountMeta(_pk(payer), True, True),
            AccountMeta(_pk(ata), False, True),
            AccountMeta(_pk(owner), False, False),
            AccountMeta(_pk(mint), False, False),
            AccountMeta(_pk(SYSTEM_PROGRAM), False, False),
            AccountMeta(_pk(token_program), False, False),
        ],
    )


def _jup_swap_tx(quote: dict, user_pk: str,
                 max_prio_lamports: int = 1_000) -> str | None:
    """Build a serialized Jupiter swap tx from a quote (not a second quote)."""
    if not quote or not user_pk:
        return None
    body = {
        "userPublicKey": user_pk,
        "quoteResponse": quote,
        "wrapAndUnwrapSol": True,
        "dynamicComputeUnitLimit": True,
        "prioritizationFeeLamports": max(0, int(max_prio_lamports)),
    }
    try:
        _jup_throttle()
        r = _HTTP.post(JUP_SWAP, json=body, headers=_hdr(), timeout=12)
        if r.status_code != 200:
            return None
        data = r.json()
        tx = (data or {}).get("swapTransaction")
        return tx if isinstance(tx, str) and len(tx) > 80 else None
    except Exception:
        return None


def _json_ix(obj: dict | None):
    """Jupiter instruction JSON → solders Instruction. None if leftover."""
    if not isinstance(obj, dict) or not obj.get("programId") or not obj.get("data"):
        return None
    from solders.instruction import AccountMeta, Instruction
    try:
        data = base64.b64decode(obj["data"])
        metas = []
        for a in obj.get("accounts") or []:
            if not isinstance(a, dict) or not a.get("pubkey"):
                return None
            metas.append(AccountMeta(
                _pk(a["pubkey"]),
                bool(a.get("isSigner") or a.get("is_signer")),
                bool(a.get("isWritable") or a.get("is_writable")),
            ))
        return Instruction(_pk(obj["programId"]), data, metas)
    except Exception:
        return None


def _jup_swap_ixs(quote: dict, user_pk: str, *, wrap: bool = True,
                  cleanup: bool = True
                  ) -> tuple[list | None, list[str], str | None]:
    """Composable Jupiter ixs + ALT pubkeys (not a standalone tx).

    Skip ComputeBudget ixs — callers prepend their own CU limit/price.
    """
    if not quote or not user_pk:
        return None, [], "no quote"
    body = {
        "userPublicKey": user_pk,
        "quoteResponse": quote,
        "wrapAndUnwrapSol": bool(wrap),
        "dynamicComputeUnitLimit": False,
        "prioritizationFeeLamports": 0,
    }
    try:
        _jup_throttle()
        r = _HTTP.post(JUP_SWAP_IX, json=body, headers=_hdr(), timeout=12)
        if r.status_code != 200:
            return None, [], f"jupiter ix HTTP {r.status_code}"
        data = r.json()
    except Exception as e:  # noqa: BLE001
        return None, [], f"jupiter ix: {type(e).__name__}"
    if not isinstance(data, dict):
        return None, [], "jupiter ix empty"
    ixs = []
    for obj in data.get("setupInstructions") or []:
        if (obj.get("programId") or "") == COMPUTE_BUDGET_PROGRAM:
            continue
        ix = _json_ix(obj)
        if ix is None:
            return None, [], "jupiter setup ix leftover"
        ixs.append(ix)
    swap = _json_ix(data.get("swapInstruction"))
    if swap is None:
        return None, [], "jupiter swap ix leftover"
    ixs.append(swap)
    if cleanup:
        clean = data.get("cleanupInstruction")
        if clean:
            if (clean.get("programId") or "") != COMPUTE_BUDGET_PROGRAM:
                cix = _json_ix(clean)
                if cix is None:
                    return None, [], "jupiter cleanup ix leftover"
                ixs.append(cix)
    alts = [a for a in (data.get("addressLookupTableAddresses") or [])
            if isinstance(a, str) and _is_pubkey(a)]
    return ixs, alts, None


def _parse_alt_addresses(raw: bytes):
    """Prefer solders native deserialize; fall back to LOOKUP_TABLE_META_SIZE."""
    if not raw:
        return None
    try:
        from solders.address_lookup_table_account import AddressLookupTable
        try:
            tbl = AddressLookupTable.deserialize(raw)
            addrs = list(tbl.addresses)
            if addrs:
                return addrs
        except Exception:
            try:
                tbl = AddressLookupTable.from_bytes(raw)
                addrs = list(tbl.addresses)
                if addrs:
                    return addrs
            except Exception:
                pass
    except Exception:
        pass
    try:
        from solders.address_lookup_table_account import LOOKUP_TABLE_META_SIZE
        meta = int(LOOKUP_TABLE_META_SIZE)
    except Exception:
        meta = LOOKUP_TABLE_META
    blob = raw[meta:] if len(raw) >= meta else b""
    n = len(blob) // 32
    if n <= 0:
        return None
    addrs = []
    for i in range(n):
        try:
            addrs.append(_pk_bytes(blob[i * 32:(i + 1) * 32]))
        except Exception:
            return None
    return addrs or None


def _load_alts(pks: list[str]) -> tuple[list, list[str]]:
    """Fetch Address Lookup Tables. Failed pubkeys are leftover, not fatal."""
    if not pks:
        return [], []
    from solders.address_lookup_table_account import AddressLookupTableAccount
    accs = _get_multiple_accounts(pks, timeout=8)
    out = []
    failed = []
    for pk in pks:
        val = accs.get(pk)
        if not val:
            failed.append(pk)
            continue
        raw = _acc_bytes(val)
        addrs = _parse_alt_addresses(raw)
        if not addrs:
            failed.append(pk)
            continue
        try:
            out.append(AddressLookupTableAccount(key=_pk(pk), addresses=addrs))
        except Exception:
            failed.append(pk)
    return out, failed


def _pk_bytes(b: bytes):
    from solders.pubkey import Pubkey
    return Pubkey.from_bytes(b)


def _build_tip_tx(payer_kp, tip_account: str, lamports: int,
                  blockhash: str) -> bytes:
    from solders.hash import Hash
    from solders.system_program import TransferParams, transfer
    from solders.transaction import Transaction
    ix = transfer(TransferParams(
        from_pubkey=payer_kp.pubkey(),
        to_pubkey=_pk(tip_account),
        lamports=max(int(lamports), JITO_MIN_TIP_LAMPORTS),
    ))
    tx = Transaction.new_signed_with_payer(
        [ix],
        payer_kp.pubkey(),
        [payer_kp],
        Hash.from_string(blockhash),
    )
    return bytes(tx)


def _jito_send_bundle(raw_txs: list[bytes]) -> tuple[str | None, str | None]:
    """Return (bundle_id, error)."""
    if not raw_txs:
        return None, "empty bundle"
    encoded = [_b58encode(t) for t in raw_txs]
    uuid = (os.environ.get("JITO_UUID") or os.environ.get("JITO_AUTH") or "").strip()
    last_err = "no jito endpoint"
    for url in JITO_BUNDLE_URLS:
        dest = url if "?" in url or not uuid else f"{url}?uuid={uuid}"
        try:
            r = _HTTP.post(
                dest,
                json={"jsonrpc": "2.0", "id": 1, "method": "sendBundle",
                      "params": [encoded]},
                headers=_hdr(),
                timeout=10,
            )
            out = r.json()
            if out.get("result"):
                return str(out["result"]), None
            err = out.get("error")
            last_err = str(err.get("message") if isinstance(err, dict) else err)
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {e}"
            continue
    return None, last_err


def _sim_tx_detail(raw: bytes) -> dict:
    b64 = base64.b64encode(raw).decode("ascii")
    out = {"err": None, "logs": [], "units": 0}
    try:
        res, _ = sol_rpc(
            "simulateTransaction",
            [b64, {
                "encoding": "base64",
                "sigVerify": False,
                "replaceRecentBlockhash": True,
                "commitment": "processed",
            }],
            timeout=12,
        )
        val = (res or {}).get("value") if isinstance(res, dict) else {}
        err = (val or {}).get("err")
        out["err"] = str(err)[:240] if err else None
        out["logs"] = list((val or {}).get("logs") or [])[:40]
        try:
            out["units"] = int((val or {}).get("unitsConsumed") or 0)
        except (TypeError, ValueError):
            out["units"] = 0
        return out
    except Exception as e:  # noqa: BLE001
        out["err"] = f"{type(e).__name__}: {e}"
        return out


def _sim_tx_err(raw: bytes) -> str | None:
    return _sim_tx_detail(raw).get("err")


def _flash_sim_kind(err: str | None, logs: list | None) -> str | None:
    blob = " ".join([err or ""] + list(logs or [])).lower()
    if not blob.strip():
        return None
    if "notwhitelisted" in blob or "not whitelisted" in blob:
        return "whitelist"
    if "flash" in blob and ("disable" in blob or "not enabled" in blob):
        return "flash_disabled"
    if "insufficient" in blob and "liquidity" in blob:
        return "liquidity"
    if "computational budget" in blob or "exceeded cUs" in blob.lower() or (
            "exceeded the remaining" in blob):
        return "cu"
    if "max loaded accounts" in blob or "account is not" in blob:
        return "accounts"
    if "tokenkeg" in blob and "2022" in blob:
        return "token-2022"
    return None


def _ix_flash_borrow_index(ixs) -> int | None:
    for i, ix in enumerate(ixs):
        try:
            if str(ix.program_id) != SOLEND_PROGRAM:
                continue
            data = bytes(ix.data)
            if data[:1] == bytes([SOLEND_IX_FLASH_BORROW]):
                return i
        except Exception:
            continue
    return None


def _set_cu_limit(ixs, limit: int):
    from solders.compute_budget import set_compute_unit_limit
    if ixs:
        ixs[0] = set_compute_unit_limit(int(limit))


def _compile_flash_vtx(bot_kp, ixs, blockhash: str, alt_pks: list[str]):
    """Compile v0. Prefer ALTs; leftover failed tables only. Never invent ALTs."""
    market_lut = _LENDING_MARKET.get("lookup_table") or ""
    pks = list(dict.fromkeys(
        [p for p in (alt_pks or []) if _is_pubkey(p)]
        + ([market_lut] if _is_pubkey(market_lut) else [])
    ))
    alts, failed = _load_alts(pks)
    leftovers = [f"alt missing {p}" for p in failed]
    try:
        raw = _compile_v0(bot_kp, ixs, blockhash, alts)
        return raw, leftovers, None
    except Exception as e:  # noqa: BLE001
        if alts:
            try:
                raw = _compile_v0(bot_kp, ixs, blockhash, [])
                leftovers.append("compiled without ALTs")
                return raw, leftovers, None
            except Exception as e2:  # noqa: BLE001
                return None, leftovers, e2
        return None, leftovers, e


def _fresh_arb_quotes(plan: dict, amount_in: int,
                      slip_bps: int | None = None,
                      max_accounts: int | None = None
                      ) -> tuple[list[dict], str | None]:
    """Re-quote the planned path immediately before /swap. No invented hops."""
    legs = list(plan.get("legs") or [])
    inp = plan.get("input_mint") or ""
    mid = plan.get("output_mint") or plan.get("mid_mint") or ""
    if not legs and inp and mid:
        legs = [
            {"input_mint": inp, "output_mint": mid, "amount": amount_in,
             "exclude": None, "only_direct": True},
            {"input_mint": mid, "output_mint": inp, "amount": 0,
             "exclude": None, "only_direct": True},
        ]
    if not legs:
        return [], "no hop legs"
    quotes = []
    amt = int(amount_in)
    seen: list[str] = []
    for i, leg in enumerate(legs):
        src = leg.get("input_mint") or ""
        dst = leg.get("output_mint") or ""
        if i == 0:
            amt = int(leg.get("amount") or amount_in)
        if not src or not dst or amt <= 0:
            return [], f"incomplete leg {i}"
        excl = _exclude_csv(seen) if seen else (leg.get("exclude") or None)
        only = bool(leg.get("only_direct", True))
        bps = int(slip_bps or _slip_bps(src, dst))
        dexes = leg.get("dexes") or None
        q, _n = _leg(src, dst, amt, bps, src in TAIL_MINTS or dst in TAIL_MINTS,
                     exclude=excl, only_direct=only, dexes=dexes,
                     max_accounts=max_accounts)
        if not q:
            return [], f"no route on hop {i}"
        quotes.append(q)
        seen.extend(_route_labels(q))
        amt = int(q.get("outAmount") or 0)
    return quotes, None


def _arb_still_plus_ev(quotes: list[dict], plan: dict, sol_px: float,
                       amount_in: int, pressure: str | None,
                       floor: float) -> tuple[bool, float, int, str]:
    if not quotes:
        return False, 0.0, 0, "no quotes"
    inp = plan.get("input_mint") or (quotes[0].get("inputMint") or "")
    back = int(quotes[-1].get("outAmount") or 0)
    hops = sum(len(q.get("routePlan") or []) or 1 for q in quotes)
    thresh = int(quotes[-1].get("otherAmountThreshold") or back)
    notional = _native_usd(inp, amount_in, sol_px)
    gross = _native_usd(inp, back - amount_in, sol_px)
    slip_cap = notional * (_slip_bps(inp) / 10_000.0) * min(hops, 2)
    thresh_hair = _native_usd(inp, max(0, back - thresh), sol_px)
    slip = min(thresh_hair if thresh_hair > 0 else slip_cap, slip_cap)
    cu_usd = _cu_fee_usd(plan.get("priority_fee_ul"), sol_px, _cu_estimate(hops))
    pre = gross - slip - cu_usd
    jito_lam = _dynamic_jito_lamports(pre, sol_px, pressure, floor)
    jito_usd = (jito_lam / 1e9) * max(sol_px, 0.0)
    net = pre - jito_usd
    if plan.get("use_flash"):
        bps = int(plan.get("flash_fee_bps") or SOLEND_FLASH_FEE_BPS_DEFAULT)
        net -= notional * (bps / 10_000.0)
    if net <= 0:
        return False, net, jito_lam, f"non-positive net ${net:.4f} after tip"
    if net < floor:
        return False, net, jito_lam, f"below floor ${floor} (net ${net:.4f})"
    labs1 = set(_route_labels(quotes[0])) if quotes else set()
    labs2 = set(_route_labels(quotes[1])) if len(quotes) > 1 else set()
    if len(quotes) == 2 and labs1 == labs2 and len(labs1) <= 1:
        return False, net, jito_lam, "same-pool"
    return True, net, jito_lam, ""


def _solend_flashable(mint: str) -> dict | None:
    _ensure_solend_index()
    row = _RESERVE_INDEX.get(mint) or {}
    addr = row.get("address") or mint
    if addr in _FLASH_DISABLED:
        return None
    if not row.get("liquidity_supply") or not row.get("fee_receiver"):
        _hydrate_reserves_onchain([addr] if _is_pubkey(addr) else [])
        row = _RESERVE_INDEX.get(mint) or _RESERVE_INDEX.get(addr) or {}
    if not row.get("liquidity_supply") or not row.get("fee_receiver"):
        return None
    if not (row.get("address") or row.get("mint")):
        return None
    if not _LENDING_MARKET.get("address"):
        mkt = row.get("lending_market") or ""
        if _is_pubkey(mkt):
            _LENDING_MARKET["address"] = mkt
    return row


def _live_send_arb_flash(plan, funds, rec, bot_kp, sp_kp, quotes,
                         amount, inp, sol_px, net, jito_lam) -> dict:
    """Single-tx Solend flash + composed Jupiter hops + flash repay."""
    bot_pk = str(bot_kp.pubkey())
    _ensure_solend_index()
    cfg = _solend_flashable(inp)
    if not cfg:
        rec["stage"] = "skip"
        rec["detail"] = "flash arb leftover — mint is not a Solend reserve"
        rec["reasons"] = ["no solend reserve"]
        rec["leftover"] = ["no invented flash remaining"]
        return rec
    reserve = cfg.get("address") or ""
    _hydrate_reserves_onchain([reserve] if _is_pubkey(reserve) else [])
    cfg = _RESERVE_INDEX.get(reserve) or cfg
    supply = cfg.get("liquidity_supply") or ""
    fee_recv = cfg.get("fee_receiver") or ""
    market = _LENDING_MARKET.get("address") or cfg.get("lending_market") or ""
    if not (reserve and supply and fee_recv and market):
        rec["stage"] = "blocked"
        rec["detail"] = "flash arb leftover — market/reserve remaining unknown"
        rec["reasons"] = ["flash remaining"]
        return rec
    tok = _mint_token_program(inp)
    if tok == TOKEN_2022_PROGRAM:
        rec["stage"] = "blocked"
        rec["detail"] = "flash leftover — Token-2022 (Solend flash uses TOKEN_PROGRAM)"
        rec["reasons"] = ["token-2022"]
        rec["leftover"] = ["Token-2022 remaining not in solend-sdk flash keys"]
        return rec
    avail = int(cfg.get("available_amount") or 0)
    if avail > 0 and int(amount) > avail:
        amount = int(avail)
        quotes, qerr = _fresh_arb_quotes(plan, amount)
        if qerr or not quotes:
            rec["stage"] = "skip"
            rec["detail"] = f"flash skip — requote after liquidity cap: {qerr}"
            rec["reasons"] = [qerr or "no quotes"]
            return rec
        ok, net, jito_lam, why = _arb_still_plus_ev(
            quotes, plan, sol_px, amount, plan.get("pressure"),
            float(plan.get("min_floor_usd") or min_sol_arb_usd()))
        if not ok:
            rec["stage"] = "skip"
            rec["detail"] = f"LIVE flash arb skip: {why}"
            rec["reasons"] = [why]
            rec["net_usd"] = round(net, 6)
            return rec
    ata = _ata_addr(bot_pk, inp, tok)
    auth = _market_authority(market)
    all_ixs, alt_pks = [], []
    for q in quotes:
        hop, alts, err = _jup_swap_ixs(q, bot_pk, wrap=False, cleanup=False)
        if err or not hop:
            rec["stage"] = "blocked"
            rec["detail"] = "flash leftover — " + (err or "jupiter hop ix")
            rec["reasons"] = [err or "jupiter hop"]
            return rec
        all_ixs.extend(hop)
        alt_pks.extend(alts)
    from solders.compute_budget import set_compute_unit_limit, set_compute_unit_price
    cu_limit = int(plan.get("compute_units") or 1_000_000)
    ixs = [
        set_compute_unit_limit(cu_limit),
        set_compute_unit_price(int(plan.get("priority_fee_ul") or 1_000)),
    ]
    if not _account_info(ata):
        ixs.append(_create_ata_ix(bot_pk, bot_pk, inp, tok, ata))
    ixs.append(_flash_borrow_ix(
        amount, supply, ata, reserve, market, auth, TOKEN_PROGRAM))
    ixs.extend(all_ixs)
    bidx = _ix_flash_borrow_index(ixs)
    if bidx is None:
        rec["stage"] = "blocked"
        rec["detail"] = "flash leftover — FlashBorrow missing after build"
        rec["reasons"] = ["borrow_instruction_index"]
        return rec
    ixs.append(_flash_repay_ix(
        amount, bidx, ata, supply, fee_recv, ata,
        reserve, market, bot_pk, TOKEN_PROGRAM))
    bh = (latest_blockhash().get("blockhash") or "")
    if not bh:
        rec["stage"] = "blocked"
        rec["detail"] = "no recent blockhash for flash arb"
        rec["reasons"] = ["blockhash"]
        return rec
    raw = None
    last_left: list[str] = []
    last_err = "compile"
    for max_acc_try in (0, 1):
        if max_acc_try == 1:
            slim, qerr = _fresh_arb_quotes(plan, amount, max_accounts=16)
            if qerr or not slim:
                last_left.append("maxAccounts=16 no route")
                break
            all_ixs, alt_pks = [], []
            for q in slim:
                hop, alts, err = _jup_swap_ixs(
                    q, bot_pk, wrap=False, cleanup=False)
                if err or not hop:
                    last_left.append(err or "jupiter hop")
                    all_ixs = []
                    break
                all_ixs.extend(hop)
                alt_pks.extend(alts)
            if not all_ixs:
                break
            ixs = [
                set_compute_unit_limit(cu_limit),
                set_compute_unit_price(int(plan.get("priority_fee_ul") or 1_000)),
            ]
            if not _account_info(ata):
                ixs.append(_create_ata_ix(bot_pk, bot_pk, inp, tok, ata))
            ixs.append(_flash_borrow_ix(
                amount, supply, ata, reserve, market, auth, TOKEN_PROGRAM))
            ixs.extend(all_ixs)
            bidx = _ix_flash_borrow_index(ixs)
            if bidx is None:
                break
            ixs.append(_flash_repay_ix(
                amount, bidx, ata, supply, fee_recv, ata,
                reserve, market, bot_pk, TOKEN_PROGRAM))
        raw, leftovers, cerr = _compile_flash_vtx(bot_kp, ixs, bh, alt_pks)
        last_left.extend(leftovers)
        if cerr or raw is None:
            last_err = f"{type(cerr).__name__}: {cerr}" if cerr else "compile"
            continue
        detail = _sim_tx_detail(raw)
        sim_err = detail.get("err")
        kind = _flash_sim_kind(sim_err, detail.get("logs"))
        if kind == "cu" or (
                detail.get("units")
                and detail["units"] > int(cu_limit * 0.92)
                and cu_limit < 1_200_000):
            cu_limit = min(1_200_000, max(cu_limit + 200_000, 900_000))
            _set_cu_limit(ixs, cu_limit)
            raw2, leftovers2, cerr2 = _compile_flash_vtx(bot_kp, ixs, bh, alt_pks)
            last_left.extend(leftovers2)
            if raw2 is not None and not cerr2:
                raw = raw2
                detail = _sim_tx_detail(raw)
                sim_err = detail.get("err")
                kind = _flash_sim_kind(sim_err, detail.get("logs"))
        if kind == "flash_disabled":
            _FLASH_DISABLED.add(reserve)
            rec["stage"] = "skip"
            rec["detail"] = "flash skip — reserve has flash loans disabled"
            rec["reasons"] = ["flash_disabled"]
            rec["flash_skip"] = "flash_disabled"
            rec["leftover"] = last_left
            return rec
        if kind == "whitelist":
            rec["stage"] = "blocked"
            rec["detail"] = "flash leftover — liquidator whitelist (bot not listed)"
            rec["reasons"] = ["liquidator_whitelist"]
            rec["flash_skip"] = "whitelist"
            rec["leftover"] = ["no invented whitelist PDA"]
            return rec
        if sim_err:
            last_err = sim_err
            rec["leftover"] = last_left or ["flash + Jupiter hops must fit one tx"]
            if kind == "accounts" and max_acc_try == 0:
                continue
            rec["stage"] = "blocked"
            rec["detail"] = f"flash arb simulate failed: {sim_err}"
            rec["reasons"] = [sim_err]
            return rec
        last_err = ""
        break
    if last_err or raw is None:
        rec["stage"] = "blocked"
        rec["detail"] = f"flash leftover — {last_err or 'compile produced no tx'}"
        rec["reasons"] = [str(last_err or "compile")]
        rec["leftover"] = last_left or ["packet size / ALT leftover"]
        return rec
    tip_acct = plan.get("jito_tip_account") or _jito_tip_account(bot_pk)
    sp_sol = float(((funds or {}).get("sponsor") or {}).get("sol") or 0)
    if sp_kp is not None and sp_sol >= 0.01:
        tip_raw = _build_tip_tx(sp_kp, tip_acct, jito_lam, bh)
        tip_payer = "sponsor"
    else:
        tip_raw = _build_tip_tx(bot_kp, tip_acct, jito_lam, bh)
        tip_payer = "bot"
    bid, err = _jito_send_bundle([raw, tip_raw])
    rec["jito_tip_lamports"] = jito_lam
    rec["tip_payer"] = tip_payer
    rec["execute"] = "solend-flash-jup-jito"
    rec["flash"] = True
    rec["flash_fee_bps"] = _flash_fee_bps(cfg)
    rec["net_usd"] = round(net, 6)
    rec["leftover"] = last_left
    if not bid:
        rec["stage"] = "blocked"
        rec["detail"] = f"Jito sendBundle failed: {err}"
        rec["reasons"] = [str(err)]
        return rec
    rec["stage"] = "sent"
    rec["bundle_id"] = bid
    rec["sig"] = bid
    rec["detail"] = (
        f"LIVE flash arb Jito {bid[:12]}… net=${net:.4f} "
        f"size={amount} tip={jito_lam}lam"
    )
    plan["jito_bundle"] = True
    plan["mode"] = "live"
    rec["plan"] = plan
    return rec


def _live_send_arb(plan: dict, funds: dict | None, rec: dict) -> dict:
    path = _bot_keypair_path()
    bot_kp = _load_solders_keypair(path)
    bot_pk = str(bot_kp.pubkey())
    sp_path = _sponsor_keypair_path()
    sp_kp = _load_solders_keypair(sp_path) if sp_path else None
    sol_px = float(plan.get("sol_px") or fetch_sol_price() or 0)
    pressure = plan.get("pressure")
    floor = float(plan.get("min_floor_usd") or min_sol_arb_usd())
    amount = int(plan.get("amount_in") or 0)
    inp = plan.get("input_mint") or ""
    if amount <= 0 or not inp:
        rec["stage"] = "blocked"
        rec["detail"] = "LIVE arb blocked: missing amount/mint"
        rec["reasons"] = ["missing amount_in or input_mint"]
        return rec
    bot_lam = int(((funds or {}).get("bot") or {}).get("lamports") or 0)
    if not bot_lam:
        try:
            res, _ = sol_rpc("getBalance", [bot_pk], timeout=6)
            bot_lam = int((res or {}).get("value") or 0) if isinstance(res, dict) else int(res or 0)
        except Exception:
            bot_lam = 0
    tip_guess = int(plan.get("jito_tip_lamports") or JITO_MIN_TIP_LAMPORTS)
    if inp == MINT_SOL:
        leave = 2_000_000 + tip_guess
        have_ok = bot_lam >= amount + leave
        if not have_ok:
            rec["_want_flash"] = True
        else:
            amount = min(amount, max(bot_lam - leave, 0))
            if amount < 50_000_000:  # 0.05 SOL
                rec["stage"] = "skip"
                rec["detail"] = "bot SOL inventory too small for hop-1"
                rec["reasons"] = ["bot inventory"]
                return rec
    else:
        tok_prog = _mint_token_program(inp)
        ata = _ata_addr(bot_pk, inp, tok_prog)
        have = _spl_amount(ata)
        if have < amount:
            rec["_want_flash"] = True
    quotes, qerr = _fresh_arb_quotes(plan, amount)
    if qerr or not quotes:
        rec["stage"] = "skip"
        rec["detail"] = f"LIVE arb skip: {qerr or 'no quotes'}"
        rec["reasons"] = [qerr or "no quotes"]
        return rec
    ok, net, jito_lam, why = _arb_still_plus_ev(
        quotes, plan, sol_px, amount, pressure, floor)
    if rec.get("_want_flash") and not plan.get("use_flash"):
        plan = dict(plan)
        plan["use_flash"] = True
        plan["flash_fee_bps"] = _flash_fee_bps(_solend_flashable(inp) or {})
        ok, net, jito_lam, why = _arb_still_plus_ev(
            quotes, plan, sol_px, amount, pressure, floor)
    if rec.pop("_want_flash", None):
        if not ok:
            rec["stage"] = "skip"
            rec["detail"] = f"LIVE flash arb skip: {why}"
            rec["reasons"] = [why]
            rec["net_usd"] = round(net, 6)
            return rec
        return _live_send_arb_flash(
            plan, funds, rec, bot_kp, sp_kp, quotes,
            amount, inp, sol_px, net, jito_lam)
    if not ok:
        rec["stage"] = "skip"
        rec["detail"] = f"LIVE arb skip: {why}"
        rec["reasons"] = [why]
        rec["net_usd"] = round(net, 6)
        return rec
    signed: list[bytes] = []
    for q in quotes:
        swap_b64 = _jup_swap_tx(q, bot_pk, max_prio_lamports=1_000)
        if not swap_b64:
            rec["stage"] = "blocked"
            rec["detail"] = "Jupiter /swap did not return a tx"
            rec["reasons"] = ["jupiter swap build failed"]
            return rec
        signed.append(_sign_versioned_b64(swap_b64, bot_kp))
    hop1_err = _sim_tx_err(signed[0])
    if hop1_err:
        rec["stage"] = "blocked"
        rec["detail"] = f"hop-1 simulate failed: {hop1_err}"
        rec["reasons"] = [hop1_err]
        return rec
    bh = (latest_blockhash().get("blockhash") or "")
    if not bh:
        rec["stage"] = "blocked"
        rec["detail"] = "no recent blockhash for Jito tip"
        rec["reasons"] = ["blockhash"]
        return rec
    tip_acct = plan.get("jito_tip_account") or _jito_tip_account(bot_pk)
    sp_sol = float(((funds or {}).get("sponsor") or {}).get("sol") or 0)
    if sp_kp is not None and sp_sol >= 0.01:
        tip_raw = _build_tip_tx(sp_kp, tip_acct, jito_lam, bh)
        tip_payer = "sponsor"
    else:
        tip_raw = _build_tip_tx(bot_kp, tip_acct, jito_lam, bh)
        tip_payer = "bot"
    bundle = signed + [tip_raw]
    bid, err = _jito_send_bundle(bundle)
    rec["net_usd"] = round(net, 6)
    rec["jito_tip_lamports"] = jito_lam
    rec["tip_payer"] = tip_payer
    rec["execute"] = "jupiter-jito"
    if not bid:
        rec["stage"] = "blocked"
        rec["detail"] = f"Jito sendBundle failed: {err}"
        rec["reasons"] = [str(err)]
        return rec
    rec["stage"] = "sent"
    rec["bundle_id"] = bid
    rec["sig"] = bid
    rec["detail"] = (
        f"LIVE arb Jito bundle {bid[:12]}… net=${net:.4f} "
        f"tip={jito_lam}lam payer={tip_payer} hops={len(signed)}"
    )
    plan["jito_bundle"] = True
    plan["jito_tip_lamports"] = jito_lam
    plan["mode"] = "live"
    rec["plan"] = plan
    return rec


def _solend_ix(tag: int, data: bytes, accounts: list[tuple[str, bool, bool]]):
    from solders.instruction import AccountMeta, Instruction
    metas = [AccountMeta(_pk(pk), sig, w) for pk, sig, w in accounts]
    return Instruction(_pk(SOLEND_PROGRAM), bytes([tag]) + data, metas)


def _compile_v0(payer_kp, ixs, blockhash: str, alts: list | None = None) -> bytes:
    from solders.hash import Hash
    from solders.message import MessageV0
    from solders.transaction import VersionedTransaction
    msg = MessageV0.try_compile(
        payer_kp.pubkey(),
        ixs,
        alts or [],
        Hash.from_string(blockhash),
    )
    return bytes(VersionedTransaction(msg, [payer_kp]))


def _flash_borrow_ix(amount: int, source_liq: str, dest_ata: str,
                     reserve: str, market: str, auth: str, tok: str):
    return _solend_ix(SOLEND_IX_FLASH_BORROW, int(amount).to_bytes(8, "little"), [
        (source_liq, False, True),
        (dest_ata, False, True),
        (reserve, False, True),
        (market, False, False),
        (auth, False, False),
        (SYSVAR_INSTRUCTIONS, False, False),
        (tok, False, False),
    ])


def _flash_repay_ix(amount: int, borrow_index: int, src_ata: str, dest_liq: str,
                    fee_recv: str, host_recv: str, reserve: str, market: str,
                    user: str, tok: str):
    data = int(amount).to_bytes(8, "little") + bytes([int(borrow_index) & 0xFF])
    return _solend_ix(SOLEND_IX_FLASH_REPAY, data, [
        (src_ata, False, True),
        (dest_liq, False, True),
        (fee_recv, False, True),
        (host_recv, False, True),
        (reserve, False, False),
        (market, False, False),
        (user, True, False),
        (SYSVAR_INSTRUCTIONS, False, False),
        (tok, False, False),
    ])


def _live_send_liq_flash(
        plan, funds, rec, bot_kp, sp_kp, rcfg, wcfg,
        repay_r, withdraw_r, repay_mint, coll_mint, ctoken,
        tok_repay, tok_coll, src_ata, dest_c, dest_liq, repay_amt) -> dict:
    """Atomic: flash-borrow debt → liquidate+redeem → swap coll→debt → flash-repay.

    Same-tx Solend ix 19/20. Jupiter coll→debt only when mints differ.
    """
    bot_pk = str(bot_kp.pubkey())
    _ensure_solend_index()
    _hydrate_reserves_onchain(
        [repay_r, withdraw_r] if repay_r or withdraw_r else [])
    rcfg = _RESERVE_INDEX.get(repay_r) or rcfg
    wcfg = _RESERVE_INDEX.get(withdraw_r) or wcfg
    supply = rcfg.get("liquidity_supply") or ""
    fee_recv = rcfg.get("fee_receiver") or ""
    if not supply or not fee_recv:
        rec["stage"] = "blocked"
        rec["detail"] = "flash leftover — repay reserve supply/fee_receiver unknown"
        rec["reasons"] = ["flash remaining"]
        rec["leftover"] = ["no invented Solend fee receiver"]
        return rec
    if tok_repay == TOKEN_2022_PROGRAM or tok_coll == TOKEN_2022_PROGRAM:
        rec["stage"] = "blocked"
        rec["detail"] = "flash leftover — Token-2022 (Solend flash uses TOKEN_PROGRAM)"
        rec["reasons"] = ["token-2022"]
        rec["leftover"] = ["Token-2022 remaining not in solend-sdk flash keys"]
        return rec
    if repay_r in _FLASH_DISABLED:
        rec["stage"] = "skip"
        rec["detail"] = "flash skip — this debt reserve previously disabled flash"
        rec["reasons"] = ["flash_disabled"]
        rec["flash_skip"] = "flash_disabled"
        return rec
    avail = int(rcfg.get("available_amount") or 0)
    if avail > 0:
        repay_amt = min(int(repay_amt), avail)
    if repay_amt <= 0:
        rec["stage"] = "skip"
        rec["detail"] = "flash skip — reserve available liquidity is 0"
        rec["reasons"] = ["liquidity"]
        rec["flash_skip"] = "liquidity"
        return rec
    flash_bps = _flash_fee_bps(rcfg)
    fee_native = (int(repay_amt) * flash_bps + 9_999) // 10_000
    need_back = int(repay_amt) + fee_native
    same = repay_mint and coll_mint and repay_mint == coll_mint
    jup_ixs, alt_pks = [], []
    if not same:
        bonus = float(plan.get("liq_bonus_pct") or 5) / 100.0
        proto = float(plan.get("liq_protocol_fee_pct") or 0) / 100.0
        seize = max(int(repay_amt * (1.0 + bonus * (1.0 - proto))), repay_amt + 1)
        q = _jup_quote(coll_mint, repay_mint, seize, slippage_bps=50)
        if not q:
            rec["stage"] = "skip"
            rec["detail"] = "flash skip — no Jupiter coll→debt route"
            rec["reasons"] = ["no jup"]
            return rec
        if int(q.get("outAmount") or 0) < need_back:
            rec["stage"] = "skip"
            rec["detail"] = (
                f"flash skip — swap {q.get('outAmount')} < repay+fee {need_back}"
            )
            rec["reasons"] = ["swap<flash+fee"]
            rec["net_usd"] = 0
            return rec
        jup_ixs, alt_pks, jerr = _jup_swap_ixs(
            q, bot_pk, wrap=False, cleanup=False)
        if jerr or not jup_ixs:
            rec["stage"] = "blocked"
            rec["detail"] = "flash leftover — " + (jerr or "jupiter ixs")
            rec["reasons"] = [jerr or "jupiter ixs"]
            rec["leftover"] = ["no fabricated Jupiter remaining accounts"]
            return rec
    market = plan.get("lending_market") or _LENDING_MARKET.get("address")
    auth = _market_authority(market)
    extra_r = _drop_placeholder_pk(rcfg.get("extra_oracle") or "") or None
    extra_w = _drop_placeholder_pk(wcfg.get("extra_oracle") or "") or None
    refresh_accounts_r = [
        (repay_r, False, True),
        (rcfg["pyth"], False, False),
        (rcfg["switchboard"], False, False),
    ]
    if extra_r:
        refresh_accounts_r.append((extra_r, False, False))
    refresh_accounts_w = [
        (withdraw_r, False, True),
        (wcfg["pyth"], False, False),
        (wcfg["switchboard"], False, False),
    ]
    if extra_w:
        refresh_accounts_w.append((extra_w, False, False))
    dep = list(plan.get("deposit_reserves") or [withdraw_r])
    bor = list(plan.get("borrow_reserves") or [repay_r])
    obl_rest = [(pk, False, True) for pk in dep + bor if _is_pubkey(pk)]
    from solders.compute_budget import set_compute_unit_limit, set_compute_unit_price
    cu_limit = int(plan.get("compute_units") or 1_000_000)
    ixs = [
        set_compute_unit_limit(cu_limit),
        set_compute_unit_price(int(plan.get("priority_fee_ul") or 1_000)),
    ]
    if not _account_info(src_ata):
        ixs.append(_create_ata_ix(bot_pk, bot_pk, repay_mint, tok_repay, src_ata))
    if not _account_info(dest_c):
        ixs.append(_create_ata_ix(bot_pk, bot_pk, ctoken, TOKEN_PROGRAM, dest_c))
    if coll_mint and dest_liq != src_ata and not _account_info(dest_liq):
        ixs.append(_create_ata_ix(bot_pk, bot_pk, coll_mint, tok_coll, dest_liq))
    ixs.extend([
        _solend_ix(SOLEND_IX_REFRESH_RESERVE, b"", refresh_accounts_r),
        _solend_ix(SOLEND_IX_REFRESH_RESERVE, b"", refresh_accounts_w),
        _solend_ix(
            SOLEND_IX_REFRESH_OBLIGATION, b"",
            [(plan["obligation"], False, True), *obl_rest],
        ),
    ])
    ixs.append(_flash_borrow_ix(
        repay_amt, supply, src_ata, repay_r, market, auth, TOKEN_PROGRAM))
    liq_accounts = [
        (src_ata, False, True),
        (dest_c, False, True),
        (dest_liq, False, True),
        (repay_r, False, True),
        (rcfg["liquidity_supply"], False, True),
        (withdraw_r, False, True),
        (wcfg["collateral_mint"], False, True),
        (wcfg["collateral_supply"], False, True),
        (wcfg["liquidity_supply"], False, True),
        (wcfg["fee_receiver"], False, True),
        (plan["obligation"], False, True),
        (market, False, True),
        (auth, False, False),
        (bot_pk, True, False),
        (TOKEN_PROGRAM, False, False),
    ]
    ixs.append(_solend_ix(
        SOLEND_IX_LIQUIDATE_AND_REDEEM,
        int(repay_amt).to_bytes(8, "little"),
        liq_accounts,
    ))
    ixs.extend(jup_ixs)
    bidx = _ix_flash_borrow_index(ixs)
    if bidx is None:
        rec["stage"] = "blocked"
        rec["detail"] = "flash leftover — FlashBorrow missing after build"
        rec["reasons"] = ["borrow_instruction_index"]
        return rec
    ixs.append(_flash_repay_ix(
        repay_amt, bidx, src_ata, supply, fee_recv, src_ata,
        repay_r, market, bot_pk, TOKEN_PROGRAM))
    bh = (latest_blockhash().get("blockhash") or "")
    if not bh:
        rec["stage"] = "blocked"
        rec["detail"] = "no recent blockhash"
        rec["reasons"] = ["blockhash"]
        return rec
    raw, leftovers, cerr = _compile_flash_vtx(bot_kp, ixs, bh, alt_pks)
    if cerr or raw is None:
        slim_ok = False
        if alt_pks and not same:
            q2 = _jup_quote(coll_mint, repay_mint, seize, slippage_bps=50,
                            max_accounts=16)
            if q2 and int(q2.get("outAmount") or 0) >= need_back:
                j2, a2, e2 = _jup_swap_ixs(
                    q2, bot_pk, wrap=False, cleanup=False)
                if j2 and not e2:
                    repay_ix = ixs[-1]
                    cut = len(ixs) - 1 - len(jup_ixs)
                    ixs = ixs[:cut] + list(j2) + [repay_ix]
                    raw, leftovers2, cerr = _compile_flash_vtx(
                        bot_kp, ixs, bh, a2)
                    leftovers.extend(leftovers2)
                    leftovers.append("maxAccounts=16")
                    slim_ok = raw is not None and not cerr
        if not slim_ok:
            rec["stage"] = "blocked"
            rec["detail"] = (
                f"flash tx compile failed: {type(cerr).__name__}: {cerr}"
                if cerr else "flash compile leftover"
            )
            rec["reasons"] = [str(cerr)]
            rec["leftover"] = leftovers or ["packed ix + ALT may exceed 1232"]
            return rec
    detail = _sim_tx_detail(raw)
    sim_err = detail.get("err")
    kind = _flash_sim_kind(sim_err, detail.get("logs"))
    if kind == "cu" or (
            detail.get("units")
            and detail["units"] > int(cu_limit * 0.92)
            and cu_limit < 1_200_000):
        cu_limit = min(1_200_000, max(cu_limit + 200_000, 900_000))
        _set_cu_limit(ixs, cu_limit)
        raw2, leftovers2, cerr2 = _compile_flash_vtx(bot_kp, ixs, bh, alt_pks)
        leftovers.extend(leftovers2)
        if raw2 is not None and not cerr2:
            raw = raw2
            detail = _sim_tx_detail(raw)
            sim_err = detail.get("err")
            kind = _flash_sim_kind(sim_err, detail.get("logs"))
    if kind == "flash_disabled":
        _FLASH_DISABLED.add(repay_r)
        rec["stage"] = "skip"
        rec["detail"] = "flash skip — reserve has flash loans disabled"
        rec["reasons"] = ["flash_disabled"]
        rec["flash_skip"] = "flash_disabled"
        rec["leftover"] = leftovers
        return rec
    if kind == "whitelist":
        rec["stage"] = "blocked"
        rec["detail"] = "flash leftover — liquidator whitelist (bot not listed)"
        rec["reasons"] = ["liquidator_whitelist"]
        rec["flash_skip"] = "whitelist"
        rec["leftover"] = ["no invented whitelist PDA"]
        return rec
    if sim_err:
        rec["stage"] = "blocked"
        rec["detail"] = f"flash simulate failed: {sim_err}"
        rec["reasons"] = [sim_err]
        rec["leftover"] = leftovers or [kind or sim_err]
        rec["flash_skip"] = kind
        return rec
    sol_px = float(plan.get("sol_px") or fetch_sol_price() or 0)
    pre = float(plan.get("expected_profit_usd") or 0)
    jito_lam = int(plan.get("jito_tip_lamports") or 0)
    if jito_lam <= 0:
        jito_lam = _dynamic_jito_lamports(pre, sol_px, None, min_sol_liq_usd())
    tip_usd = (jito_lam / 1e9) * max(sol_px, 0.0)
    if pre - tip_usd <= 0:
        rec["stage"] = "skip"
        rec["detail"] = "LIVE flash liq skip — tip would make net ≤ 0"
        rec["reasons"] = ["net<=0 after tip"]
        return rec
    tip_acct = plan.get("jito_tip_account") or _jito_tip_account(bot_pk)
    sp_sol = float(((funds or {}).get("sponsor") or {}).get("sol") or 0)
    if sp_kp is not None and sp_sol >= 0.01:
        tip_raw = _build_tip_tx(sp_kp, tip_acct, jito_lam, bh)
        tip_payer = "sponsor"
    else:
        tip_raw = _build_tip_tx(bot_kp, tip_acct, jito_lam, bh)
        tip_payer = "bot"
    bid, err = _jito_send_bundle([raw, tip_raw])
    rec["jito_tip_lamports"] = jito_lam
    rec["tip_payer"] = tip_payer
    rec["execute"] = "solend-flash-jito"
    rec["flash"] = True
    rec["flash_fee_bps"] = flash_bps
    if not bid:
        rec["stage"] = "blocked"
        rec["detail"] = f"Jito sendBundle failed: {err}"
        rec["reasons"] = [str(err)]
        return rec
    rec["stage"] = "sent"
    rec["bundle_id"] = bid
    rec["sig"] = bid
    rec["detail"] = (
        f"LIVE flash liq Jito {bid[:12]}… obl={(plan.get('obligation') or '')[:6]} "
        f"repay={repay_amt} fee_bps={flash_bps} tip={jito_lam}lam"
    )
    plan["jito_bundle"] = True
    plan["mode"] = "live"
    plan["flash"] = True
    rec["plan"] = plan
    return rec


def _live_send_liq(plan: dict, funds: dict | None, rec: dict) -> dict:
    """Solend liquidate + Jito tip. Remaining gaps are recorded, not invented."""
    gaps = _liq_account_gaps(plan)
    if gaps:
        rec["stage"] = "blocked"
        rec["detail"] = "LIVE liq blocked — account gaps: " + ", ".join(gaps)
        rec["reasons"] = gaps
        rec["leftover"] = [
            g for g in gaps if g == "liquidator_whitelist"
        ] or gaps
        return rec
    path = _bot_keypair_path()
    bot_kp = _load_solders_keypair(path)
    bot_pk = str(bot_kp.pubkey())
    sp_path = _sponsor_keypair_path()
    sp_kp = _load_solders_keypair(sp_path) if sp_path else None
    repay_r = plan.get("debt_reserve")
    withdraw_r = plan.get("coll_reserve")
    rcfg = _RESERVE_INDEX.get(repay_r) or {}
    wcfg = _RESERVE_INDEX.get(withdraw_r) or {}
    repay_mint = plan.get("repay_mint") or rcfg.get("mint") or ""
    coll_mint = plan.get("withdraw_mint") or wcfg.get("mint") or ""
    ctoken = wcfg.get("collateral_mint") or ""
    tok_repay = _mint_token_program(repay_mint)
    tok_coll = _mint_token_program(coll_mint) if coll_mint else TOKEN_PROGRAM
    src_ata = _ata_addr(bot_pk, repay_mint, tok_repay)
    dest_c = _ata_addr(bot_pk, ctoken, TOKEN_PROGRAM)
    dest_liq = _ata_addr(bot_pk, coll_mint, tok_coll)
    debt_amt = int(plan.get("debt_amount") or 0)
    close = float(plan.get("close_factor") or 1.0)
    repay_amt = max(int(debt_amt * close), 1)
    avail = int(rcfg.get("available_amount") or 0)
    if avail > 0:
        repay_amt = min(repay_amt, avail)
    have = _spl_amount(src_ata)
    native_ok = False
    if repay_mint == MINT_SOL:
        bot_lam = int(((funds or {}).get("bot") or {}).get("lamports") or 0)
        native_ok = bot_lam > repay_amt + 3_000_000
    if have < repay_amt and not native_ok:
        slots = list(plan.get("borrow_slots") or [])
        tried = []
        last = None
        candidates = slots or [{
            "reserve": repay_r, "mint": repay_mint, "amount": repay_amt,
        }]
        for slot in candidates:
            rpk = slot.get("reserve") or repay_r
            if rpk in tried or rpk in _FLASH_DISABLED:
                continue
            tried.append(rpk)
            rcfg_s = _RESERVE_INDEX.get(rpk) or rcfg
            mint_s = slot.get("mint") or rcfg_s.get("mint") or repay_mint
            tok_s = _mint_token_program(mint_s) if mint_s else tok_repay
            src_s = _ata_addr(bot_pk, mint_s, tok_s)
            amt_s = int(slot.get("amount") or repay_amt)
            if slot.get("amount"):
                amt_s = max(int(amt_s * close), 1)
            else:
                amt_s = repay_amt
            if amt_s <= 0:
                continue
            rec_s = dict(rec)
            last = _live_send_liq_flash(
                plan, funds, rec_s, bot_kp, sp_kp, rcfg_s, wcfg,
                rpk, withdraw_r, mint_s, coll_mint, ctoken,
                tok_s, tok_coll, src_s, dest_c, dest_liq, amt_s)
            if last.get("stage") == "sent":
                return last
            if last.get("flash_skip") == "flash_disabled":
                continue
            return last
        return last or rec
    market = plan.get("lending_market") or _LENDING_MARKET.get("address")
    auth = _market_authority(market)
    extra_r = _drop_placeholder_pk(rcfg.get("extra_oracle") or "") or None
    extra_w = _drop_placeholder_pk(wcfg.get("extra_oracle") or "") or None
    refresh_accounts_r = [
        (repay_r, False, True),
        (rcfg["pyth"], False, False),
        (rcfg["switchboard"], False, False),
    ]
    if extra_r:
        refresh_accounts_r.append((extra_r, False, False))
    refresh_accounts_w = [
        (withdraw_r, False, True),
        (wcfg["pyth"], False, False),
        (wcfg["switchboard"], False, False),
    ]
    if extra_w:
        refresh_accounts_w.append((extra_w, False, False))
    dep = list(plan.get("deposit_reserves") or [withdraw_r])
    bor = list(plan.get("borrow_reserves") or [repay_r])
    obl_rest = [(pk, False, True) for pk in dep + bor if _is_pubkey(pk)]
    ixs_refresh = [
        _solend_ix(SOLEND_IX_REFRESH_RESERVE, b"", refresh_accounts_r),
        _solend_ix(SOLEND_IX_REFRESH_RESERVE, b"", refresh_accounts_w),
        _solend_ix(
            SOLEND_IX_REFRESH_OBLIGATION, b"",
            [(plan["obligation"], False, True), *obl_rest],
        ),
    ]
    ixs_liq = []
    if not _account_info(dest_c):
        ixs_liq.append(_create_ata_ix(bot_pk, bot_pk, ctoken, TOKEN_PROGRAM, dest_c))
    if not _account_info(dest_liq):
        ixs_liq.append(_create_ata_ix(bot_pk, bot_pk, coll_mint, tok_coll, dest_liq))
    liq_accounts = [
        (src_ata, False, True),
        (dest_c, False, True),
        (dest_liq, False, True),
        (repay_r, False, True),
        (rcfg["liquidity_supply"], False, True),
        (withdraw_r, False, True),
        (wcfg["collateral_mint"], False, True),
        (wcfg["collateral_supply"], False, True),
        (wcfg["liquidity_supply"], False, True),
        (wcfg["fee_receiver"], False, True),
        (plan["obligation"], False, True),
        (market, False, True),
        (auth, False, False),
        (bot_pk, True, False),
        (TOKEN_PROGRAM, False, False),
    ]
    ixs_liq.append(_solend_ix(
        SOLEND_IX_LIQUIDATE_AND_REDEEM,
        int(repay_amt).to_bytes(8, "little"),
        liq_accounts,
    ))
    bh = (latest_blockhash().get("blockhash") or "")
    if not bh:
        rec["stage"] = "blocked"
        rec["detail"] = "no recent blockhash"
        rec["reasons"] = ["blockhash"]
        return rec
    try:
        from solders.compute_budget import set_compute_unit_limit, set_compute_unit_price
        prio_ixs = [
            set_compute_unit_limit(int(plan.get("compute_units") or 400_000)),
            set_compute_unit_price(int(plan.get("priority_fee_ul") or 1_000)),
        ]
    except Exception:
        prio_ixs = []
    try:
        raw_refresh = _compile_v0(bot_kp, ixs_refresh, bh)
        raw_liq = _compile_v0(bot_kp, prio_ixs + ixs_liq, bh)
    except Exception as e:  # noqa: BLE001
        rec["stage"] = "blocked"
        rec["detail"] = f"liq tx compile failed: {type(e).__name__}: {e}"
        rec["reasons"] = [str(e)]
        rec["leftover"] = ["may need address lookup tables for account count"]
        return rec
    sim_err = _sim_tx_err(raw_liq)
    if sim_err:
        rec["stage"] = "blocked"
        rec["detail"] = f"liq simulate failed: {sim_err}"
        rec["reasons"] = [sim_err]
        rec["leftover"] = [
            "common: flash disabled on reserve / insufficient liquidity",
            "common: liquidator whitelist",
            "common: stale oracle / extra oracle meta",
        ]
        return rec
    sol_px = float(plan.get("sol_px") or fetch_sol_price() or 0)
    pre = float(plan.get("expected_profit_usd") or 0)
    jito_lam = int(plan.get("jito_tip_lamports") or 0)
    if jito_lam <= 0:
        jito_lam = _dynamic_jito_lamports(pre, sol_px, None, min_sol_liq_usd())
    # Keep +EV after the tip we actually attach
    tip_usd = (jito_lam / 1e9) * max(sol_px, 0.0)
    if pre - tip_usd <= 0:
        rec["stage"] = "skip"
        rec["detail"] = "LIVE liq skip — tip would make net ≤ 0"
        rec["reasons"] = ["net<=0 after tip"]
        return rec
    tip_acct = plan.get("jito_tip_account") or _jito_tip_account(bot_pk)
    sp_sol = float(((funds or {}).get("sponsor") or {}).get("sol") or 0)
    if sp_kp is not None and sp_sol >= 0.01:
        tip_raw = _build_tip_tx(sp_kp, tip_acct, jito_lam, bh)
        tip_payer = "sponsor"
    else:
        tip_raw = _build_tip_tx(bot_kp, tip_acct, jito_lam, bh)
        tip_payer = "bot"
    bid, err = _jito_send_bundle([raw_refresh, raw_liq, tip_raw])
    rec["jito_tip_lamports"] = jito_lam
    rec["tip_payer"] = tip_payer
    rec["execute"] = "solend-jito"
    if not bid:
        rec["stage"] = "blocked"
        rec["detail"] = f"Jito sendBundle failed: {err}"
        rec["reasons"] = [str(err)]
        return rec
    rec["stage"] = "sent"
    rec["bundle_id"] = bid
    rec["sig"] = bid
    rec["detail"] = (
        f"LIVE liq Jito bundle {bid[:12]}… obl={(plan.get('obligation') or '')[:6]} "
        f"tip={jito_lam}lam payer={tip_payer}"
    )
    plan["jito_bundle"] = True
    plan["mode"] = "live"
    rec["plan"] = plan
    return rec


def submit_sol_plan(plan: dict, *, sim_only: bool, armed: bool,
                    funds: dict | None = None) -> dict[str, Any]:
    """Sim by default. LIVE send = Jupiter /swap (or Solend ix) + Jito bundle.

    Never broadcasts silently. Stub Arb1111/Liq1111 programs are refused.
    +EV-only: net after CU + Jito tip + slip must stay > 0 and ≥ floor.
    """
    kind = (plan or {}).get("kind") or "liq"
    rec: dict[str, Any] = {
        "ts": int(time.time()),
        "kind": kind,
        "plan": plan,
        "stage": "simulated",
        "detail": "",
        "reasons": [],
        "execute": (plan or {}).get("execute") or (
            "solend-jito" if kind == "liq" else "jupiter-jito"),
    }
    profit = (plan or {}).get("expected_profit_usd") or (plan or {}).get(
        "expected_net_usd")
    path = (plan or {}).get("path") or (plan or {}).get("obligation") or ""
    if sim_only or not armed:
        rec["stage"] = "simulated"
        rec["detail"] = (
            f"dry-run {kind} {path} profit=${profit} "
            f"jito_tip={plan.get('jito_tip_lamports', 0)}lam (sim; "
            f"{'jupiter+jito' if kind != 'liq' else 'solend+jito'} when armed)"
        )
        return rec

    pid = (plan or {}).get("program") or ""
    if kind == "arb" and _is_stub_program(pid):
        plan["program"] = JUPITER_V6
        pid = JUPITER_V6
    if kind == "liq" and _is_stub_program(pid):
        plan["program"] = SOLEND_PROGRAM
        pid = SOLEND_PROGRAM
    if _is_stub_program(pid):
        rec["stage"] = "blocked"
        rec["detail"] = "refusing stub program id — would no-op burn CU"
        rec["reasons"] = ["stub program"]
        return rec

    reasons = list(live_submit_blockers(kind))
    funded, fund_rs = wallets_funded_enough(funds)
    if not funded:
        reasons.extend(fund_rs)
    rec["reasons"] = reasons
    if reasons:
        rec["stage"] = "blocked"
        rec["detail"] = f"LIVE {kind} blocked: {reasons[0]}"
        return rec

    try:
        if kind == "arb":
            return _live_send_arb(plan, funds, rec)
        return _live_send_liq(plan, funds, rec)
    except Exception as e:  # noqa: BLE001
        rec["stage"] = "blocked"
        rec["detail"] = f"LIVE {kind} send failed: {type(e).__name__}: {e}"
        rec["reasons"] = [str(e)]
        return rec


def merge_liq_opportunities(*groups: list[dict]) -> list[dict]:
    """Dedupe by obligation/user, keep best net profit / lowest HF."""
    best: dict[str, dict] = {}
    for group in groups:
        for o in group or []:
            key = (o.get("obligation") or o.get("user") or o.get("mint")
                   or json.dumps(o, sort_keys=True, default=str)[:40])
            prev = best.get(key)
            if prev is None:
                best[key] = o
                continue
            p_new = float(o.get("profit_usd") or o.get("net_usd") or -1e9)
            p_old = float(prev.get("profit_usd") or prev.get("net_usd") or -1e9)
            hf_new = o.get("hf")
            hf_old = prev.get("hf")
            if p_new > p_old or (
                hf_new is not None and (hf_old is None or hf_new < hf_old)
            ):
                merged = dict(prev)
                merged.update(o)
                best[key] = merged
    rows = list(best.values())
    rows.sort(key=lambda o: (
        o.get("hf") is None, o.get("hf") or 99,
        -(o.get("profit_usd") or o.get("net_usd") or 0),
    ))
    return rows


def flash_compile_probe(simulate: bool = True) -> dict:
    """No-send MessageV0 compile of Solend USDC flash ixs. Proves remainings.

    Uses API + on-chain decode for USDC reserve. Ephemeral payer if no bot key.
    Never broadcasts.
    """
    out: dict[str, Any] = {
        "ok": False, "compile": False, "simulate": None, "error": None,
        "reserve": None, "borrow_index": None, "leftover": [],
        "flash_fee_bps": None, "available": None, "n_ixs": 0,
    }
    try:
        _ensure_solend_index()
        row = _RESERVE_INDEX.get(MINT_USDC) or {}
        addr = row.get("address") or ""
        if _is_pubkey(addr):
            _hydrate_reserves_onchain([addr])
            row = _RESERVE_INDEX.get(addr) or row
        _hydrate_market_onchain(_LENDING_MARKET.get("address"))
        supply = row.get("liquidity_supply") or ""
        fee_recv = row.get("fee_receiver") or ""
        market = (_LENDING_MARKET.get("address")
                  or row.get("lending_market") or "")
        reserve = row.get("address") or ""
        out["reserve"] = reserve
        out["flash_fee_bps"] = _flash_fee_bps(row)
        out["available"] = row.get("available_amount")
        out["whitelist"] = _LENDING_MARKET.get("whitelist") or ""
        missing = [k for k, v in (
            ("reserve", reserve), ("supply", supply),
            ("fee_receiver", fee_recv), ("market", market),
        ) if not _is_pubkey(v)]
        if missing:
            out["error"] = "missing remainings: " + ",".join(missing)
            out["leftover"] = missing
            return out
        auth = _market_authority(market)
        from solders.compute_budget import (
            set_compute_unit_limit, set_compute_unit_price)
        from solders.hash import Hash
        from solders.keypair import Keypair
        from solders.message import MessageV0
        path = _bot_keypair_path()
        if path:
            payer = _load_solders_keypair(path)
        else:
            payer = Keypair()
            out["leftover"].append("ephemeral payer (no bot.json) — compile only")
        pk = str(payer.pubkey())
        ata = _ata_addr(pk, MINT_USDC, TOKEN_PROGRAM)
        ixs = [
            set_compute_unit_limit(1_000_000),
            set_compute_unit_price(1_000),
            _create_ata_ix(pk, pk, MINT_USDC, TOKEN_PROGRAM, ata),
            _flash_borrow_ix(
                1, supply, ata, reserve, market, auth, TOKEN_PROGRAM),
        ]
        bidx = _ix_flash_borrow_index(ixs)
        out["borrow_index"] = bidx
        if bidx is None:
            out["error"] = "FlashBorrow index missing"
            return out
        ixs.append(_flash_repay_ix(
            1, bidx, ata, supply, fee_recv, ata,
            reserve, market, pk, TOKEN_PROGRAM))
        out["n_ixs"] = len(ixs)
        bh = (latest_blockhash().get("blockhash") or "")
        if not bh:
            bh = "11111111111111111111111111111111"
            out["leftover"].append("no recent blockhash — dummy Hash for compile")
        lut = _LENDING_MARKET.get("lookup_table") or ""
        alts, failed = _load_alts([lut] if _is_pubkey(lut) else [])
        out["leftover"].extend(f"alt missing {p}" for p in failed)
        msg = MessageV0.try_compile(
            payer.pubkey(), ixs, alts, Hash.from_string(bh),
        )
        out["compile"] = True
        out["msg_bytes"] = len(bytes(msg))
        if simulate and path:
            from solders.transaction import VersionedTransaction
            raw = bytes(VersionedTransaction(msg, [payer]))
            detail = _sim_tx_detail(raw)
            out["simulate"] = detail.get("err") or "ok"
            out["sim_units"] = detail.get("units")
            out["sim_logs"] = (detail.get("logs") or [])[-6:]
            kind = _flash_sim_kind(detail.get("err"), detail.get("logs"))
            if kind:
                out["sim_kind"] = kind
        out["ok"] = True
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"
    return out
