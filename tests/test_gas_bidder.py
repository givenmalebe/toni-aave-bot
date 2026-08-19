import pytest
from gas_bidder import GasBiddingEngine, CompetitorTx, GasBid


def test_empty_competitors_uses_current_gas():
    # Use high max_gas_cost_eth to avoid cap
    engine = GasBiddingEngine(max_gas_cost_eth=0.1)
    # net_usd=50 gives profit_multiplier=1.0, so max_fee = 20 * 1.2 * 1.0 = 24
    opp = {"net_usd": 50, "gas_limit": 1_500_000}
    bid = engine.calculate_bid(opp, current_gas_gwei=20, eth_usd=3000)
    assert bid is not None
    assert bid.max_fee_per_gas == pytest.approx(24.0, rel=0.01)


def test_competitor_data_increases_bid():
    engine = GasBiddingEngine(max_gas_cost_eth=0.1)
    engine.track_competitor(CompetitorTx(
        block_number=100,
        max_fee_per_gas=30.0,
        max_priority_fee_per_gas=3.0,
        success=True,
    ))
    # net_usd=50 gives profit_multiplier=1.0, so max_fee = 30 * 1.15 * 1.0 = 34.5
    opp = {"net_usd": 50, "gas_limit": 1_500_000}
    bid = engine.calculate_bid(opp, current_gas_gwei=20, eth_usd=3000)
    assert bid is not None
    assert bid.max_fee_per_gas == pytest.approx(34.5, rel=0.01)


def test_low_profit_returns_none():
    engine = GasBiddingEngine(min_profit_usd=10)
    opp = {"net_usd": 5, "gas_limit": 1_500_000}
    bid = engine.calculate_bid(opp, current_gas_gwei=20, eth_usd=3000)
    assert bid is None


def test_high_profit_scales_bid():
    engine = GasBiddingEngine(profit_scale_cap=3.0, max_gas_cost_eth=1.0)
    engine.track_competitor(CompetitorTx(
        block_number=100, max_fee_per_gas=30.0,
        max_priority_fee_per_gas=3.0, success=True,
    ))
    # net_usd=200 gives profit_multiplier=4.0, capped at 3.0
    # max_fee = 34.5 * 3.0 = 103.5
    opp = {"net_usd": 200, "gas_limit": 1_500_000}
    bid = engine.calculate_bid(opp, current_gas_gwei=20, eth_usd=3000)
    assert bid is not None
    assert bid.max_fee_per_gas == pytest.approx(103.5, rel=0.01)


def test_gas_cost_cap_enforced():
    # Force a high bid that exceeds cap
    engine = GasBiddingEngine(max_gas_cost_eth=0.001)
    engine.track_competitor(CompetitorTx(
        block_number=100, max_fee_per_gas=100.0,
        max_priority_fee_per_gas=10.0, success=True,
    ))
    opp = {"net_usd": 500, "gas_limit": 1_500_000}
    bid = engine.calculate_bid(opp, current_gas_gwei=20, eth_usd=3000)
    assert bid is not None
    # Gas cost should not exceed 0.001 ETH
    gas_cost_eth = bid.max_fee_per_gas * bid.gas_limit * 1e-9
    assert gas_cost_eth <= 0.001


def test_competitor_p95():
    engine = GasBiddingEngine()
    for i in range(20):
        engine.track_competitor(CompetitorTx(
            block_number=i, max_fee_per_gas=float(i),
            max_priority_fee_per_gas=1.0, success=True,
        ))
    p95 = engine.get_competitor_p95()
    # 95th percentile of 0..19: int(20 * 0.95) = 19, prices[19] = 19.0
    assert p95 == pytest.approx(19.0, rel=0.01)
