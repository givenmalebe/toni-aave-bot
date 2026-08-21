import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from feeds.registry import (build_eth_registry, affected, recomputed_hf,
                            build_sol_shards)


WL = [
    {"user": "0xA", "collateral": "0xC1", "hf": 1.04, "coll_usd": 1101.2,
     "debt_usd": 900.0, "liq_threshold": 0.85, "side": "collateral"},
    {"user": "0xB", "collateral": "0xC2", "hf": 1.5, "coll_usd": 500.0,
     "debt_usd": 300.0, "liq_threshold": 0.8, "side": "collateral"},
]


def test_build_and_lookup():
    reg = build_eth_registry(WL, {"0xC1": "0xF1", "0xC2": "0xF2"})
    assert [p["user"] for p in affected(reg, "0xf1")] == ["0xA"]
    assert affected(reg, "0xff") == []


def test_recomputed_hf_crosses_threshold():
    pos = affected(build_eth_registry(WL, {"0xC1": "0xF1"}), "0xF1")[0]
    hf = recomputed_hf(pos, price_ratio=0.90)
    assert hf < 1.0
    assert recomputed_hf(pos, price_ratio=1.0) > 1.0


def test_sol_shards():
    shards = build_sol_shards([f"obl{i}" for i in range(5)], 2)
    assert len(shards) == 2 and sum(len(s) for s in shards) == 5
