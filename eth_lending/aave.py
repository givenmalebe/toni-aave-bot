"""Aave V3 Pool + V4 Spoke adapter. Live send uses existing LIQ_CONTRACT."""
from __future__ import annotations

import liquidation_bot as lb

from . import util as u


ID = "aave"
LABEL = "Aave"


def enabled() -> bool:
    return u.has_code(lb.V3_POOL) or u.has_code(lb.SPOKE)


def harvest_users(from_block: int, to_block: int) -> dict:
    try:
        return lb.harvest_event_users(from_block, to_block)
    except Exception as e:  # noqa: BLE001
        return {"users": [], "n_logs": 0, "errors": [str(e)[:160]]}


def scan_liquidatable(users, *, gas_gwei: float, eth_usd: float,
                      contested=None, recent_comp=None, max_users: int = 140) -> dict:
    result = lb.sweep_users(
        users,
        gas_gwei=gas_gwei,
        eth_usd=eth_usd,
        contested=contested,
        recent_comp=recent_comp,
        max_users=max_users,
    )
    flash_bps = u.aave_flash_bps()
    opps = []
    for o in result.get("opps") or []:
        rec = dict(o)
        venue = (rec.get("protocol") or "v3").lower()
        cover = float(rec.get("cover_usd") or rec.get("debt_usd") or 0)
        if cover and rec.get("net_usd") is not None:
            # lb estimate is bonus - proto - gas; subtract Aave flash premium
            extra = u.apply_flash_fee(
                cover,
                float(rec.get("bonus_usd") or 0),
                float(rec.get("bonus_usd") or 0) * 0.10,  # already in lb net; don't double-sub
                float(rec.get("gas_usd") or 0),
                flash_bps,
            )
            # lb already subtracted proto+gas. Only deduct flash.
            rec["net_usd"] = round(float(rec["net_usd"]) - extra["flash_fee_usd"], 2)
            rec["profit_usd"] = rec["net_usd"]
            rec["flash_fee_usd"] = extra["flash_fee_usd"]
            rec["flash_fee_bps"] = extra["flash_fee_bps"]
        else:
            rec["flash_fee_bps"] = flash_bps
            rec["flash_fee_usd"] = round(cover * flash_bps / 10_000.0, 4)
            rec["profit_usd"] = rec.get("net_usd")
        rec["protocol_id"] = ID
        rec["protocol"] = LABEL
        rec["protocol_label"] = LABEL
        rec["venue"] = venue  # v3 | v4
        rec["live_ok"] = True
        rec["live_block_reason"] = ""
        rec["flash_fee_src"] = "aave-v3"
        rec["flash_note"] = f"Aave V3 flashLoan {rec.get('flash_fee_bps') or flash_bps} bps"
        rec["flash_plan"] = {
            "flash_src": "aave-v3",
            "flash_pool": lb.V3_POOL,
            "fn": "flashLiquidate",
            "live_ok": True,
        }
        if rec.get("net_usd") is not None and float(rec["net_usd"]) <= 0:
            continue
        opps.append(rec)
    watch = []
    for w in result.get("watch") or []:
        ww = dict(w)
        ww["protocol_id"] = ID
        ww["protocol"] = LABEL
        watch.append(ww)
    return {
        "opps": opps,
        "watch": watch,
        "scanned": result.get("scanned") or 0,
        "skipped": result.get("skipped") or {},
        "n_hf_ok": result.get("n_hf_ok") or 0,
    }


def scan_competitor_logs(from_block: int, to_block: int, extra_addrs=None) -> dict:
    scanned = lb.scan_liquidation_logs(from_block, to_block, extra_addrs=extra_addrs)
    events = []
    for ev in scanned.get("events") or []:
        rec = dict(ev)
        rec["protocol_id"] = ID
        rec["protocol"] = LABEL
        rec["venue"] = rec.get("protocol") if rec.get("protocol") in ("v3", "v4") else "v3"
        events.append(rec)
    return {
        "events": events,
        "n_logs": scanned.get("n_logs") or 0,
        "errors": scanned.get("errors") or [],
        "from_block": scanned.get("from_block") or from_block,
        "to_block": scanned.get("to_block") or to_block,
    }
