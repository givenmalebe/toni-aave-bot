import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "aave-v4-liquidation-bot"))

import eth_lending.aave as aave


def test_oracle_feed_map_graceful_failure(monkeypatch):
    monkeypatch.setattr(aave.u, "call", lambda to, data: None)
    assert aave.oracle_feed_for_assets(["0xC1"]) == {}


def test_oracle_feed_map_parses_feed(monkeypatch):
    raw = "0x" + "0" * 24 + "1111111111222222222233333333334444444444"
    seen = []
    def fake_call(to, data):
        seen.append((to, data[:10]))
        return raw
    monkeypatch.setattr(aave.u, "call", fake_call)
    out = aave.oracle_feed_for_assets(["0xC1", "0xc1"])
    assert out == {"0xc1": "0x1111111111222222222233333333334444444444"}
    assert len(seen) == 1
    assert seen[0][1] == "0x3850c7bd"


def test_last_price_ratio_logic():
    last = {}
    def ratio(feed, price):
        old = last.get(feed)
        last[feed] = price
        return None if old in (None, 0) else price / old
    assert ratio("f", 100.0) is None
    assert abs(ratio("f", 50.0) - 0.5) < 1e-9
