#!/usr/bin/env python3
"""Aave V4 Hub/Spoke RPC helpers for the TONI dashboard.

Local boot stub of aave-v4-monitor. Real Hub/Spoke calls against mainnet.
"""
from __future__ import annotations

import time

import requests

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

HUB = "0xCca852Bc40e560adC3b1Cc58CA5b55638ce826c9"
_SEL_GET_ASSET_COUNT = "0xa0aead4d"
_SEL_GET_SPOKE_COUNT = "0x58a54078"
_SEL_GET_SPOKE_ADDRESS = "0x132a8bea"


def rpc(url, method, params, timeout=10):
    r = requests.post(
        url,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        headers={"Content-Type": "application/json", "User-Agent": _UA},
        timeout=timeout,
    )
    r.raise_for_status()
    out = r.json()
    if "error" in out:
        raise RuntimeError(f"RPC error {method}: {out['error']}")
    return out["result"]


def jrpc(urls, method, params):
    if isinstance(urls, str):
        urls = [urls]
    last = None
    for url in urls or []:
        try:
            return rpc(url, method, params)
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(0.2)
    raise RuntimeError(f"all RPCs failed for {method}: {last}")


def eth_call(url, to, data):
    return rpc(url, "eth_call", [{"to": to, "data": data}, "latest"])


def call_uint(url, to, data):
    raw = eth_call(url, to, data)
    return int(raw, 16)


def get_spokes(url, asset_id):
    pad = hex(int(asset_id))[2:].rjust(64, "0")
    n = call_uint(url, HUB, _SEL_GET_SPOKE_COUNT + pad)
    out = []
    for i in range(min(int(n), 24)):
        data = _SEL_GET_SPOKE_ADDRESS + pad + hex(i)[2:].rjust(64, "0")
        raw = eth_call(url, HUB, data)
        addr = "0x" + (raw or "0x")[-40:]
        if int(addr, 16) != 0:
            out.append({"address": addr, "asset_id": asset_id, "index": i})
    return out


def get_pending_txs(rpc_url):
    try:
        content = rpc(rpc_url, "txpool_content", [], timeout=30)
    except Exception:
        return [], "unavailable"
    txs = []
    for bucket in (content.get("pending") or {}).values():
        for t in bucket.values():
            txs.append({
                "hash": t.get("hash", ""),
                "from": t.get("from", ""),
                "to": t.get("to") or "",
                "value": t.get("value", "0x0"),
                "input": (t.get("input") or "")[:10],
                "gasPrice": t.get("gasPrice") or t.get("maxFeePerGas") or "0x0",
                "maxPriorityFeePerGas": t.get("maxPriorityFeePerGas", "0x0"),
                "gas": t.get("gas", "0x0"),
            })
    return txs, "txpool_content"
