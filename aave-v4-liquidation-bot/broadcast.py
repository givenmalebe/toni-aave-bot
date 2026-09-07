#!/usr/bin/env python3
"""Sponsored Flashbots bundle builder stub — never submits."""
from __future__ import annotations


def build_sponsored_bundle(signed_hex, sponsor_hex, target_block):
    txs = [h for h in (sponsor_hex, signed_hex) if h]
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_sendBundle",
        "params": [{
            "txs": txs,
            "blockNumber": hex(int(target_block)),
        }],
    }
