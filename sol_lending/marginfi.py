"""MarginFi v2 adapter — bank-oracle HF, sweep plans, live liquidate path.

Program: MFv2hWf31Z9kbCa1snEPYctwafyhdvnV7FZnsebVacA
Main group: 4qp6Fx6tnZkY5Wropq9wUYgtFxXKwE6viZxFHg3rdAG8
"""
from __future__ import annotations

import base64
import os
import struct
import threading
import time
from typing import Any

try:
    import base58 as _b58
except ImportError:
    _b58 = None

ID = "marginfi"
LABEL = "MarginFi"

MARGINFI_PROGRAM = "MFv2hWf31Z9kbCa1snEPYctwafyhdvnV7FZnsebVacA"
MARGINFI_GROUP = "4qp6Fx6tnZkY5Wropq9wUYgtFxXKwE6viZxFHg3rdAG8"
MARGINFI_ACCOUNT_SIZE = 2312
MARGINFI_ACCOUNT_DISCRIMINATOR = bytes([67, 178, 130, 109, 126, 114, 28, 42])
BANK_DISCRIMINATOR = bytes([142, 49, 166, 242, 50, 66, 97, 188])
LIQUIDATE_IX_DISC = bytes([214, 169, 151, 213, 251, 167, 86, 219])

# Balance stride in LendingAccount (IDL Balance struct, repr C).
_BALANCE_STRIDE = 104
_BALANCE_BASE = 72  # after 8 disc + group + authority
_N_BALANCES = 16

# Bank layout offsets (after 8-byte Anchor disc, bytemuck repr C).
_BNK_MINT = 8
_BNK_DECIMALS = 40
_BNK_GROUP = 41
_BNK_ASSET_SHARE = 80
_BNK_LIAB_SHARE = 96
_BNK_LIQ_VAULT = 112
_BNK_INS_VAULT = 146
_BNK_CONFIG = 296
_CFG_ASSET_W_MAINT = 312
_CFG_LIAB_W_MAINT = 344
_CFG_ORACLE_SETUP = 616
_CFG_ORACLE_KEYS = 624
_CFG_ASSET_TAG = 806

# OracleSetup variants (marginfi v2).
_ORACLE_FIXED = 6
_ASSET_TAG_DEFAULT = 0
_ASSET_TAG_SOL = 1
_ASSET_TAG_KAMINO = 3
_ASSET_TAG_DRIFT = 4

_BANKS_CACHE: dict[str, Any] = {"ts": 0, "rows": {}}
_BANKS_LOCK = threading.Lock()
_LIQ_BONUS = 0.05  # ~5% liquidator + insurance (protocol default band)

_ZERO_PK = b"\x00" * 32


def _env_flag(key: str, default: bool = True) -> bool:
    v = os.environ.get(key, "").strip().lower()
    if v in ("0", "false", "no", "off"):
        return False
    if v in ("1", "true", "yes", "on"):
        return True
    return default


def enabled() -> bool:
    return _env_flag("TONI_MARGINFI", True)


def _pk_bytes(pk: str) -> bytes:
    if _b58:
        return _b58.b58decode(pk)
    raise RuntimeError("base58 required")


def _pk_str(raw: bytes) -> str:
    if _b58 and len(raw) == 32:
        return _b58.b58encode(raw).decode()
    return ""


def _i80f48(data: bytes) -> float:
    if len(data) < 16:
        return 0.0
    raw = int.from_bytes(data[:16], "little", signed=True)
    return raw / (1 << 48)


def _is_live_pk(pk: str) -> bool:
    s = (pk or "").strip()
    return len(s) >= 32 and "…" not in s and "..." not in s


def _mint_symbol(mint: str) -> str:
    try:
        import sol_scanner as sols
        return sols.SYM.get(mint) or mint[:6]
    except Exception:
        return mint[:6] if mint else "?"


