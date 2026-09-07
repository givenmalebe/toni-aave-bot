"""Race-mode tuition accounting + contested-gate expiry + sweep multi-fire."""
import base64
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import dashboard as dash
import sol_scanner as s


def _dash():
    obj = object.__new__(dash.Dashboard)
    obj.sol_race_mode = True
    obj.sol_race_tuition_day_sol = 0.10
    obj.sol_race_tip_cap_sol = 0.002
    obj.sol_race_max_fires = 3
    obj.sol_race_tuition_spent = 0.0
    obj._sol_race_day = time.strftime("%Y-%m-%d", time.gmtime())
    obj.sol_race_inflight = {}
    obj.sol_race_last = {}
    obj.state = {"sol": {"sol_price_usd": 100.0}}
    return obj


@pytest.fixture(autouse=True)
def _isolate_bandit(tmp_path, monkeypatch):
    monkeypatch.setattr(s, "_TIP_BANDIT_PATH", str(tmp_path / "sol_tip_bandit.json"))
    monkeypatch.setattr(s, "_TIP_BANDIT_LOADED", False)
    s._TIP_BANDIT = {"calm": 1.0, "contested": 1.0}
    yield
    s._TIP_BANDIT = {"calm": 1.0, "contested": 1.0}
    s._TIP_BANDIT_LOADED = False


def test_bandit_defaults_and_arm_selection():
    assert s.tip_mult() == 1.0
    assert s.tip_mult("calm") == 1.0
    assert s.tip_mult("contested") == 1.0
    assert s.tip_mult("tip-war") == 1.0
    assert s._is_contested_pressure("contested") is True
    assert s._is_contested_pressure("tip-war") is True
    assert s._is_contested_pressure("high") is False
    assert s._is_contested_pressure(None) is False


def test_bandit_escalates_on_loss_and_decays_on_win():
    nxt = s.update_tip_mult(True, won=False, tip_lamports=1_000_000)
    assert nxt == pytest.approx(1.25)
    assert s.tip_mult("contested") == pytest.approx(1.25)
    assert s.tip_mult("calm") == 1.0          # calm arm unaffected
    nxt = s.update_tip_mult(True, won=True, tip_lamports=1_000_000)
    assert nxt == pytest.approx(1.25 * 0.9)


def test_bandit_clamps_to_bounds():
    s._TIP_BANDIT["contested"] = 1.0
    for _ in range(8):
        s.update_tip_mult(True, won=False, tip_lamports=1_000_000)
    assert s.tip_mult("contested") == pytest.approx(3.0)
    for _ in range(20):
        s.update_tip_mult(True, won=True, tip_lamports=1_000_000)
    assert s.tip_mult("contested") == pytest.approx(0.7)


def test_bandit_zero_tip_is_noop():
    assert s.update_tip_mult(True, won=False, tip_lamports=0) == 1.0
    assert s.tip_mult("contested") == 1.0


def test_bandit_persists_across_reload():
    s.update_tip_mult(True, won=False, tip_lamports=1_000_000)
    s._TIP_BANDIT = {"calm": 1.0, "contested": 1.0}
    s._TIP_BANDIT_LOADED = False
    assert s.tip_mult("contested") == pytest.approx(1.25)
    assert s.tip_mult("calm") == pytest.approx(1.0)


def test_dynamic_tip_scales_with_mult_and_stays_capped():
    s._COMP_TIP_WINDOW.clear()
    s.track_comp_jito_tip(500_000_000)   # 0.5 SOL competitor p95
    bid_base = s._dynamic_jito_lamports(200.0, 100.0, "contested", 0.0)
    s._TIP_BANDIT["contested"] = 2.0
    bid_boost = s._dynamic_jito_lamports(200.0, 100.0, "contested", 0.0)
    assert bid_boost > bid_base
    assert bid_boost == 500_000_000 * 1.15 * 2
    # spend cap still honored even with a huge multiplier
    capped = s._dynamic_jito_lamports(30.0, 100.0, "contested", 0.0)
    assert capped == int((30.0 - 1e-6) / 100.0 * 1e9)
    s._COMP_TIP_WINDOW.clear()


