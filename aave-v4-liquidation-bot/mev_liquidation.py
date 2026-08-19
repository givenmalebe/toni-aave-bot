#!/usr/bin/env python3
"""Flash-liquidation plan builder for the TONI dashboard."""
from __future__ import annotations

import os

import liquidation_bot as lb
import precompute_eth as pe

CONTRACT = os.environ.get("LIQ_CONTRACT", "")
LIQ_SIG = os.environ.get(
    "LIQ_SIG", "flashLiquidate(address,address,address,uint256)")


def build_full_plan(user, gas_gwei=None, eth_usd=None, allow_near=False,
                    max_hf=1.05):
    cached = pe.get(user or "")
    if cached and cached.get("live_ok"):
        plan = {
            "user": user,
            "liquidatable": True,
            "long_tail": False,
            "contract": cached.get("debt_token", CONTRACT or ""),
            "liq_sig": cached.get("selector", LIQ_SIG),
            "liq_args": [cached.get("calldata", "")],
            "gas_limit": cached.get("gas_limit", 1500000),
            "net_usd": cached.get("estimated_profit_usd", 0),
            "precomputed": True,
            "protocol": cached.get("protocol", "aave"),
            "collateral": cached.get("collateral", ""),
            "debt": cached.get("debt", ""),
            "hf": cached.get("hf", 0),
        }
        if gas_gwei is not None:
            plan["gas_gwei"] = float(gas_gwei)
        return plan

    plan = lb.build_plan(
        user, gas_gwei=gas_gwei, eth_usd=eth_usd,
        allow_near=allow_near, max_hf=max_hf)
    if not plan:
        return None
    plan["long_tail"] = is_long_tail(plan)
    plan["contract"] = CONTRACT or ""
    plan["liq_sig"] = LIQ_SIG
    if gas_gwei is not None:
        plan["gas_gwei"] = float(gas_gwei)
    return plan


def is_long_tail(out) -> bool:
    if not out:
        return False
    if isinstance(out, dict):
        if out.get("long_tail") or out.get("edge"):
            return True
        for k in ("coll_sym", "debt_sym"):
            sym = str(out.get(k) or "").upper()
            if any(x in sym for x in (
                    "EURC", "RLUSD", "FRAX", "GDOLLAR", "GHO",
                    "CBBTC", "WEETH", "WSTETH")):
                return True
    return False
