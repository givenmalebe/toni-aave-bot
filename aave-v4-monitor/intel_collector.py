#!/usr/bin/env python3
"""Mempool / intel collector stub for the TONI dashboard."""
from __future__ import annotations

import json
import os
import time

LIQ_SELS = {
    "0xc2fa746c",  # liquidationCall
}
ROUTERS = {
    "0x7a250d5630b4cf539739df2c5dacb4c659f2488d",
    "0xe592427a0aece92de3edee1f18e0157c05861564",
    "0x68b3465833fb72a70ecdf485e0e4c7bd8665fc45",
    "0x3fc91a3afd70395cd496c647d5a6cc9d4b2b7fad",
    "0x1111111254eeb25477b68fb85ed929f73a960582",
    "0xdef1c0ded9bec7f1a1670819833240f027b25eff",
    "0xba12222222228d8ba445958a75a0704d566bf2c8",
    "0xd9e1ce17f2641f24ae83637ab66a2cca9c378b9f",
}
AAVE_POOLS = {
    "0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2",
}
MEMPOOL_RPC = os.environ.get("MEMPOOL_RPC", "https://ethereum-rpc.publicnode.com")

_HERE = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR = os.path.join(_HERE, "intel_data")
_RECORDS = os.path.join(_DATA_DIR, "records.jsonl")

_SPOKE_SELS = {
    "0xc2fa746c": "liquidationCall",
    "0x617ba037": "supply",
    "0xa415bcad": "borrow",
    "0x573ade81": "repay",
    "0x69328dec": "withdraw",
    "0x110496e5": "supply",
    "0x2e1a7d4d": "withdraw",
    "0xc29942d8": "borrow",
    "0x0e752702": "repay",
    "0x8b2a4df5": "supplyCollateral",
    "0x02c205f0": "withdrawCollateral",
    "0xe8eda9df": "supply",
    "0x69c3202a": "withdraw",
    "0x28530a47": "setEMode",
    "0x38ed1739": "swapExactTokensForTokens",
    "0x095ea7b3": "approve",
}

# All known lending pool/spoke addresses → (short name, protocol)
_LENDING_ADDRS = {
    "0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2": ("Aave V3 Pool", "aave-v3"),
    "0x94e7a5dcb e816e498b89ab752661904e2f56c485".replace(" ", ""): ("Aave V4 Spoke", "aave-v4"),
    "0xc13e21b648a5ee794902342038ff3adab66be987": ("Spark Lend", "spark"),
    "0xc3d688b66703497daa19211eedff47f25384cdc3": ("Compound cUSDCv3", "compound"),
    "0xa17581a9e3356d9a858b789d68b4d866e593ae94": ("Compound cWETHv3", "compound"),
    "0x3afdc9bca9213a35503b077a6072f3d0d5ab0840": ("Compound cUSDTv3", "compound"),
    "0xbbbbbbbbbb9cc5e90e3b3af64bdaf62c37eeffcb": ("Morpho Blue", "morpho"),
}

_LIQLIQ_SELS = {
    "0xc2fa746c",  # liquidationCall (Aave V3/V4)
    "0xc3cecfd2",  # absorb (Compound V3)
    "0xd8eabcb8",  # liquidate (Morpho Blue)
    "0x1bc1e9ba",  # batchAbsorb (Compound V3)
}

_LIQ_NAMES = {
    "0xc2fa746c": "liquidationCall",
}


def gas_class(gwei) -> str:
    try:
        g = float(gwei or 0)
    except (TypeError, ValueError):
        g = 0.0
    if g <= 0.5:
        return "low"
    if g <= 5:
        return "normal"
    if g <= 20:
        return "elevated"
    if g <= 50:
        return "high"
    return "extreme"


def spoke_txs(txs):
    """Return only lending-protocol txs with protocol label and method name."""
    rows = []
    for t in txs or []:
        to = (t.get("to") or "").lower()
        info = _LENDING_ADDRS.get(to)
        if not info:
            continue
        proto_label, proto = info
        inp = (t.get("input") or t.get("selector") or "").lower()
        sel = inp[:10]
        name = _SPOKE_SELS.get(sel) or _LIQ_NAMES.get(sel) or sel or "?"
        args = []
        if sel in _LIQLIQ_SELS:
            raw = inp[10:]
            if len(raw) >= 64 * 4:
                args = [f"user={raw[64:104]}", f"collateral={raw[104:144]}", f"debt={raw[144:184]}"]
        rows.append({
            "name": name,
            "proto": proto,
            "proto_label": proto_label,
            "args": args,
            "hash": t.get("hash") or "",
            "to": to,
            "from": t.get("from") or "",
            "hot": sel in _LIQLIQ_SELS,
        })
    return rows