def test_dynamic_tip_ignores_mult_without_competitors():
    s._COMP_TIP_WINDOW.clear()
    s._TIP_BANDIT["contested"] = 2.0
    bid = s._dynamic_jito_lamports(200.0, 100.0, "hot", 0.0)
    share = s._jito_tip_share()
    assert bid == int(min(200.0 * share, 200.0) / 100.0 * 1e9)
    s._COMP_TIP_WINDOW.clear()


def test_adjudicate_trains_bandit(monkeypatch):
    d = _dash()
    rec = {"stage": "sent", "tip_lamports": 500_000,
           "expected_profit_usd": 3.0, "protocol_id": "solend",
           "hf": 0.9, "contested": True}
    d._sol_race_register_inflight("OblB", rec)
    d._sol_race_adjudicate("OblB", "lost")
    assert s.tip_mult("contested") == pytest.approx(1.25)
    rec2 = dict(rec, **{"stage": "confirmed"})
    d._sol_race_register_inflight("OblC", rec2)
    assert d.sol_race_last["OblC"]["outcome"] == "won"
    assert s.tip_mult("contested") == pytest.approx(1.25 * 0.9)


def test_tuition_spendable_under_caps():
    d = _dash()
    assert d._sol_race_spendable(1_000_000) is True      # 0.001 SOL
    assert d._sol_race_spendable(2_500_000) is False     # 0.0025 > tip cap
    d.sol_race_tuition_spent = 0.0995
    assert d._sol_race_spendable(1_000_000) is False     # would exceed 0.10/day


def test_tuition_resets_next_day():
    d = _dash()
    d.sol_race_tuition_spent = 0.08
    d._sol_race_day = "2001-01-01"
    assert d._sol_race_tuition_left_sol() == 0.10
    assert d.sol_race_tuition_spent == 0.0


def test_enrich_rec_carries_position_features():
    d = _dash()
    rec = {"stage": "sent"}
    opp = {"hf": 0.9, "debt_usd": 800.0, "coll_sym": "BONK", "edge": True,
           "contested": True}
    plan = {"protocol_id": "kamino", "jito_tip_lamports": 123_456}
    out = d._sol_race_enrich_rec(rec, opp, plan)
    assert out["hf"] == 0.9
    assert out["debt_usd"] == 800.0
    assert out["coll_sym"] == "BONK"
    assert out["protocol_id"] == "kamino"
    assert out["contested"] is True
    assert out["tip_lamports"] == 123_456


def test_register_inflight_spends_tuition_and_is_confirmable(monkeypatch, tmp_path):
    d = _dash()
    monkeypatch.setattr(dash, "SOL_RACE_TIP_CAP_SOL", 0.002)
    rec = {"stage": "sent", "tip_lamports": 500_000,
           "expected_profit_usd": 3.0, "protocol_id": "solend", "hf": 0.9}
    d._sol_race_register_inflight("OblA", rec)
    assert "OblA" in d.sol_race_inflight
    assert round(d.sol_race_tuition_spent, 6) == 0.0005
    # lost by competitor
    d._sol_race_adjudicate("OblA", "lost")
    assert d.sol_race_last["OblA"]["outcome"] == "lost"
    assert "OblA" not in d.sol_race_inflight


def test_contested_window_expires_with_current_slot():
    pk = "3Kom4T8Z4Z6zRqgywrqcFL3uJbYjURD5SmzRkDiid7XX"
    s._COMPETITOR_OBL.clear()
    s._COMPETITOR_SLOT_WINDOW = 50
    s.record_competitor_liq(pk, 1000)
    assert s.is_contested_obligation(pk, current_slot=1005) is True
    assert s.is_contested_obligation(pk, current_slot=1100) is False
    # no current slot provided -> conservative True (legacy behavior)
    assert s.is_contested_obligation(pk) is True
    s._COMPETITOR_OBL.clear()


def test_date_roundtrip_uses_utc_day():
    d = _dash()
    day = d._sol_race_day_key()
    assert len(day) == 10  # YYYY-MM-DD


def test_flash_presign_passthrough_no_regression():
    assert hasattr(s, "presign_kamino_plan")
    assert hasattr(s, "presign_marginfi_plan")
    assert hasattr(dash, "SOL_RACE_MODE")