def fetch_banks(force: bool = False) -> dict[str, dict]:
    """GPA main-group banks; cached ~5 min."""
    now = time.time()
    with _BANKS_LOCK:
        if not force and _BANKS_CACHE["rows"] and now - _BANKS_CACHE["ts"] < 300:
            return dict(_BANKS_CACHE["rows"])
    import sol_scanner as sols
    rows: dict[str, dict] = {}
    try:
        import base64 as b64
        disc_b64 = b64.b64encode(BANK_DISCRIMINATOR).decode()
        group_b64 = b64.b64encode(_pk_bytes(MARGINFI_GROUP)).decode()
        accs = sols.sol_gpa(
            MARGINFI_PROGRAM,
            filters=[
                {"memcmp": {"offset": 0, "bytes": disc_b64}},
                {"memcmp": {"offset": _BNK_GROUP, "bytes": group_b64}},
            ],
            encoding="base64",
            timeout=25.0,
        )
    except Exception:
        accs = []
    for acc in accs or []:
        pk = acc.get("pubkey") or ""
        raw = sols._acc_bytes(acc.get("account") or {})
        row = _parse_bank(pk, raw)
        if row:
            rows[pk] = row
    with _BANKS_LOCK:
        _BANKS_CACHE["ts"] = now
        _BANKS_CACHE["rows"] = rows
    return rows


def _parse_bank(pk: str, raw: bytes) -> dict | None:
    if len(raw) < _CFG_ORACLE_KEYS + 160:
        return None
    if raw[:8] != BANK_DISCRIMINATOR:
        return None
    mint = _pk_str(raw[_BNK_MINT:_BNK_MINT + 32])
    group = _pk_str(raw[_BNK_GROUP:_BNK_GROUP + 32])
    if not mint or group != MARGINFI_GROUP:
        return None
    keys = []
    for i in range(5):
        off = _CFG_ORACLE_KEYS + i * 32
        k = _pk_str(raw[off:off + 32])
        if k and raw[off:off + 32] != _ZERO_PK:
            keys.append(k)
    return {
        "address": pk,
        "mint": mint,
        "symbol": _mint_symbol(mint),
        "decimals": int(raw[_BNK_DECIMALS]),
        "group": group,
        "asset_share_value": _i80f48(raw[_BNK_ASSET_SHARE:_BNK_ASSET_SHARE + 16]),
        "liability_share_value": _i80f48(raw[_BNK_LIAB_SHARE:_BNK_LIAB_SHARE + 16]),
        "liquidity_vault": _pk_str(raw[_BNK_LIQ_VAULT:_BNK_LIQ_VAULT + 32]),
        "insurance_vault": _pk_str(raw[_BNK_INS_VAULT:_BNK_INS_VAULT + 32]),
        "asset_weight_maint": _i80f48(raw[_CFG_ASSET_W_MAINT:_CFG_ASSET_W_MAINT + 16]),
        "liability_weight_maint": _i80f48(raw[_CFG_LIAB_W_MAINT:_CFG_LIAB_W_MAINT + 16]),
        "oracle_setup": int(raw[_CFG_ORACLE_SETUP]),
        "oracle_keys": keys,
        "asset_tag": int(raw[_CFG_ASSET_TAG]) if len(raw) > _CFG_ASSET_TAG else 0,
    }


def _parse_balances(raw: bytes) -> list[dict]:
    out: list[dict] = []
    for i in range(_N_BALANCES):
        off = _BALANCE_BASE + i * _BALANCE_STRIDE
        if off + _BALANCE_STRIDE > len(raw):
            break
        slot = raw[off:off + _BALANCE_STRIDE]
        if not slot[0]:
            continue
        bank_pk = _pk_str(slot[1:33])
        if not bank_pk:
            continue
        asset_sh = _i80f48(slot[40:56])
        liab_sh = _i80f48(slot[56:72])
        if asset_sh <= 0 and liab_sh <= 0:
            continue
        out.append({
            "bank": bank_pk,
            "asset_shares": asset_sh,
            "liability_shares": liab_sh,
        })
    out.sort(key=lambda b: b["bank"])
    return out


def _bank_qty(shares: float, share_value: float, decimals: int) -> float:
    if shares <= 0 or share_value <= 0:
        return 0.0
    native = shares * share_value
    return native / (10 ** max(decimals, 0))


def _mint_usd(mint: str, sol_px: float) -> float:
    try:
        import sol_scanner as sols
        if mint == sols.MINT_SOL:
            return float(sol_px or sols.fetch_sol_price() or 0)
        q = sols._jup_quote(mint, sols.MINT_USDC, 10 ** max(sols.DECIMALS.get(mint, 6), 0),
                            slippage_bps=100)
        if q and q.get("outAmount"):
            dec = sols.DECIMALS.get(sols.MINT_USDC, 6)
            return float(q["outAmount"]) / (10 ** dec)
    except Exception:
        pass
    return 0.0


