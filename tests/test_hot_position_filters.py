import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dashboard


def test_coerce_hf_handles_string():
    assert dashboard.coerce_hf("0.97") == 0.97


def test_coerce_hf_handles_none_and_garbage():
    assert dashboard.coerce_hf(None) == 999.0
    assert dashboard.coerce_hf("") == 999.0
    assert dashboard.coerce_hf("abc") == 999.0


def test_get_hot_eth_positions_with_string_hf():
    class FakeDash:
        state = {"watchlist": [
            {"user": "0xA", "hf": "0.97"},
            {"user": "0xB", "hf": 1.5},
            {"user": "0xC", "hf": None},
        ]}
        _get_hot_eth_positions = dashboard.Dashboard._get_hot_eth_positions
    got = FakeDash._get_hot_eth_positions(FakeDash())
    assert [p["user"] for p in got] == ["0xA"]