def watch_txs(txs, addrs):
    want = {(a or "").lower() for a in (addrs or [])}
    out = []
    for t in txs or []:
        to = (t.get("to") or "").lower()
        if to in want:
            out.append({
                "hash": t.get("hash") or "",
                "to": to,
                "from": t.get("from") or "",
                "input": (t.get("input") or "")[:10],
            })
    return out


def mev_classes(txs):
    mv = {"liq": 0, "router": 0, "spoke": 0, "aave": 0, "create": 0, "other": 0}
    samples = []
    for t in txs or []:
        to = (t.get("to") or "").lower()
        inp = (t.get("input") or t.get("selector") or "").lower()
        if not to:
            cls = "create"
        elif inp[:10] in LIQ_SELS:
            cls = "liq"
        elif to in ROUTERS:
            cls = "router"
        elif to in AAVE_POOLS:
            cls = "aave"
        else:
            cls = "other"
        mv[cls] = mv.get(cls, 0) + 1
        if cls != "other" and len(samples) < 8:
            samples.append({"cls": cls, "to": to, "hash": t.get("hash") or ""})
    return mv, samples


def collect_once(_unused=0):
    import aave_v4_monitor as avm
    ts = int(time.time())
    gm = time.gmtime(ts)
    rec = {
        "ts": ts,
        "block": 0,
        "gas_gwei": 0.0,
        "spoke_txs": [],
        "mempool_txs": 0,
        "mev_classes": {"liq": 0, "router": 0, "spoke": 0, "aave": 0, "create": 0},
        "utc_hour": gm.tm_hour,
        "utc_dow": gm.tm_wday,
    }
    try:
        from liquidation_bot import RPC_CALL, jrpc
        rec["block"] = int(jrpc(RPC_CALL, "eth_blockNumber", []), 16)
        rec["gas_gwei"] = int(jrpc(RPC_CALL, "eth_gasPrice", []), 16) / 1e9
    except Exception:
        try:
            rec["block"] = int(avm.rpc(MEMPOOL_RPC, "eth_blockNumber", []), 16)
            rec["gas_gwei"] = int(avm.rpc(MEMPOOL_RPC, "eth_gasPrice", []), 16) / 1e9
        except Exception:
            pass
    return rec


def append(rec):
    os.makedirs(_DATA_DIR, exist_ok=True)
    with open(_RECORDS, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


def aggregate_liq_intel(spoke_rows, eth_price=3500.0):
    """Aggregate liquidation intel from spoke transaction rows."""
    LIQ_SEL_PREFIXES = ("0xc2fa746c", "0xd8eabcb8")  # Aave liquidationCall, Morpho liquidate
    PROTO_MAP = {
        "Aave V3 Pool": "aave_v3",
        "Aave V4 Spoke": "aave_v3",
        "Compound cUSDCv3": "compound_v3",
        "Compound cWETHv3": "compound_v3",
        "Compound cUSDTv3": "compound_v3",
        "Morpho Blue": "morpho",
        "Spark Lend": "spark",
    }

    result = {
        "volume_24h": 0.0,
        "count_24h": 0,
        "avg_size": 0.0,
        "gas_per_liq": 0.0,
        "protocols": {
            "aave_v3": {"count": 0, "volume": 0.0},
            "compound_v3": {"count": 0, "volume": 0.0},
            "morpho": {"count": 0, "volume": 0.0},
            "spark": {"count": 0, "volume": 0.0},
        },
        "health_dist": {"<1.0": 0, "1.0-1.05": 0, "1.05-1.1": 0, ">1.1": 0},
        "competitors": {"searchers": 0, "success_rate": 0.0, "missed": 0},
        "volume_history": [],
    }

    liq_rows = [r for r in spoke_rows if r.get("sel") in LIQ_SEL_PREFIXES or r.get("name", "").startswith("liquidat")]
    if not liq_rows:
        return result

    result["count_24h"] = len(liq_rows)

    for row in liq_rows:
        proto_label = row.get("proto_label", "")
        proto_key = PROTO_MAP.get(proto_label, None)
        try:
            amount = float(row.get("amount", 0) or 0)
        except (ValueError, TypeError):
            amount = 0.0

        if proto_key and proto_key in result["protocols"]:
            result["protocols"][proto_key]["count"] += 1
            result["protocols"][proto_key]["volume"] += amount

        result["volume_24h"] += amount

        hf = row.get("health_factor")
        if hf is not None:
            try:
                hf_f = float(hf)
                if hf_f < 1.0:
                    result["health_dist"]["<1.0"] += 1
                elif hf_f < 1.05:
                    result["health_dist"]["1.0-1.05"] += 1
                elif hf_f < 1.1:
                    result["health_dist"]["1.05-1.1"] += 1
                else:
                    result["health_dist"][">1.1"] += 1
            except (ValueError, TypeError):
                pass

    if result["count_24h"] > 0:
        result["avg_size"] = result["volume_24h"] / result["count_24h"]

    return result
