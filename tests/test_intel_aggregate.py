"""aggregate_liq_intel extended kwargs (competitors/watchlist/opps/meta)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "aave-v4-monitor"))

from intel_collector import aggregate_liq_intel  # noqa: E402


def _row(sel="0xc2fa746c", amount=100.0, hf=0.97, label="Aave V4 Spoke"):
    return {"sel": sel, "amount": amount, "health_factor": hf,
            "proto_label": label, "name": "liquidationCall"}


def test_backward_compat_two_args():
    r = aggregate_liq_intel([_row()], eth_price=3500.0)
    assert r["count_24h"] == 1
    assert r["volume_24h"] == 100.0
    assert r["competitors"]["searchers"] == 0


def test_competitors_meta_populates_block():
    r = aggregate_liq_intel(
        [_row(), _row()],
        competitors_meta={"unique_searchers": 3, "count_1h": 10,
                          "missed_by_us": 4},
    )
    assert r["competitors"]["searchers"] == 3
    assert r["competitors"]["missed"] == 4
    assert r["competitors"]["success_rate"] == 0.6


def test_competitor_list_dedup_fallback():
    rows = [{"searcher": "0xa"}, {"searcher": "0xa"}, {"searcher": "0xb"}]
    r = aggregate_liq_intel([_row()], competitors=rows)
    assert r["competitors"]["searchers"] == 2


def test_watchlist_and_opportunities_counts():
    wl = [{"hf": 0.99}, {"hf": 1.02}]
    opps = [{"profit_usd": 5.0}, {"profit_usd": 2.0}, {"profit_usd": 0.0}]
    r = aggregate_liq_intel([_row()], watchlist=wl, opportunities=opps)
    assert r["watchlist_size"] == 2
    assert r["open_opps"] == 2
    assert abs(r["best_opp_usd"] - 5.0) < 1e-9


def test_empty_rows_still_reports_meta():
    r = aggregate_liq_intel(
        [], competitors_meta={"unique_searchers": 2, "count_1h": 5,
                              "missed_by_us": 1},
        watchlist=[{"hf": 1.0}],
    )
    assert r["count_24h"] == 0
    assert r["competitors"]["searchers"] == 2
    assert r["watchlist_size"] == 1
