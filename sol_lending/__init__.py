"""SOL multi-protocol lending adapters for the TONI SOL dashboard.

Mirrors the eth_lending/ pattern: each protocol module exports
  enabled() -> bool
  scan_obligations(max_accounts) -> dict
  scan_competitor_sigs(limit, sol_px) -> dict
  score_profit(opp, sol_px, priority_median, pressure) -> dict
  build_plan(opp, priority_median, pressure) -> dict

The sweep orchestrator merges results across all enabled adapters.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from . import kamino, marginfi, drift

ADAPTERS: tuple[tuple[str, Any, bool], ...] = (
    ("kamino", kamino, True),
    ("marginfi", marginfi, True),
    ("drift", drift, True),
)

PILLS = (
    {"id": "all", "label": "All"},
    {"id": "solend", "label": "Solend"},
    {"id": "kamino", "label": "Kamino"},
    {"id": "marginfi", "label": "MarginFi"},
    {"id": "drift", "label": "Drift"},
)


def registered() -> list[dict]:
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


def scan_all_obligations(*, sol_px: float = 0.0,
                         priority_median: int | None = None,
                         pressure: str | None = None,
                         max_accounts: int = 40) -> dict:
    """Probe obligations across all enabled SOL lending adapters."""
    all_opps: list[dict] = []
    all_watch: list[dict] = []
    errors: list[str] = []
    adapter_stats: list[dict] = []
    probed = 0
    hydrated = 0

    def probe_one(item):
        pid, mod, _live = item
        try:
            if not mod.enabled():
                return pid, {"opportunities": [], "watch": [], "probed": 0,
                             "hydrated": 0, "enabled": False}
            out = mod.scan_obligations(max_accounts=max_accounts)
            out["enabled"] = True
            return pid, out
        except Exception as e:
            return pid, {"opportunities": [], "watch": [], "probed": 0,
                         "hydrated": 0, "errors": [str(e)[:160]], "enabled": True}

    _mod_map = {pid: mod for pid, mod, _ in ADAPTERS}

    with ThreadPoolExecutor(max_workers=3) as ex:
        for pid, result in ex.map(probe_one, ADAPTERS):
            opps = result.get("opportunities") or []
            for o in opps:
                o.setdefault("protocol_id", pid)
                o.setdefault("protocol", getattr(
                    _mod_map.get(pid) or type("", (), {"LABEL": pid}),
                    "LABEL", pid))
            all_opps.extend(opps)
            watch = result.get("watch") or []
            for w in watch:
                w.setdefault("protocol_id", pid)
                w.setdefault("protocol", getattr(
                    _mod_map.get(pid) or type("", (), {"LABEL": pid}),
                    "LABEL", pid))
            all_watch.extend(watch)
            probed += result.get("probed") or 0
            hydrated += result.get("hydrated") or 0
            errs = result.get("errors") or []
            errors.extend(f"{pid}: {e}" for e in errs[:2])
            adapter_stats.append({
                "id": pid,
                "enabled": bool(result.get("enabled")),
                "probed": result.get("probed") or 0,
                "hydrated": result.get("hydrated") or 0,
                "opps": len(opps),
                "errors": errs[:2],
            })

    return {
        "opportunities": all_opps,
        "watch": all_watch,
        "probed": probed,
        "hydrated": hydrated,
        "errors": errors[:12],
        "adapters": adapter_stats,
        "pills": list(PILLS),
    }


def scan_all_competitors(*, limit: int = 24, sol_px: float = 0.0) -> dict:
    """Decode recent liquidation txs across all enabled SOL adapters."""
    all_events: list[dict] = []
    errors: list[str] = []

    def one(item):
        pid, mod, _live = item
        try:
            if not mod.enabled():
                return pid, {"events": [], "errors": []}
            return pid, mod.scan_competitor_sigs(limit=limit, sol_px=sol_px)
        except Exception as e:
            return pid, {"events": [], "errors": [str(e)[:160]]}

    with ThreadPoolExecutor(max_workers=3) as ex:
        for pid, result in ex.map(one, ADAPTERS):
            evs = result.get("events") or []
            for ev in evs:
                ev.setdefault("protocol_id", pid)
            all_events.extend(evs)
            errors.extend(result.get("errors") or [])

    all_events.sort(key=lambda r: r.get("slot") or 0, reverse=True)
    return {"events": all_events, "errors": errors[:12]}
