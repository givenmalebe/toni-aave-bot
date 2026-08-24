"""Kamino Finance (kLend) adapter for SOL liquidation scanning.

Kamino uses a Solana lending program forked from Solend/Save with modified
obligation layout. Program ID: KLend2g3cP87fffoy8q1mQqGKjrxjC8boSyAYavgmjD

Obligation layout (official klend IDL, validated on-chain 2026-08-21:
collateral marketValue sum == depositedValueSf on 53/53 live accounts):
  - discriminator(8): a8ce8d6a584caca7
  - tag(u64) @ [8:16]
  - last_update.slot(u64 LE) @ [16:24], stale(u8) @ [24], price_status @ [25]
  - lending_market(Pubkey=32) @ [32:64]
  - owner(Pubkey=32) @ [64:96]
  - deposits[8] @ [96:1184], stride 136:
      deposit_reserve(Pubkey) @[+0:+32]
      deposited_amount(u64)   @[+32:+40]
      market_value_sf(u128, USD WAD 1e18) @[+40:+56]
  - lowest_reserve_deposit_liquidation_ltv(u64) @ [1184:1192]
  - deposited_value_sf(u128 WAD) @ [1192:1208]
  - borrows[5] @ [1208:2208], stride 200:
      borrow_reserve(Pubkey) @[+0:+32]
      cumulative_borrow_rate_bsf(48B BigFractionBytes) @[+32:+80]
      last_borrowed_at_ts(u64) @[+80:+88]
      borrowed_amount_sf(u128) @[+88:+104]
      market_value_sf(u128, USD WAD) @[+104:+120]
      borrow_factor_adjusted_market_value_sf(u128) @[+120:+136]
  - borrow_factor_adjusted_debt_value_sf(u128) @ [2208:2224]
  - borrowed_assets_market_value_sf(u128) @ [2224:2240]
  - allowed_borrow_value_sf(u128) @ [2240:2256]
  - unhealthy_borrow_value_sf(u128) @ [2256:2272]
  - has_debt(u8) @ [2287]

HF = unhealthy_borrow_value_sf / borrowed_assets_market_value_sf

Only 3344-byte accounts are main-market obligations.

Liquidation: ix tag 14 = LiquidateObligationAndRedeemReserveCollateral
  close factor 20% (HF < 1.0), bonus from reserve config.
"""
from __future__ import annotations

import base64
import os
import struct
from typing import Any

try:
    import base58 as _b58
except ImportError:
    _b58 = None

ID = "kamino"
LABEL = "Kamino"

KAMINO_PROGRAM = "KLend2g3cP87fffoy8q1mQqGKjrxjC8boSyAYavgmjD"
KAMINO_MAIN_MARKET = "7u3HeHxYDLhnCoErrtycNokbQYbWGzLs6JSDqGAv5PfF"
KAMINO_IX_LIQUIDATE = 14
KAMINO_IX_REFRESH_RESERVE = 3
KAMINO_IX_REFRESH_OBLIGATION = 7

OBLIGATION_DATA_SIZES = (3344,)
OBLIGATION_MARKET_OFFSET = 32

# Proven kLend Obligation offsets (see module docstring).
_OBL_DEPOSITS_BASE = 96
_OBL_DEPOSIT_STRIDE = 136
_OBL_N_DEPOSITS = 8
_OBL_DEPOSITED_VALUE = 1192
_OBL_BORROWS_BASE = 1208
_OBL_BORROW_STRIDE = 200
_OBL_N_BORROWS = 5
_OBL_DEBT_MARKET_VALUE = 2224
_OBL_ALLOWED_BORROW = 2240
_OBL_UNHEALTHY_BORROW = 2256

_MAX_USD_WAD = 5e11 * 1e18


def _env_flag(key: str, default: bool = True) -> bool:
    v = os.environ.get(key, "").strip().lower()
    if v in ("0", "false", "no", "off"):
        return False
    if v in ("1", "true", "yes", "on"):
        return True
    return default


