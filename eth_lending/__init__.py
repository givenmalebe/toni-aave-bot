"""ETH multi-protocol lending adapters for the TONI dashboard.

Flash liquidity is Aave V3 `flashLoan` (~9 bps) for every venue. Live send
uses GenericFlashLiquidator: Aave 4-arg flashLiquidate still works; Spark /
Compound / Morpho use flashLiquidatePool / Comet / Morpho on the same
executor when KIND() matches.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from . import aave, compound, morpho, spark, util, executor
from . import util as u

ADAPTERS = (
    ("aave", aave, True),
    ("spark", spark, True),
    ("compound", compound, True),
    ("morpho", morpho, True),
)

PILLS = (
    {"id": "all", "label": "All"},
    {"id": "aave", "label": "Aave"},
    {"id": "spark", "label": "Spark"},
    {"id": "compound", "label": "Compound"},
    {"id": "morpho", "label": "Morpho"},
)


def registered():
    rows = []
    for pid, mod, live in ADAPTERS:
        on = False
        try:
            on = bool(mod.enabled())
        except Exception:
            on = False
        rows.append({
            "id": pid,
            "label": getattr(mod, "LABEL", pid),
            "enabled": on,
            "live_ok": bool(live and on),
            "module": mod.__name__,
        })
    return rows


def scan_all(ctx: dict) -> dict:
    """Harvest + HF sweep every enabled adapter. Merge-sort happens in dashboard."""
    from_block = int(ctx.get("from_block") or 0)
    to_block = int(ctx.get("to_block") or 0)
    gas = float(ctx.get("gas_gwei") or 1.0)
    eth = float(ctx.get("eth_usd") or 0.0)
    contested = ctx.get("contested") or []
    recent = ctx.get("recent_comp") or []
    extra_users = list(ctx.get("borrowers") or [])
    leftovers = list(u.PROTOCOL_LEFTOVERS)
    errors = []
    n_logs = 0
    adapter_stats = []

    harvests = {}

    def harvest_one(item):
        pid, mod, _live = item
        if not mod.enabled():
            return pid, {"users": [], "n_logs": 0, "errors": ["disabled"],
                         "enabled": False}
        try:
            if from_block and to_block and from_block <= to_block:
                h = mod.harvest_users(from_block, to_block)
            else:
                h = {"users": [], "n_logs": 0, "errors": []}
            h["enabled"] = True
            return pid, h
        except Exception as e:  # noqa: BLE001
            return pid, {"users": [], "n_logs": 0, "errors": [str(e)[:160]],
                         "enabled": True}

    with ThreadPoolExecutor(max_workers=4) as ex:
        for pid, h in ex.map(harvest_one, ADAPTERS):
            harvests[pid] = h
            n_logs += int(h.get("n_logs") or 0)
            errors.extend(h.get("errors") or [])

    scans = {}

    def sweep_one(item):
        pid, mod, _live = item
        h = harvests.get(pid) or {}
        if not h.get("enabled"):
            return pid, {"opps": [], "watch": [], "scanned": 0, "skipped": {},
                         "enabled": False}
        users = list(h.get("users") or [])
        if pid == "aave":
            users = extra_users + users
        try:
            out = mod.scan_liquidatable(
                users,
                gas_gwei=gas,
                eth_usd=eth,
                contested=contested,
                recent_comp=recent,
            )
            out["enabled"] = True
            return pid, out
        except Exception as e:  # noqa: BLE001
            return pid, {"opps": [], "watch": [], "scanned": 0,
                         "skipped": {"rpc": 1}, "errors": [str(e)[:160]],
                         "enabled": True}

    with ThreadPoolExecutor(max_workers=4) as ex:
        for pid, s in ex.map(sweep_one, ADAPTERS):
            scans[pid] = s
            errors.extend(s.get("errors") or [])
            if s.get("leftover"):
                leftovers.append(f"{pid}: {s['leftover']}")

    opps, watch = [], []
    skipped = {"dust": 0, "healthy": 0, "no_account": 0, "negative": 0, "rpc": 0}
    scanned = 0
    skipped_n_by = {}
    for pid, _mod, live in ADAPTERS:
        s = scans.get(pid) or {}
        h = harvests.get(pid) or {}
        adapter_stats.append({
            "id": pid,
            "enabled": bool(s.get("enabled") or h.get("enabled")),
            "live_ok": bool(live and s.get("enabled")),
            "scanned": int(s.get("scanned") or 0),
            "opps": len(s.get("opps") or []),
            "n_logs": int(h.get("n_logs") or 0),
            "errors": (s.get("errors") or h.get("errors") or [])[:3],
        })
        scanned += int(s.get("scanned") or 0)
        for k, v in (s.get("skipped") or {}).items():
            if k in skipped:
                skipped[k] += int(v or 0)
        skipped_n_by[pid] = s.get("skipped") or {}
        opps.extend(s.get("opps") or [])
        watch.extend(s.get("watch") or [])

    watch.sort(key=lambda r: int(r.get("hf") or 0))
    return {
        "opps": opps,
        "watch": watch[:24],
        "scanned": scanned,
        "skipped": skipped,
        "n_logs": n_logs,
        "errors": errors[:12],
        "leftovers": leftovers,
        "adapters": adapter_stats,
        "pills": list(PILLS),
        "flash_fee_bps": u.aave_flash_bps(),
        "flash_fee_src": "aave-v3",
        "skipped_by": skipped_n_by,
    }


def scan_all_logs(from_block: int, to_block: int, extra_addrs=None) -> dict:
    events, errors, n_logs = [], [], 0

    def one(item):
        pid, mod, _live = item
        try:
            if not mod.enabled():
                return pid, {"events": [], "n_logs": 0, "errors": []}
            return pid, mod.scan_competitor_logs(
                from_block, to_block, extra_addrs=extra_addrs)
        except Exception as e:  # noqa: BLE001
            return pid, {"events": [], "n_logs": 0, "errors": [str(e)[:160]]}

    with ThreadPoolExecutor(max_workers=4) as ex:
        for pid, scanned in ex.map(one, ADAPTERS):
            evs = scanned.get("events") or []
            events.extend(evs)
            n_logs += int(scanned.get("n_logs") or 0)
            errors.extend(scanned.get("errors") or [])
    events.sort(
        key=lambda r: (r.get("block") or 0, r.get("log_index") or 0), reverse=True)
    return {
        "events": events,
        "n_logs": n_logs,
        "errors": errors[:12],
        "from_block": from_block,
        "to_block": to_block,
    }
