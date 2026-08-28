"""MarginFi plan builder and HF scoring."""
from sol_lending import marginfi
from sol_scanner import build_marginfi_liq_plan


def _sample_banks():
    return {
        "AssetBank": {
            "address": "AssetBank",
            "mint": "So11111111111111111111111111111111111111112",
            "symbol": "SOL",
            "decimals": 9,
            "asset_share_value": 1.0,
            "liability_share_value": 1.0,
            "asset_weight_maint": 0.8,
            "liability_weight_maint": 1.2,
            "oracle_setup": 3,
            "oracle_keys": ["OracleA"],
            "asset_tag": 0,
        },
        "LiabBank": {
            "address": "LiabBank",
            "mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
            "symbol": "USDC",
            "decimals": 6,
            "asset_share_value": 1.0,
            "liability_share_value": 1.0,
            "asset_weight_maint": 0.9,
            "liability_weight_maint": 1.1,
            "oracle_setup": 3,
            "oracle_keys": ["OracleU"],
            "asset_tag": 0,
        },
    }


def test_score_requires_bank_weights():
    opp = {
        "obligation": "Acc1",
        "hf": 0.92,
        "debt_usd": 500.0,
        "coll_usd": 600.0,
        "hf_method": "bank_oracle_weights",
        "coll_mint": "So11111111111111111111111111111111111111112",
        "debt_mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    }
    scored = marginfi.score_profit(opp, sol_px=100.0)
    assert scored["actionable"] is True
    assert scored["net_usd"] > 0


def test_proxy_hf_not_actionable():
    opp = {"obligation": "Acc2", "hf": 0.8, "debt_usd": 100.0,
           "hf_method": "share_ratio"}
    scored = marginfi.score_profit(opp, sol_px=100.0)
    assert scored["actionable"] is False


def test_build_plan_ready():
    opp = {
        "obligation": "Acc3",
        "hf": 0.88,
        "debt_usd": 200.0,
        "asset_bank": "AssetBank",
        "liab_bank": "LiabBank",
        "hf_method": "bank_oracle_weights",
    }
    plan = marginfi.build_plan(opp)
    assert plan["ready"] is True
    assert plan["execute"] == "marginfi-jito"
    assert plan["asset_bank"] == "AssetBank"


def test_scanner_plan_wires_jito():
    opp = {
        "obligation": "Acc4",
        "hf": 0.85,
        "debt_usd": 300.0,
        "asset_bank": "A",
        "liab_bank": "L",
        "hf_method": "bank_oracle_weights",
    }
    plan = build_marginfi_liq_plan(opp)
    assert plan["protocol_id"] == "marginfi"
    assert plan["program"] == marginfi.MARGINFI_PROGRAM
    assert plan["jito_tip_lamports"] >= 0


def test_remaining_accounts_sorted():
    banks = _sample_banks()
    bals = [
        {"bank": "LiabBank", "asset_shares": 0, "liability_shares": 10},
        {"bank": "AssetBank", "asset_shares": 5, "liability_shares": 0},
    ]
    metas = marginfi.remaining_accounts_for_balances(bals, banks)
    banks_only = [m[0] for m in metas if m[2]]
    assert banks_only == sorted(banks_only)