def enabled() -> bool:
    return _env_flag("TONI_KAMINO", True)


def _u128(data: bytes) -> int:
    return int.from_bytes(data[:16], "little")


def parse_obligation(pk: str, raw: bytes) -> dict | None:
    """Decode a Kamino obligation account into HF + position summary.

    Offsets per official klend IDL, validated on-chain 2026-08-21:
      deposits[8] @ 96 (stride 136), deposited_value_sf @ 1192,
      borrows[5] @ 1208 (stride 200), debt market value @ 2224,
      allowed_borrow @ 2240, unhealthy_borrow @ 2256.
    """
    if len(raw) < 2272:
        return None

    debt_mv = _u128(raw[_OBL_DEBT_MARKET_VALUE:
                        _OBL_DEBT_MARKET_VALUE + 16]) / 1e18
    if debt_mv <= 0:
        return None
    unhealthy = _u128(raw[_OBL_UNHEALTHY_BORROW:
                          _OBL_UNHEALTHY_BORROW + 16]) / 1e18
    deposited = _u128(raw[_OBL_DEPOSITED_VALUE:_OBL_DEPOSITED_VALUE + 16]) / 1e18
    allowed = _u128(raw[_OBL_ALLOWED_BORROW:_OBL_ALLOWED_BORROW + 16]) / 1e18

    # WAD USD sanity — garbage layouts explode past any plausible value.
    if debt_mv > 5e11 or deposited > 5e11 or unhealthy > 5e11:
        return None

    hf = unhealthy / debt_mv if unhealthy > 0 else (
        deposited / debt_mv if deposited > 0 else 0.0)
    if hf <= 0 or hf > 500:
        return None

    owner_bytes = raw[64:96]
    if _b58 and len(owner_bytes) == 32:
        owner = _b58.b58encode(owner_bytes).decode()
    else:
        owner = ""

    deposit_reserves: list[str] = []
    borrow_reserves: list[str] = []
    for i in range(_OBL_N_DEPOSITS):
        off = _OBL_DEPOSITS_BASE + i * _OBL_DEPOSIT_STRIDE
        res_bytes = raw[off:off + 32]
        if any(res_bytes):
            deposit_reserves.append(
                _b58.b58encode(res_bytes).decode() if _b58 else "")
    for j in range(_OBL_N_BORROWS):
        off = _OBL_BORROWS_BASE + j * _OBL_BORROW_STRIDE
        res_bytes = raw[off:off + 32]
        if any(res_bytes):
            borrow_reserves.append(
                _b58.b58encode(res_bytes).decode() if _b58 else "")

    return {
        "obligation": pk,
        "user": owner or pk,
        "owner": owner,
        "hf": round(hf, 6),
        "coll_usd": round(deposited, 2),
        "debt_usd": round(debt_mv, 2),
        "deposited_usd": round(deposited, 2),
        "borrowed_usd": round(debt_mv, 2),
        "unhealthy_usd": round(unhealthy, 2),
        "allowed_borrow_usd": round(allowed, 2),
        "deposit_reserves": deposit_reserves,
        "borrow_reserves": borrow_reserves,
        "protocol_id": ID,
        "protocol": LABEL,
    }


def score_profit(opp: dict, *, sol_px: float = 0.0,
                 priority_median: int | None = None,
                 pressure: str | None = None) -> dict:
    """20% close factor, ~5% liq bonus — Kamino kLend defaults."""
    hf = opp.get("hf")
    debt = float(opp.get("debt_usd") or opp.get("borrowed_usd") or 0)
    close = 0.20
    bonus = 0.05
    repay = debt * close if debt > 0 else 0.0
    seized = repay * (1.0 + bonus)
    gross = seized - repay
    cu_usd = 0.03 if (sol_px or 0) <= 0 else max(0.01, 250_000 * 50e-6 * (sol_px / 150.0))
    if str(pressure or "") in ("hot", "elevated"):
        cu_usd *= 1.4
    net = gross - cu_usd
    out = dict(opp)
    out["close_factor"] = close
    out["liq_bonus_pct"] = bonus * 100.0
    out["repay_usd"] = round(repay, 4)
    out["seized_usd"] = round(seized, 4)
    out["profit_usd"] = round(net, 4)
    out["net_usd"] = round(net, 4)
    out["actionable"] = bool(hf is not None and hf < 1.0 and net > 0.5)
    return out


