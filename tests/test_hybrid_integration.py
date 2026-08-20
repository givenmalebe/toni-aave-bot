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
    relay.send_bundle = AsyncMock(return_value={"success": True, "result": "0xtxhash"})
    backrun = BackrunEngine()
    executor = HybridExecutor(gas_bidder, backrun, relay, tracker, enabled=enabled)
    return executor


@pytest.mark.asyncio
async def test_full_flow_front_run_success():
    """Test complete flow: opportunity → bid → submit via relay → success."""
    executor = make_full_stack()

    # Execute with signed txs
    result = await executor.execute(
        opportunity={"user": "0xabc", "net_usd": 100, "gas_limit": 1_500_000},
        current_block=100,
        current_gas_gwei=20,
        eth_usd=3000,
        signed_txs=["0xsigned123"],
        signer_address="0xsigner",
        signature="0xsig",
    )
    assert result["outcome"] == "front_run_submitted"
    assert executor.phase == ExecutionPhase.FRONT_RUN

    # Check result - our tx landed
    result2 = await executor.check_front_run_result(
        our_tx_hash="0xtxhash",
        block_txs=[{"hash": "0xtxhash"}],
        current_block=101,
    )
    assert result2["outcome"] == "success"
    assert executor.phase == ExecutionPhase.IDLE


@pytest.mark.asyncio
async def test_full_flow_bid_computed_then_submit():
    """Test: bid computed → submit_tx → front-run → backrun fallback."""
    executor = make_full_stack()

    # Phase 1: Compute bid (no signed txs)
    result = await executor.execute(
        opportunity={"user": "0xabc", "net_usd": 100, "gas_limit": 1_500_000},
        current_block=100,
        current_gas_gwei=20,
        eth_usd=3000,
    )
    assert result["outcome"] == "bid_computed"
    assert executor.phase == ExecutionPhase.FRONT_RUN

    # Phase 2: Submit signed txs
    submit_result = await executor.submit_tx(
        signed_txs=["0xsigned123"],
        block_number=101,
        signer_address="0xsigner",
        signature="0xsig",
    )
    assert submit_result["outcome"] == "submitted"

    # Phase 3: Check result - competitor landed
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
