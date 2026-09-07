#!/usr/bin/env python3
"""Intel dataset readers for the TONI dashboard."""
from __future__ import annotations

import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_RECORDS = os.path.join(_HERE, "intel_data", "records.jsonl")


def load_records():
    if not os.path.isfile(_RECORDS):
        return []
    out = []
    try:
        with open(_RECORDS, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return out[-2000:]


def hourly_activity(records):
    hours = {}
    for r in records or []:
        mv = r.get("mev_classes") or {}
        w = (int(mv.get("liq") or 0) + int(mv.get("spoke") or 0)
             + int(mv.get("aave") or 0))
        h = r.get("utc_hour")
        if h is None:
            continue
        hours[int(h)] = hours.get(int(h), 0) + max(w, 0)
    return hours


def dow_activity(records):
    dows = {}
    for r in records or []:
        mv = r.get("mev_classes") or {}
        w = (int(mv.get("liq") or 0) + int(mv.get("spoke") or 0)
             + int(mv.get("aave") or 0))
        d = r.get("utc_dow")
        if d is None:
            continue
        dows[int(d)] = dows.get(int(d), 0) + max(w, 0)
    return dows


def readiness(records):
    n = len(records or [])
    if n <= 0:
        return 0.0
    return round(min(100.0, n / 4.0), 1)


def oracle_moves(records):
    return []
