"""ETH outbidding: 28-dim position brain, race-outcome ML, bid bandit."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import dashboard as dash
import gas_bidder as gb
import profit_brain as pb


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(pb, "STATE_PATH", str(tmp_path / "brain.json"))
    monkeypatch.setattr(pb, "ETH_RACE_OUTCOME_PATH",
                        str(tmp_path / "eth_race_outcomes.jsonl"))
    monkeypatch.setattr(gb, "ETH_BID_STATE_PATH", str(tmp_path / "bid.json"))
    pb._brain = None
    gb._ETH_BID = {"calm": 1.0, "contested": 1.0}
    yield
    pb._brain = None
    gb._ETH_BID = {"calm": 1.0, "contested": 1.0}


def _brain_saved():
    with open(pb.STATE_PATH) as f:
        import json
        return json.load(f)


def test_position_features_fill_indices_20_27():
    state = {"gas_gwei": 40.0, "eth_price_usd": 2000.0, "competitors_meta": {},
             "intel": {}, "mempool": {}, "broadcast": {}, "funds": {}}
    base = pb.features_from_state(state)
    assert base.shape == (pb.FEAT_DIM,)
    rec = {
        "debt_usd": 5000.0, "net_est_usd": 80.0, "gas_gwei": 120.0,
        "protocol_id": "spark", "edge": "long-tail", "contested": True,
        "prio_mult": 1.75, "missed_by_us": True,
    }
    x = pb.features_from_state(state, rec)
    assert x.shape == (pb.FEAT_DIM,)
    assert x[20] == pytest.approx(min(pb.math.log1p(5000.0) / 10.0, 2.0))
    assert x[21] == pytest.approx(min(80.0 / 50.0, 2.0))
    assert x[23] == pytest.approx(1.0)   # long-tail edge
    assert x[24] == pytest.approx(0.5)   # spark
    assert x[25] == pytest.approx(1.0)   # contested
    assert x[26] == pytest.approx(min(1.75 / 3.0, 1.0))
    assert x[27] == pytest.approx(1.0)   # involved/missed


def test_load_resets_on_stale_dimensions():
    pb.save(pb.OnlineDeepMLP(in_dim=20))
    path = pb.STATE_PATH
    with open(path) as f:
        import json
        stale = json.load(f)
    assert len(stale["W"][0]) == 20
    m = pb.load()
    assert m.in_dim == pb.FEAT_DIM
    assert m.steps == 0
    pb.save(m)
    d = _brain_saved()
    assert d["feat_version"] == pb.FEAT_VERSION
    assert len(d["W"][0]) == pb.FEAT_DIM


def test_learn_eth_race_trains_and_ledgers():
    rec = {"user": "0xabc", "debt_usd": 3000.0, "net_usd": 40.0,
           "protocol_id": "aave", "contested": True, "prio_mult": 1.25,
           "gas_gwei": 60.0}
    pb.learn_eth_race({"gas_gwei": 30.0, "eth_price_usd": 2000.0}, rec,
                      "won", profit=40.0)
    assert pb.get_brain().steps == 1
    assert os.path.exists(pb.ETH_RACE_OUTCOME_PATH)
    with open(pb.ETH_RACE_OUTCOME_PATH) as f:
        row = f.read().strip()
    import json
    row = json.loads(row)
    assert row["outcome"] == "won"
    assert row["profit"] == pytest.approx(40.0)
    pb.learn_eth_race({}, rec, "bogus")
    assert pb.get_brain().steps == 1  # unknown outcome is a no-op


def test_bandit_defaults_and_arms():
    assert gb.eth_bid_mult() == 1.0
    assert gb.eth_bid_mult(False) == 1.0
    assert gb.eth_bid_mult(True) == 1.0


def test_bandit_escalates_on_loss_decays_on_win():
    nxt = gb.update_eth_bid_mult(True, won=False)
    assert nxt == pytest.approx(1.25)
    assert gb.eth_bid_mult(True) == pytest.approx(1.25)
    assert gb.eth_bid_mult() == pytest.approx(1.0)
    nxt = gb.update_eth_bid_mult(True, won=True)
    assert nxt == pytest.approx(1.25 * 0.9)


def test_bandit_clamps_bounds():
    for _ in range(8):
        gb.update_eth_bid_mult(True, won=False)
    assert gb.eth_bid_mult(True) == pytest.approx(gb.ETH_BID_MAX)
    for _ in range(20):
        gb.update_eth_bid_mult(True, won=True)
    assert gb.eth_bid_mult(True) == pytest.approx(gb.ETH_BID_MIN)


def test_bandit_persists():
    gb.update_eth_bid_mult(True, won=False)
    gb._ETH_BID = {"calm": 1.0, "contested": 1.0}
    assert gb.eth_bid_mult(True) == pytest.approx(1.25)
    assert gb.eth_bid_mult() == pytest.approx(1.0)


def test_calculate_bid_scales_by_bandit():
    eng = gb.GasBiddingEngine()
    opp = {"net_usd": 200.0, "gas_limit": 1_500_000, "contested": True}
    gb.update_eth_bid_mult(True, won=False)  # contested mult -> 1.25
    b1 = eng.calculate_bid(opp, 40.0, 2000.0)
    assert b1 is not None
    opp2 = dict(opp)
    opp2["contested"] = False
    b2 = eng.calculate_bid(opp2, 40.0, 2000.0)
    assert b2 is not None
    assert b1.max_priority_fee_per_gas > b2.max_priority_fee_per_gas


def test_prio_mult_composition():
    import profit_engine as pe
    base = pe.race_prio_mult("mempool-contested")
    assert base == 1.4
    assert base * gb.eth_bid_mult(True) == pytest.approx(1.4)
    gb.update_eth_bid_mult(True, won=False)
    assert base * gb.eth_bid_mult(True) == pytest.approx(1.4 * 1.25)


def _dash():
    obj = object.__new__(dash.Dashboard)
    obj.eth_race_inflight = {}
    obj.eth_race_last = {}
    obj._eth_race_ttl_blocks = 60
    obj.state = {"sol": {"sol_price_usd": 100.0}}
    return obj


def test_eth_register_skips_sim_only():
    d = _dash()
    d._eth_race_register_inflight("0xAAA", 100, {"sim_only": True})
    assert d.eth_race_inflight == {}
    d._eth_race_register_inflight("0xAAA", 100, {"_sim_only": True})
    assert d.eth_race_inflight == {}
    d._eth_race_register_inflight("0xAAA", 100, {})
    assert "0xaaa" in d.eth_race_inflight


def test_eth_adjudicate_trains_bandit_and_brain():
    d = _dash()
    d._eth_race_register_inflight(
        "0x0B0B", 120,
        {"prio_mult": 1.4, "contested": True, "protocol": "aave",
         "net_usd": 50.0, "debt_usd": 2000.0, "gas_gwei": 50.0})
    d._eth_race_adjudicate("0x0B0B", "lost")
    assert d.eth_race_last["0x0b0b"]["outcome"] == "lost"
    assert "0x0b0b" not in d.eth_race_inflight
    assert gb.eth_bid_mult(True) == pytest.approx(1.25)
    assert pb.get_brain().steps == 1


def test_eth_race_reap_no_contest():
    d = _dash()
    d._eth_race_register_inflight(
        "0x0C0C", 100, {"prio_mult": 1.0, "contested": False,
                        "protocol": "aave", "net_usd": 10.0})
    d._eth_race_reap(100 + 61)
    assert d.eth_race_last["0x0c0c"]["outcome"] == "no-contest"
    assert gb.eth_bid_mult(False) == pytest.approx(1.0)  # no-contest: no bandit move