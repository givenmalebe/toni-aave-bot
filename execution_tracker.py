"""Execution tracker — logs attempts, outcomes, adapts bids."""

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field, asdict
from typing import Optional

log = logging.getLogger("execution_tracker")

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
EXECUTION_LOG = os.path.join(DATA_DIR, "execution_log.jsonl")
MAX_ATTEMPTS = 500


@dataclass
class ExecutionAttempt:
    timestamp: float
    block_number: int
    opportunity_id: str
    phase: str  # "front_run" | "backrun" | "skip"
    gas_bid_max_fee: float
    gas_bid_priority_fee: float
    competitor_gas: float
    outcome: str  # "success" | "fail" | "skip"
    profit_usd: float
    gas_cost_usd: float


class ExecutionTracker:
    """Track execution attempts and adapt bidding strategy."""

    def __init__(
        self,
        skip_cooldown_blocks: int = 3,
        bid_increase_factor: float = 1.25,
        pause_threshold: int = 5,
        pause_blocks: int = 10,
    ):
        self.skip_cooldown_blocks = skip_cooldown_blocks
        self.bid_increase_factor = bid_increase_factor
        self.pause_threshold = pause_threshold
        self.pause_blocks = pause_blocks
        self._attempts: list[ExecutionAttempt] = []
        self._consecutive_fails: dict[str, int] = {}  # opportunity_id -> count
        self._paused_until: dict[str, int] = {}  # opportunity_id -> block_number
        self._lock = threading.Lock()

    def log_attempt(self, attempt: ExecutionAttempt) -> None:
        """Log an execution attempt."""
        with self._lock:
            self._attempts.append(attempt)
            if len(self._attempts) > MAX_ATTEMPTS:
                self._attempts = self._attempts[-MAX_ATTEMPTS:]

        # Update consecutive fails
        oid = attempt.opportunity_id
        if attempt.outcome == "fail":
            self._consecutive_fails[oid] = self._consecutive_fails.get(oid, 0) + 1
        else:
            self._consecutive_fails[oid] = 0

        # Check if we should pause
        if self._consecutive_fails.get(oid, 0) >= self.pause_threshold:
            self._paused_until[oid] = attempt.block_number + self.pause_blocks
            log.warning("Pausing opportunity %s until block %d", oid, self._paused_until[oid])

        # Persist to file
        self._persist(attempt)

    def should_skip(self, opportunity_id: str, current_block: int) -> bool:
        """Check if we should skip this opportunity (cooldown or paused)."""
        # Check pause
        if opportunity_id in self._paused_until:
            if current_block < self._paused_until[opportunity_id]:
                return True
            else:
                del self._paused_until[opportunity_id]

        # Check recent failures
        with self._lock:
            recent_fails = sum(
                1 for a in reversed(self._attempts[-100:])
                if a.opportunity_id == opportunity_id
                and a.outcome == "fail"
                and current_block - a.block_number < self.skip_cooldown_blocks
            )
        return recent_fails >= 1

    def get_adapted_bid(
        self,
        base_bid_max_fee: float,
        base_bid_priority_fee: float,
        opportunity_id: str,
    ) -> tuple[float, float]:
        """Adapt bid based on consecutive failures."""
        with self._lock:
            fails = self._consecutive_fails.get(opportunity_id, 0)
        if fails >= 2:
            # Increase bid after 2+ failures
            multiplier = self.bid_increase_factor ** (fails - 1)
            return (base_bid_max_fee * multiplier, base_bid_priority_fee * multiplier)
        return (base_bid_max_fee, base_bid_priority_fee)

    def get_stats(self, opportunity_id: Optional[str] = None) -> dict:
        """Get execution statistics."""
        with self._lock:
            attempts = list(self._attempts)
        if opportunity_id:
            attempts = [a for a in attempts if a.opportunity_id == opportunity_id]

        total = len(attempts)
        successes = sum(1 for a in attempts if a.outcome == "success")
        fails = sum(1 for a in attempts if a.outcome == "fail")
        skips = sum(1 for a in attempts if a.outcome == "skip")

        return {
            "total": total,
            "successes": successes,
            "fails": fails,
            "skips": skips,
            "success_rate": successes / total if total else 0,
        }

    def _persist(self, attempt: ExecutionAttempt) -> None:
        """Persist attempt to JSONL file."""
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(EXECUTION_LOG, "a") as f:
            f.write(json.dumps(asdict(attempt)) + "\n")
