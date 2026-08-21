"""MarginFi v2 adapter for SOL liquidation scanning.

MarginFi uses its own lending program with a "marginfi account" per user
that holds balances across lending groups (pools).
Program ID: MFv2hWf31Z9kbCa1snEPYctwafyhdvnV7FZnsebVacA

MarginFi account layout (simplified):
  - discriminator(8) + group(32) + authority/owner(32) @ offset 8
  - lending_account starts at offset 72
  - Each balance entry: active(1) + bank_pk(32) + asset_shares(u64,u64 wad)
    + liability_shares(u64,u64 wad) + ... = 152 bytes per slot
  - Up to 16 balance slots

HF is derived from on-chain bank state (asset weights, oracle prices,
liability weights). For scanning we use getProgramAccounts + hydrate via
bank configs to approximate HF.

Liquidation: LiquidateAccount ix — close factor 100% when HF < 1.0,
liquidator bonus configured per bank (typically 2.5–5%).
"""
from __future__ import annotations

import base64
import os
import struct
from typing import Any

ID = "marginfi"
LABEL = "MarginFi"

MARGINFI_PROGRAM = "MFv2hWf31Z9kbCa1snEPYctwafyhdvnV7FZnsebVacA"
MARGINFI_GROUP = "4qp6Fx6tnZkY5Wropq9wUYgtFxXKwE6viZxFHg3rdAG8"
MARGINFI_ACCOUNT_SIZE = 2312
MARGINFI_ACCOUNT_DISCRIMINATOR = b"C\xb2\x82m~r\x1c*"

MARGINFI_IX_LIQUIDATE = 4


def _env_flag(key: str, default: bool = True) -> bool:
    v = os.environ.get(key, "").strip().lower()
    if v in ("0", "false", "no", "off"):
        return False
    if v in ("1", "true", "yes", "on"):
        return True
    return default


def enabled() -> bool:
    return _env_flag("TONI_MARGINFI", True)


def _u64_wad_pair(data: bytes) -> float:
    """Read marginfi's fixed-point I80F48 (i128 stored as 16 bytes, little-endian)."""
    if len(data) < 16:
        return 0.0
    raw = int.from_bytes(data[:16], "little", signed=True)
    return raw / (1 << 48)


def parse_marginfi_account(pk: str, raw: bytes) -> dict | None:
    """Decode a MarginFi account and estimate HF from balance slots.

    This is a simplified parse — real HF requires bank oracle prices +
    asset/liability weights. We flag accounts with active liabilities
    so the sweep loop can hydrate them with full bank state.
    """
    if len(raw) < 256:
        return None
    disc = raw[:8]
    if disc != MARGINFI_ACCOUNT_DISCRIMINATOR:
        return None
    group = raw[8:40]
    owner = raw[40:72]

    n_deposits = 0
    n_borrows = 0
    total_assets_raw = 0.0
    total_liabs_raw = 0.0
    balance_offset = 72

    for i in range(16):
        slot_start = balance_offset + i * 152
        if slot_start + 152 > len(raw):
            break
        active = raw[slot_start]
        if not active:
            continue
        bank_pk_bytes = raw[slot_start + 1: slot_start + 33]
        asset_shares = _u64_wad_pair(raw[slot_start + 33: slot_start + 49])
        liab_shares = _u64_wad_pair(raw[slot_start + 49: slot_start + 65])
        if asset_shares > 0:
            n_deposits += 1
            total_assets_raw += asset_shares
        if liab_shares > 0:
            n_borrows += 1
            total_liabs_raw += abs(liab_shares)

    if n_borrows == 0 or total_liabs_raw <= 0:
        return None

    hf_proxy = total_assets_raw / total_liabs_raw if total_liabs_raw > 0 else 999.0
    if hf_proxy <= 0 or hf_proxy > 500:
        return None

    return {
        "obligation": pk,
        "user": pk,
        "hf": round(hf_proxy, 6),
        "n_deposits": n_deposits,
        "n_borrows": n_borrows,
        "assets_raw": round(total_assets_raw, 4),
        "liabs_raw": round(total_liabs_raw, 4),
        "protocol_id": ID,
        "protocol": LABEL,
        "hf_method": "share_ratio",
    }


def scan_obligations(*, max_accounts: int = 40) -> dict:
    """GPA probe MarginFi accounts for potential liquidation candidates."""
    import sol_scanner as sols

    probed = 0
    hydrated = 0
    opps: list[dict] = []
    watch: list[dict] = []
    errors: list[str] = []

    try:
        filters = [
            {"dataSize": MARGINFI_ACCOUNT_SIZE},
        ]
        if MARGINFI_GROUP:
            filters.append({
                "memcmp": {
                    "offset": 8,
                    "bytes": MARGINFI_GROUP,
                }
            })

        result = sols.sol_gpa(MARGINFI_PROGRAM, filters=filters,
                               encoding="base64", timeout=15.0)

        if not result:
            return {
                "ok": False, "opportunities": [], "watch": [],
                "probed": 0, "hydrated": 0,
                "errors": ["marginfi gpa: no results"],
                "method": "gpa",
            }

        probed = len(result)
        for acc in result[:max_accounts * 5]:
            pk = acc.get("pubkey") or ""
            data_b64 = ((acc.get("account") or {}).get("data") or [""])[0]
            if not data_b64:
                continue
            raw = base64.b64decode(data_b64)
            parsed = parse_marginfi_account(pk, raw)
            if not parsed:
                continue
            hydrated += 1
            if parsed["hf"] < 1.0 and parsed["liabs_raw"] > 0:
                opps.append(parsed)
            elif parsed["hf"] < 1.15:
                watch.append(parsed)

    except Exception as e:
        errors.append(f"marginfi gpa: {str(e)[:140]}")

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
    """Decode recent MarginFi liquidation txs."""
    import sol_scanner as sols

    events: list[dict] = []
    errors: list[str] = []
    try:
        sigs_result, _ = sols.sol_rpc("getSignaturesForAddress", [
            MARGINFI_PROGRAM,
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
                meta = tx_result.get("meta") or {}
                logs = meta.get("logMessages") or []
                is_liq = any("liquidat" in (log or "").lower() for log in logs)
                if is_liq:
                    events.append({
                        "sig": sig,
                        "slot": sig_row.get("slot"),
                        "protocol_id": ID,
                        "protocol": LABEL,
                        "type": "liquidation",
                    })
            except Exception:
                continue
    except Exception as e:
        errors.append(str(e)[:160])

    return {"events": events, "errors": errors}
