"""Integration tests for the hybrid execution engine."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from gas_bidder import GasBiddingEngine
from execution_tracker import ExecutionTracker
from alchemy_relay import AlchemyRelay
from backrun import BackrunEngine, CompetitorLanding
from hybrid_executor import HybridExecutor, ExecutionPhase


def make_full_stack(enabled=True):
    """Build a complete hybrid execution stack for testing."""
    gas_bidder = GasBiddingEngine(min_profit_usd=10)
    tracker = ExecutionTracker()
    relay = AsyncMock(spec=AlchemyRelay)
    backrun = BackrunEngine()
    executor = HybridExecutor(gas_bidder, backrun, relay, tracker, enabled=enabled)
    return executor


@pytest.mark.asyncio
async def test_full_flow_front_run_success():
    """Test complete flow: opportunity → bid → front-run → success."""
    executor = make_full_stack()

    # Execute
    result = await executor.execute(
        opportunity={"user": "0xabc", "net_usd": 100, "gas_limit": 1_500_000},
        current_block=100,
        current_gas_gwei=20,
        eth_usd=3000,
    )
    assert result["outcome"] == "front_run_submitted"
    assert executor.phase == ExecutionPhase.FRONT_RUN

    # Check result - our tx landed
    result2 = await executor.check_front_run_result(
        our_tx_hash="0xours",
        block_txs=[{"hash": "0xours"}],
        current_block=101,
    )
    assert result2["outcome"] == "success"
    assert executor.phase == ExecutionPhase.IDLE


@pytest.mark.asyncio
async def test_full_flow_front_run_fail_backrun():
    """Test complete flow: opportunity → bid → front-run fail → backrun."""
    executor = make_full_stack()

    # Execute
    result = await executor.execute(
        opportunity={"user": "0xabc", "net_usd": 100, "gas_limit": 1_500_000},
        current_block=100,
        current_gas_gwei=20,
        eth_usd=3000,
    )
    assert result["outcome"] == "front_run_submitted"

    # Check result - our tx didn't land, competitor did
    result2 = await executor.check_front_run_result(
        our_tx_hash="0xours",
        block_txs=[
            {"hash": "0xcomp", "input": "0xc2fa746c000000", "blockNumber": 101},
        ],
        current_block=101,
    )
    assert result2["outcome"] == "switching_to_backrun"
    assert executor.phase == ExecutionPhase.BACKRUN


@pytest.mark.asyncio
async def test_full_flow_skip_low_profit():
    """Test skip when profit too low."""
    executor = make_full_stack()

    result = await executor.execute(
        opportunity={"user": "0xabc", "net_usd": 5, "gas_limit": 1_500_000},
        current_block=100,
        current_gas_gwei=20,
        eth_usd=3000,
    )
    assert result["outcome"] == "skip"
    assert executor.phase == ExecutionPhase.IDLE


@pytest.mark.asyncio
async def test_full_flow_disabled():
    """Test disabled state."""
    executor = make_full_stack(enabled=False)

    result = await executor.execute(
        opportunity={"user": "0xabc", "net_usd": 100, "gas_limit": 1_500_000},
        current_block=100,
        current_gas_gwei=20,
        eth_usd=3000,
    )
    assert result["outcome"] == "disabled"
