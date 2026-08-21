import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sol_fees import (TipFloor, adaptive_tip_lamports, priority_fee_lamports,
                      LossBreaker, percentile)


def _floor(p25, p50, p75, p95):
    f = TipFloor()
    f.update({"data": [{
        "landed_tips_25th_percentile": p25 / 1e9,
        "landed_tips_50th_percentile": p50 / 1e9,
        "landed_tips_75th_percentile": p75 / 1e9,
        "landed_tips_95th_percentile": p95 / 1e9}]})
    return f


def test_tip_floor_update_and_stale():
    f = TipFloor()
    assert f.stale()
    assert not f.update({"data": []})
    assert f.update({"data": [{
        "landed_tips_50th_percentile": 0.001,
        "landed_tips_25th_percentile": 0.0005,
        "landed_tips_75th_percentile": 0.002,
        "landed_tips_95th_percentile": 0.005}]})
    assert f.pcts["p50"] == 1_000_000
    assert not f.stale()


def test_adaptive_tip_capped_by_profit_share():
    f = _floor(500_000, 1_000_000, 2_000_000, 5_000_000)
    # $1 pre-tip profit at $100/SOL -> share cap 30% = $0.30 = 3M lamports.
    tip = adaptive_tip_lamports(f, 1.0, 100.0, contested=False)
    assert tip == min(f.pcts["p50"], 3_000_000)
    # Tiny profit: absolute cap and floor interplay keep tip sane.
    tip_small = adaptive_tip_lamports(f, 0.05, 100.0, contested=False)
    assert 0 <= tip_small <= 5_000_000


def test_adaptive_tip_escalates_when_contested():
    f = _floor(500_000, 1_000_000, 2_000_000, 5_000_000)
    calm = adaptive_tip_lamports(f, 10.0, 100.0, contested=False)
    hot = adaptive_tip_lamports(f, 10.0, 100.0, contested=True)
    assert hot == f.pcts["p75"] * (10.0 > 0) or hot >= calm
    assert hot <= 5_000_000


def test_priority_fee_bounds():
    assert priority_fee_lamports([], 1.0, 100.0) >= 1000
    big = priority_fee_lamports([500_000] * 20, 5.0, 100.0)
    assert 1000 <= big <= 200_000
    zero_profit = priority_fee_lamports([500_000] * 20, 0.0, 100.0)
    assert zero_profit == 1000


def test_loss_breaker_pauses_and_resets():
    b = LossBreaker(threshold_lamports=1_000_000)
    assert not b.paused
    b.record_loss(400_000)
    b.record_loss(400_000)
    assert not b.paused
    b.record_loss(300_000)
    assert b.paused
    assert b.lost_lamports == 1_100_000
    b.reset()
    assert not b.paused and b.lost_lamports == 0


def test_loss_breaker_window_expiry():
    b = LossBreaker(threshold_lamports=1_000_000)
    old = time.time() - 90000
    b.record_loss(2_000_000, ts=old)
    assert b.lost_lamports == 0
    assert not b.paused


def test_percentile_interpolates():
    assert percentile([10, 20, 30, 40], 0.5) == 25
    assert percentile([], 0.5) == 0
    assert percentile([7], 0.95) == 7
