"""liq_event_profit: token, reserve-id, and debt-side fallback estimation."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "aave-v4-liquidation-bot"))

import pytest

import liquidation_bot as lb

USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
USDT = "0xdac17f958d2ee523a2206206994597c13d831ec7"
WETH = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
DAI = "0x6b175474e89094c44da98b954eedeac495271d0f"
LINK = "0x514910771af9ca656af840dff83e8264ecf986ca"


@pytest.fixture(autouse=True)
def fake_prices(monkeypatch):
    def fake_decimals(addr):
        return {USDC: 6, USDT: 6, DAI: 18, WETH: 18, LINK: 18}.get(
            (addr or "").lower(), 18)

    def fake_price(addr):
        p = {
            USDC: 1.0, USDT: 1.0, DAI: 1.0,
            WETH: 3500.0, LINK: 12.0,
        }
        return p.get((addr or "").lower(), 0.0)

    monkeypatch.setattr(lb, "token_decimals", fake_decimals)
    monkeypatch.setattr(lb, "asset_price_usd", fake_price)


def test_v3_both_sides_exact_bonus():
    ev = {"protocol": "v3", "coll_addr": USDC, "debt_addr": USDC,
          "coll_seized": int(105_000_000), "debt_to_cover": int(100_000_000)}
    est, coll = lb.liq_event_profit(ev)
    assert est == 5.0
    assert coll == 105.0


def test_v3_negative_bonus_is_decode_miss():
    ev = {"protocol": "v3", "coll_addr": WETH, "debt_addr": USDC,
          "coll_seized": int(100 * 1e18), "debt_to_cover": int(400_000 * 1e6)}
    assert lb.liq_event_profit(ev) == (None, None)


def test_reserve_id_path_debt_sym_fallback():
    ev = {"protocol": "v4", "coll_rid": 9, "debt_rid": 3,
          "coll_sym": "DDE3EC", "debt_sym": "USDC",
          "coll_seized": 105_000_000, "debt_to_cover": 100_000_000}
    est, coll = lb.liq_event_profit(ev)
    assert est == 5.0
    assert abs(coll - 105.0) < 1e-9


def test_morpho_mesh_usdt_debt_estimated():
    ev = {"debt_sym": "USDT", "coll_sym": "928FB6",
          "debt_to_cover": 25000000, "coll_to_liq": 27000000}
    est, _ = lb.liq_event_profit(ev)
    assert est == 1.25


def test_weth_debt_priced_at_eth_price():
    ev = {"debt_sym": "WETH", "debt_to_cover": int(1 * 1e18)}
    est, coll = lb.liq_event_profit(ev)
    assert est == 175.0
    assert abs(coll - 3675.0) < 1e-9


def test_collateral_priced_debt_unpriced():
    ev = {"coll_sym": "USDC", "debt_sym": "DDE3EC",
          "coll_seized": int(105 * 1e6), "debt_to_cover": 0}
    est, coll = lb.liq_event_profit(ev)
    assert abs(est - 5.0) < 0.01
    assert abs(coll - 105.0) < 1e-9


def test_nothing_priceable_returns_none():
    ev = {"coll_sym": "ZZZZ", "debt_sym": "XYZ9",
          "coll_to_liq": 123, "debt_to_cover": 456}
    assert lb.liq_event_profit(ev) == (None, None)


def test_zero_amounts_return_none():
    ev = {"debt_sym": "USDC", "coll_sym": "DDE3EC",
          "coll_seized": 0, "debt_to_cover": 0}
    assert lb.liq_event_profit(ev) == (None, None)