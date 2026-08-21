import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dashboard


def _make_fake_dash():
    class FakeDash(dashboard.Dashboard):
        def __init__(self):
            self.state = {"watchlist": [{
                "user": "0xA", "collateral": "0xc1", "hf": 1.04,
                "coll_usd": 1101.2, "debt_usd": 900.0,
                "liq_threshold": 0.85, "side": "collateral"}]}
            self._reg_cache = ({"0xf1": [{
                "user": "0xA", "asset": "0xc1", "side": "collateral",
                "hf": 1.04, "coll_usd": 1101.2, "debt_usd": 900.0,
                "liq_threshold": 0.85}]}, time.time())
            self._last_prices = {}
            self._eth_hot_kick = None
            self._sol_hot_kick = None
            self.eth_feed = None
            self.sol_feed = None

        def log(self, *a, **k): pass
    return FakeDash()


def test_on_oracle_tick_flags_crossed_position():
    fd = _make_fake_dash()
    kicks = []
    class Kick:
        def set(self): kicks.append(1)
    fd._eth_hot_kick = Kick()
    fd._on_oracle_tick("0xF1", 100.0)
    assert len(kicks) == 0
    fd._on_oracle_tick("0xF1", 80.0)
    assert len(kicks) == 1


def test_feed_status_shape():
    fd = _make_fake_dash()
    out = fd._feeds_status()
    assert out == {"eth": {"mode": "off"}, "sol": {"mode": "off"}}


def test_shadow_counters_increment():
    fd = _make_fake_dash()
    fd._on_oracle_tick("0xF1", 100.0)
    fd._on_sol_account_change("Obl1")
    assert fd.state["shadow"]["eth_ticks"] == 1
    assert fd.state["shadow"]["sol_events"] == 1