def build_plan(opp: dict, priority_median: int | None = None,
               pressure: str | None = None) -> dict:
    """Sweep/hot-path plan for `_live_send_kamino` in sol_scanner."""
    scored = score_profit(opp, pressure=pressure)
    obl = str(opp.get("obligation") or opp.get("user") or "")
    borrows = [r for r in (opp.get("borrow_reserves") or []) if r]
    deposits = [r for r in (opp.get("deposit_reserves") or []) if r]
    debt_r = str(opp.get("debt_reserve") or (borrows[0] if borrows else ""))
    coll_r = str(opp.get("coll_reserve") or (deposits[0] if deposits else ""))
    hf = opp.get("hf")
    ready = bool(obl) and (hf is None or float(hf) < 1.0)
    return {
        "kind": "liq",
        "protocol_id": ID,
        "protocol": LABEL,
        "execute": "kamino-jito",
        "program": KAMINO_PROGRAM,
        "obligation": obl,
        "debt_reserve": debt_r,
        "coll_reserve": coll_r,
        "borrow_reserves": borrows,
        "deposit_reserves": deposits,
        "hf": hf,
        "ready": ready,
        "close_factor": scored["close_factor"],
        "profit_usd": scored.get("profit_usd"),
        "net_usd": scored.get("net_usd"),
        "repay_usd": scored.get("repay_usd"),
        "seized_usd": scored.get("seized_usd"),
        "note": ("" if ready else "kamino plan waiting on HF<1 obligation"),
    }


def hydrate_pubkeys(pubkeys: list[str], *, max_accounts: int = 40) -> dict:
    """getMultipleAccounts hydrate — no GPA. Used by event-driven discovery."""
    import sol_scanner as sols

    keys = [p for p in pubkeys if p][:max_accounts]
    opps: list[dict] = []
    watch: list[dict] = []
    errors: list[str] = []
    hydrated = 0
    if not keys:
        return {"ok": True, "opportunities": [], "watch": [], "probed": 0,
                "hydrated": 0, "errors": [], "method": "gma"}
    try:
        res, _url = sols.sol_rpc(
            "getMultipleAccounts",
            [keys, {"encoding": "base64", "commitment": "confirmed"}],
            timeout=12.0)
        vals = (res or {}).get("value") if isinstance(res, dict) else (res or [])
        for pk, acc in zip(keys, vals or []):
            if not isinstance(acc, dict):
                continue
            if (acc.get("owner") or "") != KAMINO_PROGRAM:
                continue
            data = acc.get("data")
            b64 = data[0] if isinstance(data, list) and data else ""
            if not b64:
                continue
            raw = base64.b64decode(b64)
            parsed = parse_obligation(pk, raw)
            if not parsed:
                continue
            hydrated += 1
            if parsed["hf"] < 1.0 and parsed["borrowed_usd"] > 0.50:
                opps.append(parsed)
            elif parsed["hf"] < 1.15:
                watch.append(parsed)
    except Exception as e:
        errors.append(f"kamino gma: {str(e)[:140]}")
    watch.sort(key=lambda w: w.get("hf") or 99)
    return {
        "ok": hydrated > 0 or not errors,
        "opportunities": opps,
        "watch": watch[:50],
        "probed": len(keys),
        "hydrated": hydrated,
        "errors": errors,
        "method": "gma",
    }


