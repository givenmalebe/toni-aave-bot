import pytest
from backrun import BackrunEngine, CompetitorLanding


def test_detect_competitor_liquidation():
    engine = BackrunEngine()
    txs = [
        {"hash": "0xcomp", "input": "0xc2fa746c000000", "blockNumber": 100},
        {"hash": "0xother", "input": "0x12345678", "blockNumber": 100},
    ]
    result = engine.detect_competitor_tx(txs, our_tx_hash="0xours")
    assert result is not None
    assert result.tx_hash == "0xcomp"


def test_skip_own_tx():
    engine = BackrunEngine()
    txs = [
        {"hash": "0xours", "input": "0xc2fa746c000000", "blockNumber": 100},
    ]
    result = engine.detect_competitor_tx(txs, our_tx_hash="0xours")
    assert result is None


def test_no_competitor():
    engine = BackrunEngine()
    txs = [
        {"hash": "0xother", "input": "0x12345678", "blockNumber": 100},
    ]
    result = engine.detect_competitor_tx(txs, our_tx_hash="0xours")
    assert result is None


def test_build_backrun_minimum_profit():
    engine = BackrunEngine()
    landing = CompetitorLanding(
        tx_hash="0xcomp", block_number=100,
        user="0xuser", protocol="aave", profit_usd=50,
    )
    result = engine.build_backrun(landing, price_impact=0.02, estimated_profit_usd=3)
    assert result is None  # below $5 minimum


def test_build_backrun_viable():
    engine = BackrunEngine()
    landing = CompetitorLanding(
        tx_hash="0xcomp", block_number=100,
        user="0xuser", protocol="aave", profit_usd=50,
    )
    result = engine.build_backrun(landing, price_impact=0.02, estimated_profit_usd=15)
    assert result is not None
    assert result.competitor_tx == "0xcomp"
