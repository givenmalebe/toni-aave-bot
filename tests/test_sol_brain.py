"""SOL twin brain: features, learning hooks, policy knobs."""
import json
import os

import numpy as np
import pytest

import profit_brain as pb


@pytest.fixture()
def sol_brain(tmp_path, monkeypatch):
    monkeypatch.setattr(pb, "SOL_STATE_PATH",
                        str(tmp_path / "sol_brain_test.json"))
    monkeypatch.setattr(pb, "_sol_brain", None)
    return pb.get_sol_brain()


def _state():
    return {
        "sol": {
            "sol_price_usd": 92.0,
            "opportunities": [{"profit_usd": 3.2}, {"profit_usd": 1.1}],
            "watchlist": [{"hf": 0.98}, {"hf": 1.05}],
            "mempool": {"count": 42},
            "intel": {"readiness": 80},
            "broadcast": {"edge_bias": True},
            "funds": {"bot": {"sol": 0.25}},
            "competitors": [{}, {}, {}],
        },
        "sol_fees": {
            "tip_floor": {"p50": 20000, "p95": 120000},
            "tip_floor_age_s": 30.0,
            "breaker": {"lost_sol": 0.01},
        },
        "feeds": {"sol": {"events_seen": 250}},
    }


def test_features_shape(sol_brain):
    x = pb.features_from_sol_state(_state())
    assert isinstance(x, np.ndarray)
    assert x.shape == (pb.SOL_FEAT_DIM,)
    assert np.all(np.isfinite(x))


def test_learn_sol_broadcast_increments_steps_and_persists(sol_brain):
    st = _state()
    n0 = sol_brain.steps
    pb.learn_sol_broadcast(st, {"stage": "sent", "profit_usd": 4.0})
    assert sol_brain.steps == n0 + 1
    assert os.path.exists(pb.SOL_STATE_PATH)
    d = json.load(open(pb.SOL_STATE_PATH))
    assert d["steps"] == n0 + 1


def test_skip_label_learns_negative(sol_brain):
    st = _state()
    before = sol_brain.steps
    pb.learn_sol_broadcast(st, {"stage": "skip", "why": "below floor"})
    assert sol_brain.steps == before + 1


def test_policy_warmup_then_knobs(sol_brain):
    st = _state()
    pol = pb.sol_policy(st)
    assert pol["model"] == pb.SOL_MODEL_NAME
    assert pol["advice"].startswith("warmup")
    assert pol["min_liq_mult"] == 1.0
    for _ in range(25):
        pb.learn_sol_broadcast(st, {"stage": "sent", "profit_usd": 6.0})
    pol = pb.sol_policy(st)
    assert 0.5 <= pol["min_liq_mult"] <= 1.6
    assert "act_prob" in pol and "exp_net_usd" in pol


def test_policy_roundtrip_persistence(sol_brain):
    st = _state()
    for _ in range(3):
        pb.learn_sol_broadcast(st, {"stage": "simulated", "profit_usd": 2.0})
    steps = sol_brain.steps
    reloaded = pb.load_sol()
    assert reloaded.steps == steps
