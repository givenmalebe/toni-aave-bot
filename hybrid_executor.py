"""Hybrid executor — orchestrates three-phase execution strategy."""

import logging
import time
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
    bid: Optional[GasBid] = None


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
        signed_txs: list[str] = None,
        signer_address: str = "",
        signature: str = "",
    ) -> dict:
        """Execute a liquidation opportunity with three-phase strategy.
        
        If signed_txs is provided, submits via relay. Otherwise returns bid
        for the caller to sign and resubmit via submit_tx().
        """
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
        
        # Build context for backrun fallback
        self._pending_context = ExecutionContext(
            opportunity=opportunity,
            current_block=current_block,
            signed_txs=signed_txs or [],
            tx_hash="",
            signer_address=signer_address,
            signature=signature,
            bid=bid,
        )
        
        # If signed txs provided, submit via relay
        if signed_txs and signer_address and signature:
            log.info("Front-running %s with bid %.1f gwei via relay",
                     opp_id, bid.max_fee_per_gas)
            result = await self.relay.send_bundle(
                signed_txs=signed_txs,
                block_number=current_block + 1,
                signer_address=signer_address,
                signature=signature,
            )
            if result.get("success"):
                self._pending_context.tx_hash = result.get("result", "")
                return {
                    "outcome": "front_run_submitted",
                    "phase": "front_run",
                    "tx_hash": self._pending_context.tx_hash,
                    "bid": {
                        "max_fee_per_gas": bid.max_fee_per_gas,
                        "max_priority_fee_per_gas": bid.max_priority_fee_per_gas,
                        "gas_limit": bid.gas_limit,
                    },
                }
            else:
                log.warning("Relay submission failed: %s", result.get("error"))
                self._phase = ExecutionPhase.IDLE
                self._pending_context = None
                return {
                    "outcome": "relay_failed",
                    "phase": "front_run",
                    "error": result.get("error"),
                }
        
        # No signed txs — return bid for caller to sign
        log.info("Front-running %s — bid computed, awaiting signed txs", opp_id)
        return {
            "outcome": "bid_computed",
            "phase": "front_run",
            "bid": {
                "max_fee_per_gas": bid.max_fee_per_gas,
                "max_priority_fee_per_gas": bid.max_priority_fee_per_gas,
                "gas_limit": bid.gas_limit,
            },
        }

    async def submit_tx(
        self,
        signed_txs: list[str],
        block_number: int,
        signer_address: str,
        signature: str,
    ) -> dict:
        """Submit a pre-signed transaction bundle via relay."""
        if not self._pending_context:
            return {"outcome": "no_pending_context"}
        
        self._pending_context.signed_txs = signed_txs
        self._pending_context.signer_address = signer_address
        self._pending_context.signature = signature
        
        result = await self.relay.send_bundle(
            signed_txs=signed_txs,
            block_number=block_number,
            signer_address=signer_address,
            signature=signature,
        )
        
        if result.get("success"):
            self._pending_context.tx_hash = result.get("result", "")
            log.info("Bundle submitted: %s", self._pending_context.tx_hash)
            return {
                "outcome": "submitted",
                "tx_hash": self._pending_context.tx_hash,
            }
        else:
            log.warning("Bundle submission failed: %s", result.get("error"))
            return {"outcome": "failed", "error": result.get("error")}

    async def check_front_run_result(
        self,
        our_tx_hash: str,
        block_txs: list[dict],
        current_block: int,
    ) -> dict:
        """Check if front-run succeeded, fallback to backrun if not."""
        if self._phase != ExecutionPhase.FRONT_RUN:
            return {"outcome": "not_in_front_run"}
        
        ctx = self._pending_context
        if not ctx:
            return {"outcome": "no_pending_context"}
        
        # Check if our tx landed
        our_landed = any(tx.get("hash") == our_tx_hash for tx in block_txs)
        
        if our_landed:
            # Success!
            profit_usd = ctx.opportunity.get("net_usd", 0)
            self.tracker.log_attempt(ExecutionAttempt(
                timestamp=int(time.time()),
                block_number=current_block,
                opportunity_id=ctx.opportunity.get("user", ""),
                phase="front_run",
                gas_bid_max_fee=ctx.bid.max_fee_per_gas if ctx.bid else 0,
                gas_bid_priority_fee=ctx.bid.max_priority_fee_per_gas if ctx.bid else 0,
                competitor_gas=0,
                outcome="success",
                profit_usd=profit_usd,
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
        
        # No competitor either, log failure and skip
        self.tracker.log_attempt(ExecutionAttempt(
            timestamp=int(time.time()),
            block_number=current_block,
            opportunity_id=ctx.opportunity.get("user", ""),
            phase="front_run",
            gas_bid_max_fee=ctx.bid.max_fee_per_gas if ctx.bid else 0,
            gas_bid_priority_fee=ctx.bid.max_priority_fee_per_gas if ctx.bid else 0,
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
