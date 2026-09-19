"""liq_amount_usd: USD principal liquidated = debt repaid, coll fallback."""
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


@pytest.fixture(autouse=True)
def fake_prices(monkeypatch):
    def fake_decimals(addr):
        return {USDC: 6, USDT: 6, WETH: 18}.get((addr or "").lower(), 18)

    def fake_price(addr):
        return {
            USDC: 1.0, USDT: 1.0, WETH: 3500.0,
        }.get((addr or "").lower(), 0.0)

    monkeypatch.setattr(lb, "token_decimals", fake_decimals)
    monkeypatch.setattr(lb, "asset_price_usd", fake_price)


def test_debt_priced_primary_basis():
    ev = {"debt_addr": USDC, "debt_to_cover": int(100_000_000)}
    amt, basis = lb.liq_amount_usd(ev)
    assert amt == 100.0
    assert basis == "debt"


def test_debt_unpriced_falls_back_to_collateral():
    ev = {"debt_sym": "ZZZZ", "debt_to_cover": 123,
          "coll_sym": "USDC", "coll_seized": int(105 * 1e6)}
    amt, basis = lb.liq_amount_usd(ev)
    assert amt == 105.0
    assert basis == "coll"


def test_zero_debt_falls_back_to_collateral():
    ev = {"debt_addr": USDC, "debt_to_cover": 0,
          "coll_addr": USDC, "coll_seized": int(105 * 1e6)}
    amt, basis = lb.liq_amount_usd(ev)
    assert amt == 105.0
    assert basis == "coll"


def test_weth_debt_priced():
    ev = {"debt_addr": WETH, "debt_to_cover": int(1 * 1e18)}
    amt, basis = lb.liq_amount_usd(ev)
    assert amt == 3500.0
    assert basis == "debt"


def test_nothing_priceable_returns_none():
    ev = {"coll_sym": "ZZZZ", "debt_sym": "XYZ9",
          "coll_to_liq": 123, "debt_to_cover": 456}
    assert lb.liq_amount_usd(ev) == (None, None)


def test_sub_cent_dust_returns_none():
    ev = {"debt_addr": USDC, "debt_to_cover": 4000}
    assert lb.liq_amount_usd(ev) == (None, None)