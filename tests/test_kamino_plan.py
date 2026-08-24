"""Kamino sweep plan builder — ready=True for HF<1 obligations."""
from sol_lending import kamino
from sol_scanner import build_kamino_liq_plan


def test_build_plan_ready_when_hf_below_one():
    opp = {
        "obligation": "OblPk",
        "hf": 0.91,
        "debt_usd": 1000.0,
        "borrowed_usd": 1000.0,
        "borrow_reserves": ["DebtRes"],
        "deposit_reserves": ["CollRes"],
        "protocol_id": "kamino",
    }
    plan = kamino.build_plan(opp)
    assert plan["ready"] is True
    assert plan["execute"] == "kamino-jito"
    assert plan["debt_reserve"] == "DebtRes"
    assert plan["coll_reserve"] == "CollRes"
    assert plan["close_factor"] == 0.20
    assert plan["net_usd"] is not None
    assert plan["net_usd"] > 0


def test_scanner_plan_matches_live_sender_keys():
    opp = {"obligation": "OblPk", "hf": 0.85, "debt_usd": 500.0,
           "borrow_reserves": ["R1"], "deposit_reserves": ["C1"]}
    plan = build_kamino_liq_plan(opp)
    assert plan["protocol_id"] == "kamino"
    assert plan["ready"] is True
    assert plan["program"]
    assert plan["jito_tip_lamports"] >= 0


def test_healthy_not_ready():
    plan = kamino.build_plan({"obligation": "OblPk", "hf": 1.2, "debt_usd": 10})
    assert plan["ready"] is False