def scan_obligations(*, max_accounts: int = 40) -> dict:
    """GPA probe Kamino obligations for HF < 1 opportunities (fallback)."""
    import sol_scanner as sols

    probed = 0
    hydrated = 0
    opps: list[dict] = []
    watch: list[dict] = []
    errors: list[str] = []

    for data_size in OBLIGATION_DATA_SIZES:
        try:
            filters = [{"dataSize": data_size}]
            if KAMINO_MAIN_MARKET:
                filters.append({
                    "memcmp": {
                        "offset": OBLIGATION_MARKET_OFFSET,
                        "bytes": KAMINO_MAIN_MARKET,
                    }
                })
            result = sols.sol_gpa(KAMINO_PROGRAM, filters=filters,
                                   encoding="base64", timeout=12.0)
            if not result:
                continue
            probed += len(result)
            for acc in result[:max_accounts]:
                pk = acc.get("pubkey") or ""
                data_b64 = ((acc.get("account") or {}).get("data") or [""])[0]
                if not data_b64:
                    continue
                raw = base64.b64decode(data_b64)
                parsed = parse_obligation(pk, raw)
                if not parsed:
                    continue
                hydrated += 1
                if parsed["hf"] < 1.0 and parsed["borrowed_usd"] > 0.50:
                    opps.append(parsed)
                elif parsed["hf"] < 1.15:
                    watch.append(parsed)
        except Exception as e:
            errors.append(f"kamino gpa {data_size}: {str(e)[:120]}")

    watch.sort(key=lambda w: w.get("hf") or 99)
    return {
        "ok": hydrated > 0 or probed > 0,
        "opportunities": opps,
        "watch": watch[:50],
        "probed": probed,
        "hydrated": hydrated,
        "errors": errors,
        "method": "gpa",
    }


def scan_competitor_sigs(*, limit: int = 24, sol_px: float = 0.0) -> dict:
    """Decode recent Kamino liquidation txs from on-chain signatures."""
    import sol_scanner as sols

    events: list[dict] = []
    errors: list[str] = []
    try:
        sigs_result, _ = sols.sol_rpc("getSignaturesForAddress", [
            KAMINO_PROGRAM,
            {"limit": min(limit * 3, 80), "commitment": "confirmed"},
        ], timeout=8.0)
        if not sigs_result:
            return {"events": [], "errors": []}

        for sig_row in (sigs_result or [])[:limit]:
            sig = sig_row.get("signature") or ""
            if not sig:
                continue
            try:
                tx_result, _ = sols.sol_rpc("getTransaction", [
                    sig,
                    {"encoding": "json", "maxSupportedTransactionVersion": 0,
                     "commitment": "confirmed"},
                ], timeout=6.0)
                if not tx_result:
                    continue
                tx = tx_result.get("transaction") or {}
                msg = tx.get("message") or {}
                ixs = msg.get("instructions") or []
                accounts = msg.get("accountKeys") or []
                for ix in ixs:
                    prog_idx = ix.get("programIdIndex")
                    if prog_idx is None or prog_idx >= len(accounts):
                        continue
                    if accounts[prog_idx] != KAMINO_PROGRAM:
                        continue
                    ix_data = base64.b64decode(ix.get("data") or "")
                    if not ix_data:
                        continue
                    tag = ix_data[0]
                    if tag == KAMINO_IX_LIQUIDATE:
                        ix_accounts = ix.get("accounts") or []
                        events.append({
                            "sig": sig,
                            "slot": sig_row.get("slot"),
                            "protocol_id": ID,
                            "protocol": LABEL,
                            "type": "liquidation",
                            "tag": tag,
                            "n_accounts": len(ix_accounts),
                        })
                        break
            except Exception:
                continue
    except Exception as e:
        errors.append(str(e)[:160])

    return {"events": events, "errors": errors}


def _pubkey_to_b64(pubkey: str) -> str:
    """Convert base58 pubkey to base64 (for memcmp filters)."""
    import base58
    try:
        return base64.b64encode(base58.b58decode(pubkey)).decode()
    except Exception:
        return pubkey
