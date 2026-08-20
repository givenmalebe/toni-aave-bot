import pytest
from execution_tracker import ExecutionTracker, ExecutionAttempt


def make_attempt(opportunity_id="0xabc", block=100, outcome="fail"):
    return ExecutionAttempt(
        timestamp=1000.0,
        block_number=block,
        opportunity_id=opportunity_id,
        phase="front_run",
        gas_bid_max_fee=30.0,
        gas_bid_priority_fee=3.0,
        competitor_gas=25.0,
        outcome=outcome,
        profit_usd=50.0,
        gas_cost_usd=5.0,
    )


def test_should_skip_after_failure():
    tracker = ExecutionTracker(skip_cooldown_blocks=3)
    tracker.log_attempt(make_attempt(block=100, outcome="fail"))
    assert tracker.should_skip("0xabc", current_block=101) is True
    assert tracker.should_skip("0xabc", current_block=103) is False


def test_should_not_skip_different_opportunity():
    tracker = ExecutionTracker(skip_cooldown_blocks=3)
    tracker.log_attempt(make_attempt(opportunity_id="0xabc", block=100, outcome="fail"))
    assert tracker.should_skip("0xdef", current_block=101) is False


def test_bid_adaptation_after_failures():
    tracker = ExecutionTracker(bid_increase_factor=1.25)
    # 2 failures should increase bid
    tracker.log_attempt(make_attempt(block=100, outcome="fail"))
    tracker.log_attempt(make_attempt(block=101, outcome="fail"))
    max_fee, priority_fee = tracker.get_adapted_bid(30.0, 3.0, "0xabc")
    # 2 fails: multiplier = 1.25^(2-1) = 1.25
    assert max_fee == pytest.approx(37.5, rel=0.01)


def test_bid_no_adaptation_on_success():
    tracker = ExecutionTracker()
    tracker.log_attempt(make_attempt(block=100, outcome="success"))
    max_fee, priority_fee = tracker.get_adapted_bid(30.0, 3.0, "0xabc")
    assert max_fee == 30.0


def test_pause_after_threshold():
    tracker = ExecutionTracker(pause_threshold=3, pause_blocks=10)
    for i in range(3):
        tracker.log_attempt(make_attempt(block=100 + i, outcome="fail"))
    assert tracker.should_skip("0xabc", current_block=105) is True
    assert tracker.should_skip("0xabc", current_block=112) is False


def test_stats():
    tracker = ExecutionTracker()
    tracker.log_attempt(make_attempt(outcome="success"))
    tracker.log_attempt(make_attempt(outcome="fail"))
    tracker.log_attempt(make_attempt(outcome="skip"))
    stats = tracker.get_stats()
    assert stats["total"] == 3
    assert stats["success_rate"] == pytest.approx(1 / 3, rel=0.01)
