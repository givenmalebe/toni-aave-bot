"""Hybrid executor — orchestrates three-phase execution strategy."""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from gas_bidder import GasBiddingEngine, GasBid
from backrun import BackrunEngine, BackrunOpportunity
from alchemy_relay import AlchemyRelay
from execution_tracker import ExecutionTracker, ExecutionAttempt

log = logging.getLogger("hybrid_executor")


class ExecutionPhase(Enum):
    IDLE = "idle"
    FRONT_RUN = "front_run"
    BACKRUN = "backrun"
    SKIP = "skip"


@dataclass
class ExecutionContext:
    opportunity: dict
    current_block: int
    signed_txs: list[str]
    tx_hash: str
    signer_address: str
    signature: str


class HybridExecutor:
    """Orchestrate three-phase execution: front-run → backrun → skip."""

    def __init__(
        self,
        gas_bidder: GasBiddingEngine,
        backrun_engine: BackrunEngine,
        relay: AlchemyRelay,
        tracker: ExecutionTracker,
        enabled: bool = True,
    ):
        self.gas_bidder = gas_bidder
        self.backrun_engine = backrun_engine
        self.relay = relay
        self.tracker = tracker
        self.enabled = enabled
        self._phase = ExecutionPhase.IDLE
        self._pending_context: Optional[ExecutionContext] = None

    async def execute(
        self,
        opportunity: dict,
        current_block: int,
        current_gas_gwei: float,
        eth_usd: float,
    ) -> dict:
        """Execute a liquidation opportunity with three-phase strategy."""
        if not self.enabled:
            return {"outcome": "disabled", "phase": "none"}
        
        opp_id = opportunity.get("user", "unknown")
        
        # Check if we should skip
        if self.tracker.should_skip(opp_id, current_block):
            log.info("Skipping %s (cooldown/paused)", opp_id)
            return {"outcome": "skip", "phase": "skip"}
        
        # Calculate gas bid
        bid = self.gas_bidder.calculate_bid(opportunity, current_gas_gwei, eth_usd)
        if bid is None:
            return {"outcome": "skip", "phase": "skip", "reason": "profit_too_low"}
        
        # Adapt bid based on history
        max_fee, priority_fee = self.tracker.get_adapted_bid(
            bid.max_fee_per_gas, bid.max_priority_fee_per_gas, opp_id
        )
        bid.max_fee_per_gas = max_fee
        bid.max_priority_fee_per_gas = priority_fee
        
        # Phase 1: Front-run
        self._phase = ExecutionPhase.FRONT_RUN
        log.info("Front-running %s with bid %.1f gwei", opp_id, bid.max_fee_per_gas)
        
        # Build context for backrun fallback
        self._pending_context = ExecutionContext(
            opportunity=opportunity,
            current_block=current_block,
            signed_txs=[],  # would be populated by live_liquidator
            tx_hash="",
            signer_address="",
            signature="",
        )
        
        return {
            "outcome": "front_run_submitted",
            "phase": "front_run",
            "bid": {
                "max_fee_per_gas": bid.max_fee_per_gas,
                "max_priority_fee_per_gas": bid.max_priority_fee_per_gas,
                "gas_limit": bid.gas_limit,
            },
        }

    async def check_front_run_result(
        self,
        our_tx_hash: str,
        block_txs: list[dict],
        current_block: int,
    ) -> dict:
        """Check if front-run succeeded, fallback to backrun if not."""
        if self._phase != ExecutionPhase.FRONT_RUN:
            return {"outcome": "not_in_front_run"}
        
        # Check if our tx landed
        our_landed = any(tx.get("hash") == our_tx_hash for tx in block_txs)
        
        if our_landed:
            # Success!
            self.tracker.log_attempt(ExecutionAttempt(
                timestamp=0,
                block_number=current_block,
                opportunity_id=self._pending_context.opportunity.get("user", ""),
                phase="front_run",
                gas_bid_max_fee=0,
                gas_bid_priority_fee=0,
                competitor_gas=0,
                outcome="success",
                profit_usd=0,
                gas_cost_usd=0,
            ))
            self._phase = ExecutionPhase.IDLE
            self._pending_context = None
            return {"outcome": "success", "phase": "front_run"}
        
        # Front-run failed, check if competitor landed
        competitor = self.backrun_engine.detect_competitor_tx(
            block_txs, our_tx_hash
        )
        
        if competitor:
            # Phase 2: Backrun
            self._phase = ExecutionPhase.BACKRUN
            log.info("Front-run failed, switching to backrun for %s", competitor.tx_hash)
            return {
                "outcome": "switching_to_backrun",
                "phase": "backrun",
                "competitor_tx": competitor.tx_hash,
            }
        
        # No competitor either, skip
        self.tracker.log_attempt(ExecutionAttempt(
            timestamp=0,
            block_number=current_block,
            opportunity_id=self._pending_context.opportunity.get("user", ""),
            phase="front_run",
            gas_bid_max_fee=0,
            gas_bid_priority_fee=0,
            competitor_gas=0,
            outcome="fail",
            profit_usd=0,
            gas_cost_usd=0,
        ))
        self._phase = ExecutionPhase.IDLE
        self._pending_context = None
        return {"outcome": "fail", "phase": "front_run"}

    @property
    def phase(self) -> ExecutionPhase:
        return self._phase