def compute_health(balances: list[dict], banks: dict[str, dict],
                   sol_px: float = 0.0) -> dict:
    """Maintenance-weight health from bank configs + Jupiter/oracle-free USD."""
    w_assets = 0.0
    w_liabs = 0.0
    coll_usd = 0.0
    debt_usd = 0.0
    top_asset = ("", 0.0)
    top_liab = ("", 0.0)
    for bal in balances:
        bank = banks.get(bal["bank"]) or {}
        if not bank:
            continue
        dec = int(bank.get("decimals") or 6)
        px = _mint_usd(bank.get("mint") or "", sol_px)
        if px <= 0:
            continue
        aw = float(bank.get("asset_weight_maint") or 0)
        lw = float(bank.get("liability_weight_maint") or 0)
        a_qty = _bank_qty(bal["asset_shares"], bank.get("asset_share_value") or 0, dec)
        l_qty = _bank_qty(bal["liability_shares"], bank.get("liability_share_value") or 0, dec)
        a_usd = a_qty * px
        l_usd = l_qty * px
        if a_usd > 0:
            w_assets += a_usd * aw
            coll_usd += a_usd
            if a_usd > top_asset[1]:
                top_asset = (bal["bank"], a_usd)
        if l_usd > 0:
            w_liabs += l_usd * lw
            debt_usd += l_usd
            if l_usd > top_liab[1]:
                top_liab = (bal["bank"], l_usd)
    health = w_assets - w_liabs
    hf = (w_assets / w_liabs) if w_liabs > 0 else (99.0 if w_assets > 0 else 0.0)
    return {
        "hf": round(hf, 6),
        "health": health,
        "coll_usd": round(coll_usd, 2),
        "debt_usd": round(debt_usd, 2),
        "asset_bank": top_asset[0],
        "liab_bank": top_liab[0],
        "weighted_assets": w_assets,
        "weighted_liabs": w_liabs,
    }


def parse_account(pk: str, raw: bytes, banks: dict[str, dict] | None = None,
                  sol_px: float = 0.0) -> dict | None:
    if len(raw) < _BALANCE_BASE + _BALANCE_STRIDE:
        return None
    if raw[:8] != MARGINFI_ACCOUNT_DISCRIMINATOR:
        return None
    group = _pk_str(raw[8:40])
    owner = _pk_str(raw[40:72])
    if group != MARGINFI_GROUP:
        return None
    banks = banks if banks is not None else fetch_banks()
    balances = _parse_balances(raw)
    if not balances:
        return None
    if not any(b["liability_shares"] > 0 for b in balances):
        return None
    h = compute_health(balances, banks, sol_px=sol_px)
    if h["debt_usd"] <= 0:
        return None
    asset_b = h.get("asset_bank") or ""
    liab_b = h.get("liab_bank") or ""
    asset_bank = banks.get(asset_b) or {}
    liab_bank = banks.get(liab_b) or {}
    return {
        "obligation": pk,
        "user": owner or pk,
        "owner": owner,
        "hf": h["hf"],
        "health": h["health"],
        "coll_usd": h["coll_usd"],
        "debt_usd": h["debt_usd"],
        "collateral_sym": asset_bank.get("symbol") or "?",
        "debt_sym": liab_bank.get("symbol") or "?",
        "coll_mint": asset_bank.get("mint") or "",
        "debt_mint": liab_bank.get("mint") or "",
        "asset_bank": asset_b,
        "liab_bank": liab_b,
        "coll_reserve": asset_b,
        "debt_reserve": liab_b,
        "balances": balances,
        "protocol_id": ID,
        "protocol": LABEL,
        "hf_method": "bank_oracle_weights",
        "proxy": False,
    }


