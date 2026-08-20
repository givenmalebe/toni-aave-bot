import pytest
from unittest.mock import AsyncMock, MagicMock
from hybrid_executor import HybridExecutor, ExecutionPhase
from gas_bidder import GasBiddingEngine, GasBid
from backrun import BackrunEngine
from alchemy_relay import AlchemyRelay
from execution_tracker import ExecutionTracker


def make_executor(enabled=True):
    gas_bidder = GasBiddingEngine(min_profit_usd=10)
    backrun = BackrunEngine()
    relay = AsyncMock(spec=AlchemyRelay)
    relay.send_bundle = AsyncMock(return_value={"success": True, "result": "0xtxhash"})
    tracker = ExecutionTracker()
    return HybridExecutor(gas_bidder, backrun, relay, tracker, enabled=enabled)


@pytest.mark.asyncio
async def test_execute_disabled():
    executor = make_executor(enabled=False)
    result = await executor.execute(
        opportunity={"user": "0xabc", "net_usd": 100, "gas_limit": 1_500_000},
        current_block=100,
        current_gas_gwei=20,
        eth_usd=3000,
    )
    assert result["outcome"] == "disabled"


@pytest.mark.asyncio
async def test_execute_skip_low_profit():
    executor = make_executor()
    result = await executor.execute(
        opportunity={"user": "0xabc", "net_usd": 5, "gas_limit": 1_500_000},
        current_block=100,
        current_gas_gwei=20,
        eth_usd=3000,
    )
    assert result["outcome"] == "skip"


@pytest.mark.asyncio
async def test_execute_front_run_no_signed_tx():
    executor = make_executor()
    result = await executor.execute(
        opportunity={"user": "0xabc", "net_usd": 100, "gas_limit": 1_500_000},
        current_block=100,
        current_gas_gwei=20,
        eth_usd=3000,
    )
    assert result["outcome"] == "bid_computed"
    assert executor.phase == ExecutionPhase.FRONT_RUN
    assert "bid" in result


@pytest.mark.asyncio
async def test_execute_front_run_with_signed_tx():
    executor = make_executor()
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
    executor.relay.send_bundle.assert_called_once()


@pytest.mark.asyncio
async def test_execute_relay_failure():
    executor = make_executor()
    executor.relay.send_bundle = AsyncMock(return_value={"success": False, "error": "timeout"})
    result = await executor.execute(
        opportunity={"user": "0xabc", "net_usd": 100, "gas_limit": 1_500_000},
        current_block=100,
        current_gas_gwei=20,
        eth_usd=3000,
        signed_txs=["0xsigned123"],
        signer_address="0xsigner",
        signature="0xsig",
    )
    assert result["outcome"] == "relay_failed"
    assert executor.phase == ExecutionPhase.IDLE


@pytest.mark.asyncio
async def test_submit_tx():
    executor = make_executor()
    # First execute to set up pending context
    await executor.execute(
        opportunity={"user": "0xabc", "net_usd": 100, "gas_limit": 1_500_000},
        current_block=100,
        current_gas_gwei=20,
        eth_usd=3000,
    )
    # Then submit tx
    result = await executor.submit_tx(
        signed_txs=["0xsigned123"],
        block_number=101,
        signer_address="0xsigner",
        signature="0xsig",
    )
    assert result["outcome"] == "submitted"
    executor.relay.send_bundle.assert_called_once()


@pytest.mark.asyncio
async def test_check_front_run_success():
    executor = make_executor()
    await executor.execute(
        opportunity={"user": "0xabc", "net_usd": 100, "gas_limit": 1_500_000},
        current_block=100,
        current_gas_gwei=20,
        eth_usd=3000,
    )
    result = await executor.check_front_run_result(
        our_tx_hash="0xours",
        block_txs=[{"hash": "0xours"}],
        current_block=101,
    )
    assert result["outcome"] == "success"
    assert executor.phase == ExecutionPhase.IDLE
