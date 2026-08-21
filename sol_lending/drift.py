"""Drift Protocol adapter for SOL liquidation scanning.

Drift is a cross-margin perps + lending protocol on Solana.
Program ID: dRiftyHA39MWEi3m9aunc5MzRF1JYuBsbn6VPcn33UH

User accounts are "User" accounts (discriminator-based) tied to a
sub-account index. Each user holds spot positions (deposits/borrows)
and perp positions. Liquidation occurs when the user's margin ratio
drops below maintenance margin.

User account layout (simplified for spot-only scanning):
  - discriminator(8) + authority(32) + sub_account_id(2) +
    name(32) + ... spot_positions start at offset ~168
  - Each SpotPosition: scaled_balance(u128) + ... = ~56 bytes, 8 slots
  - Each PerpPosition: base/quote amounts + ... = ~88 bytes, 8 slots

Liquidation: LiquidateSpot / LiquidatePerp ix tags.
The liquidation bonus is the "if_liquidation_fee" per market (typically 1-4%).
"""
from __future__ import annotations

import base64
import os
import struct
from typing import Any

ID = "drift"
LABEL = "Drift"

DRIFT_PROGRAM = "dRiftyHA39MWEi3m9aunc5MzRF1JYuBsbn6VPcn33UH"
DRIFT_USER_DISCRIMINATOR = b"\xd0\xc0\xda\xa4\x17\x15\x14\x0b"
DRIFT_USER_ACCOUNT_SIZE = 4376

DRIFT_IX_LIQ_SPOT = 13
DRIFT_IX_LIQ_PERP = 14


def _env_flag(key: str, default: bool = True) -> bool:
    v = os.environ.get(key, "").strip().lower()
    if v in ("0", "false", "no", "off"):
        return False
    if v in ("1", "true", "yes", "on"):
        return True
    return default


def enabled() -> bool:
    return _env_flag("TONI_DRIFT", True)


def _i128_le(data: bytes) -> int:
    if len(data) < 16:
        return 0
    return int.from_bytes(data[:16], "little", signed=True)


def parse_user_account(pk: str, raw: bytes) -> dict | None:
    """Decode a Drift user account to extract spot balances for HF proxy."""
    if len(raw) < 256:
        return None
    disc = raw[:8]
    if disc != DRIFT_USER_DISCRIMINATOR:
        return None

    authority = raw[8:40]

    spot_offset = 168
    total_assets = 0.0
    total_liabs = 0.0
    n_spot = 0

    for i in range(8):
        pos_start = spot_offset + i * 56
        if pos_start + 56 > len(raw):
            break
        scaled_balance = _i128_le(raw[pos_start:pos_start + 16])
        market_index = int.from_bytes(raw[pos_start + 16:pos_start + 18], "little")
        balance_type = raw[pos_start + 18] if pos_start + 18 < len(raw) else 0
        if scaled_balance == 0:
            continue
        n_spot += 1
        amount = abs(scaled_balance) / 1e9
        if balance_type == 0:  # deposit
            total_assets += amount
        elif balance_type == 1:  # borrow
            total_liabs += amount

    if total_liabs <= 0:
        return None

    hf_proxy = total_assets / total_liabs if total_liabs > 0 else 999.0
    if hf_proxy <= 0 or hf_proxy > 500:
        return None

    return {
        "obligation": pk,
        "user": pk,
        "hf": round(hf_proxy, 6),
        "n_spots": n_spot,
        "assets_raw": round(total_assets, 4),
        "liabs_raw": round(total_liabs, 4),
        "protocol_id": ID,
        "protocol": LABEL,
        "hf_method": "share_ratio",
    }


def scan_obligations(*, max_accounts: int = 40) -> dict:
    """GPA probe Drift user accounts for potential liquidation candidates."""
    import sol_scanner as sols

    probed = 0
    hydrated = 0
    opps: list[dict] = []
    watch: list[dict] = []
    errors: list[str] = []

    try:
        disc_b58 = ""
        try:
            import base58 as _b58
            disc_b58 = _b58.b58encode(DRIFT_USER_DISCRIMINATOR).decode()
        except Exception:
            disc_b58 = base64.b64encode(DRIFT_USER_DISCRIMINATOR).decode()

        filters = [
            {"dataSize": DRIFT_USER_ACCOUNT_SIZE},
        ]
        if disc_b58:
            filters.append({
                "memcmp": {
                    "offset": 0,
                    "bytes": disc_b58,
                }
            })

        result = sols.sol_gpa(DRIFT_PROGRAM, filters=filters,
                               encoding="base64", timeout=15.0)

        if not result:
            # Try without discriminator filter (size-only) — parse function
            # validates discriminator anyway. Handles paused/empty protocols.
            try:
                result = sols.sol_gpa(DRIFT_PROGRAM,
                                       filters=[{"dataSize": DRIFT_USER_ACCOUNT_SIZE}],
                                       encoding="base64", timeout=10.0)
            except Exception:
                pass

        probed = len(result) if result else 0
        for acc in (result or [])[:max_accounts * 5]:
            pk = acc.get("pubkey") or ""
            data_b64 = ((acc.get("account") or {}).get("data") or [""])[0]
            if not data_b64:
                continue
            raw = base64.b64decode(data_b64)
            parsed = parse_user_account(pk, raw)
            if not parsed:
                continue
            hydrated += 1
            if parsed["hf"] < 1.0 and parsed["liabs_raw"] > 0:
                opps.append(parsed)
            elif parsed["hf"] < 1.15:
                watch.append(parsed)

    except Exception as e:
        errors.append(f"drift gpa: {str(e)[:140]}")

    watch.sort(key=lambda w: w.get("hf") or 99)
    return {
        "ok": True,  # Protocol is accessible even if no User accounts
        "opportunities": opps,
        "watch": watch[:50],
        "probed": probed,
        "hydrated": hydrated,
        "errors": errors,
        "method": "gpa",
    }


def scan_competitor_sigs(*, limit: int = 24, sol_px: float = 0.0) -> dict:
    """Decode recent Drift liquidation txs."""
    import sol_scanner as sols

    events: list[dict] = []
    errors: list[str] = []
    try:
        sigs_result, _ = sols.sol_rpc("getSignaturesForAddress", [
            DRIFT_PROGRAM,
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