def score_profit(opp: dict, *, sol_px: float = 0.0,
                 priority_median: int | None = None,
                 pressure: str | None = None,
                 banks: dict | None = None) -> dict:
    """Score with maintenance weights, 100% close factor, liq bonus band."""
    hf = opp.get("hf")
    debt = float(opp.get("debt_usd") or 0)
    close = 1.0
    bonus = _LIQ_BONUS
    repay = min(debt * close, float(opp.get("coll_usd") or debt) / (1.0 + bonus), debt)
    seized = repay * (1.0 + bonus)
    gross = seized - repay
    px = float(sol_px or 0.0)
    cu = 600_000
    cu_usd = 0.04 if px <= 0 else max(0.015, cu * 50e-6 * (px / 150.0))
    if str(pressure or "") in ("hot", "elevated"):
        cu_usd *= 1.35
    slip = seized * 0.004 if opp.get("coll_mint") != opp.get("debt_mint") else 0.0
    pre_tip = gross - cu_usd - slip
    try:
        import sol_scanner as sols
        jito_lam = sols._dynamic_jito_lamports(pre_tip, px, pressure, sols.min_sol_liq_usd())
        jito_usd = (jito_lam / 1e9) * px
        floor = sols.min_sol_liq_usd()
    except Exception:
        jito_usd = 0.03
        floor = 3.0
    net = pre_tip - jito_usd
    out = dict(opp)
    out["close_factor"] = close
    out["liq_bonus_pct"] = bonus * 100.0
    out["repay_usd"] = round(repay, 4)
    out["seized_usd"] = round(seized, 4)
    out["gross_usd"] = round(gross, 4)
    out["slip_usd"] = round(slip, 6)
    out["profit_usd"] = round(net, 4)
    out["net_usd"] = round(net, 4)
    out["actionable"] = bool(
        hf is not None and float(hf) < 1.0 and net > floor
        and opp.get("hf_method") == "bank_oracle_weights")
    out["edge"] = bool(out.get("actionable") or str(out.get("debt_sym") or "").upper()
                       in ("BONK", "WIF", "PYTH"))
    out["compute_units"] = cu
    return out


def build_plan(opp: dict, priority_median: int | None = None,
               pressure: str | None = None) -> dict:
    scored = score_profit(opp, pressure=pressure)
    obl = str(opp.get("obligation") or opp.get("user") or "")
    hf = opp.get("hf")
    ready = bool(obl) and hf is not None and float(hf) < 1.0
    return {
        "kind": "liq",
        "protocol_id": ID,
        "protocol": LABEL,
        "execute": "marginfi-jito",
        "program": MARGINFI_PROGRAM,
        "obligation": obl,
        "marginfi_group": MARGINFI_GROUP,
        "asset_bank": opp.get("asset_bank") or opp.get("coll_reserve") or "",
        "liab_bank": opp.get("liab_bank") or opp.get("debt_reserve") or "",
        "coll_reserve": opp.get("asset_bank") or "",
        "debt_reserve": opp.get("liab_bank") or "",
        "withdraw_mint": opp.get("coll_mint") or "",
        "repay_mint": opp.get("debt_mint") or "",
        "hf": hf,
        "ready": ready,
        "close_factor": scored["close_factor"],
        "profit_usd": scored.get("profit_usd"),
        "net_usd": scored.get("net_usd"),
        "repay_usd": scored.get("repay_usd"),
        "seized_usd": scored.get("seized_usd"),
        "balances": opp.get("balances") or [],
        "note": ("" if ready else "marginfi waiting on HF<1 with bank weights"),
    }


def remaining_accounts_for_balances(balances: list[dict],
                                    banks: dict[str, dict]) -> list[tuple[str, bool, bool]]:
    """Bank + oracle metas sorted by bank pubkey (marginfi risk engine layout)."""
    metas: list[tuple[str, bool, bool]] = []
    active = sorted(
        [b for b in balances if b.get("asset_shares", 0) > 0 or b.get("liability_shares", 0) > 0],
        key=lambda x: x["bank"],
    )
    for bal in active:
        bank = banks.get(bal["bank"]) or {}
        if not bank:
            continue
        metas.append((bal["bank"], False, True))
        setup = int(bank.get("oracle_setup") or 0)
        keys = bank.get("oracle_keys") or []
        if setup == _ORACLE_FIXED:
            continue
        tag = int(bank.get("asset_tag") or 0)
        if tag in (_ASSET_TAG_KAMINO, _ASSET_TAG_DRIFT) and len(keys) >= 2:
            metas.append((keys[0], False, False))
            metas.append((keys[1], False, False))
        elif keys:
            metas.append((keys[0], False, False))
    return metas


def liquidate_ix_data(asset_amount: int, liquidatee_n: int, liquidator_n: int) -> bytes:
    return (LIQUIDATE_IX_DISC
            + struct.pack("<QBB", int(asset_amount), int(liquidatee_n) & 0xFF,
                          int(liquidator_n) & 0xFF))


def hydrate_pubkeys(pubkeys: list[str], *, max_accounts: int = 40,
                      sol_px: float = 0.0) -> dict:
    import sol_scanner as sols
    keys = [p for p in pubkeys if _is_live_pk(p)][:max_accounts]
    banks = fetch_banks()
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
        px = float(sol_px or sols.fetch_sol_price() or 0)
        for pk, acc in zip(keys, vals or []):
            if not isinstance(acc, dict):
                continue
            if (acc.get("owner") or "") != MARGINFI_PROGRAM:
                continue
            data = acc.get("data")
            b64 = data[0] if isinstance(data, list) and data else ""
            if not b64:
                continue
            raw = base64.b64decode(b64)
            parsed = parse_account(pk, raw, banks=banks, sol_px=px)
            if not parsed:
                continue
            hydrated += 1
            if parsed["hf"] < 1.0 and parsed["debt_usd"] > 0.5:
                opps.append(parsed)
            elif parsed["hf"] < 1.15:
                watch.append(parsed)
    except Exception as e:
        errors.append(f"marginfi gma: {str(e)[:140]}")
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


def scan_obligations(*, max_accounts: int = 40, sol_px: float = 0.0) -> dict:
    import sol_scanner as sols
    probed = 0
    hydrated = 0
    opps: list[dict] = []
    watch: list[dict] = []
    errors: list[str] = []
    banks = fetch_banks()
    px = float(sol_px or 0.0)
    try:
        import base64 as b64
        disc_b64 = b64.b64encode(MARGINFI_ACCOUNT_DISCRIMINATOR).decode()
        group_b64 = b64.b64encode(_pk_bytes(MARGINFI_GROUP)).decode()
        result = sols.sol_gpa(
            MARGINFI_PROGRAM,
            filters=[
                {"dataSize": MARGINFI_ACCOUNT_SIZE},
                {"memcmp": {"offset": 0, "bytes": disc_b64}},
                {"memcmp": {"offset": 8, "bytes": group_b64}},
            ],
            encoding="base64",
            timeout=20.0,
        )
        probed = len(result or [])
        for acc in (result or [])[:max_accounts * 4]:
            pk = acc.get("pubkey") or ""
            raw = sols._acc_bytes(acc.get("account") or {})
            parsed = parse_account(pk, raw, banks=banks, sol_px=px)
            if not parsed:
                continue
            hydrated += 1
            if parsed["hf"] < 1.0 and parsed["debt_usd"] > 0.5:
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


def find_liquidator_account(authority: str) -> str | None:
    env = (os.environ.get("MARGINFI_LIQUIDATOR_ACCOUNT") or "").strip()
    if _is_live_pk(env):
        return env
    if not _is_live_pk(authority):
        return None
    import sol_scanner as sols
    try:
        import base64 as b64
        disc_b64 = b64.b64encode(MARGINFI_ACCOUNT_DISCRIMINATOR).decode()
        auth_b64 = b64.b64encode(_pk_bytes(authority)).decode()
        accs = sols.sol_gpa(
            MARGINFI_PROGRAM,
            filters=[
                {"dataSize": MARGINFI_ACCOUNT_SIZE},
                {"memcmp": {"offset": 0, "bytes": disc_b64}},
                {"memcmp": {"offset": 40, "bytes": auth_b64}},
            ],
            encoding="base64",
            timeout=15.0,
        )
        for acc in accs or []:
            pk = acc.get("pubkey") or ""
            if _is_live_pk(pk):
                return pk
    except Exception:
        pass
    return None


def scan_competitor_sigs(*, limit: int = 24, sol_px: float = 0.0) -> dict:
    import sol_scanner as sols
    events: list[dict] = []
    errors: list[str] = []
    try:
        sigs_result, _ = sols.sol_rpc("getSignaturesForAddress", [
            MARGINFI_PROGRAM,
            {"limit": min(limit * 3, 80), "commitment": "confirmed"},
        ], timeout=8.0)
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
                if not any("liquidat" in (lg or "").lower() for lg in logs):
                    continue
                msg = (tx_result.get("transaction") or {}).get("message") or {}
                keys = msg.get("accountKeys") or []
                obl = ""
                for k in keys:
                    ks = k if isinstance(k, str) else (k or {}).get("pubkey") or ""
                    if ks and len(ks) > 30:
                        obl = ks
                events.append({
                    "sig": sig,
                    "slot": sig_row.get("slot"),
                    "protocol_id": ID,
                    "protocol": LABEL,
                    "type": "liquidation",
                    "obligation": obl,
                    "user": obl,
                })
            except Exception:
                continue
    except Exception as e:
        errors.append(str(e)[:160])
    return {"events": events, "errors": errors}
