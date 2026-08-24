"""Hybrid executor — orchestrates three-phase execution strategy."""

import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from gas_bidder import GasBiddingEngine, GasBid
from backrun import BackrunEngine, BackrunOpportunity, CompetitorLanding
from alchemy_relay import AlchemyRelay
from execution_tracker import ExecutionTracker, ExecutionAttempt
from nonce_manager import get_nonce_manager

try:
    sys.path.insert(
        0, os.path.join(os.path.dirname(__file__), "aave-v4-liquidation-bot")
    )
    import stealth as _stealth_mod
except ImportError:
    _stealth_mod = None

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
        stealth_module=None,
    ):
        self.gas_bidder = gas_bidder
        self.backrun_engine = backrun_engine
        self.relay = relay
        self.tracker = tracker
        self.enabled = enabled
        self.stealth = stealth_module if stealth_module is not None else _stealth_mod
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
        
        # If signed txs provided, submit via relay (prefer stealth 7-builder spray)
        if signed_txs and signer_address and signature:
            log.info("Front-running %s with bid %.1f gwei via relay",
                     opp_id, bid.max_fee_per_gas)
            result = await self._send_via_stealth(
                signed_txs, current_block, signer_address, signature
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
        
        result = await self._send_via_stealth(
            signed_txs, block_number, signer_address, signature
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

    async def _send_via_stealth(
        self,
        signed_txs: list[str],
        block_number: int,
        signer_address: str,
        signature: str,
    ) -> dict:
        """Try 7-builder spray via stealth module; fall back to AlchemyRelay."""
        target_block = block_number + 1
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_sendBundle",
            "params": [
                {
                    "txs": [
                        t if t.startswith("0x") else "0x" + t for t in signed_txs
                    ],
                    "blockNumber": hex(target_block),
                    "minTimestamp": 0,
                    "maxTimestamp": 0,
                }
            ],
        }
        auth_header = f"{signer_address}:{signature}"

        # Try stealth 7-builder spray first
        if self.stealth is not None:
            try:
                import concurrent.futures

                loop = __import__("asyncio").get_event_loop()
                result = await loop.run_in_executor(
                    None,
                    lambda: self.stealth.send_bundle(
                        body, auth_header=auth_header
                    ),
                )
                if result.get("n_ok", 0) > 0:
                    log.info(
                        "Stealth spray to %d builders succeeded",
                        result["n_ok"],
                    )
                    return {
                        "success": True,
                        "result": result.get("stage", "sent"),
                        "relay": "stealth",
                        "n_ok": result["n_ok"],
                    }
                log.warning(
                    "Stealth spray failed (n_ok=0), falling back to relay"
                )
            except Exception as exc:
                log.warning("Stealth spray error: %s, falling back to relay", exc)

        # Fallback: AlchemyRelay (Flashbots only)
        return await self.relay.send_bundle(
            signed_txs=signed_txs,
            block_number=target_block,
            signer_address=signer_address,
            signature=signature,
        )

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

    async def execute_backrun(
        self,
        competitor_tx: CompetitorLanding,
        token_in: str,
        token_out: str,
        amount_in: int,
        current_block: int,
        eth_usd: float,
        current_gas_gwei: float,
        fee: int = 3000,
    ) -> dict:
        """Execute a backrun swap after a competitor's liquidation.

        Builds a Uni V3 swap to buy the discounted collateral token the
        competitor just dumped, signs it with the native signer, and
        submits to block N+1 via stealth spray.
        """
        if not self.enabled:
            return {"outcome": "disabled", "phase": "backrun"}

        ctx = self._pending_context
        if not ctx:
            return {"outcome": "no_pending_context", "phase": "backrun"}

        # Simulate price impact
        impact = self.backrun_engine.simulate_price_impact(
            competitor_tx, token_in, token_out, amount_in
        )
        estimated_profit = impact * amount_in * eth_usd / 1e18 if eth_usd else 0

        opp = self.backrun_engine.build_backrun(
            competitor_tx, impact, estimated_profit
        )
        if opp is None:
            log.info("Backrun unviable: impact=%.4f profit=$%.2f", impact, estimated_profit)
            self._phase = ExecutionPhase.IDLE
            self._pending_context = None
            return {"outcome": "skip", "phase": "backrun", "reason": "unviable"}

        # min_amount_out = amount_in * (1 - impact) roughly
        min_amount_out = int(amount_in * (1 - impact * 2))

        nm = get_nonce_manager()
        try:
            from eth_signer import get_signer
            signer = get_signer()
        except ImportError:
            signer = None

        if not signer or not signer.ready:
            log.warning("No native signer available for backrun")
            self._phase = ExecutionPhase.IDLE
            self._pending_context = None
            return {"outcome": "no_signer", "phase": "backrun"}

        recipient = signer.address
        calldata = self.backrun_engine.encode_backrun_swap(
            token_in, token_out, amount_in, min_amount_out, fee, recipient
        )

        # GAS: backrun is opportunistic — use moderate gas
        prio = max(1.0, current_gas_gwei * 0.15)
        max_fee = max(current_gas_gwei * 1.3, current_gas_gwei + prio)
        gas_limit = 500_000

        nonce = nm.reserve(signer.address)

        try:
            raw_hex = signer.sign_tx(
                to="0xE592427A0AEce92De3Edee1F18E0157C05861564",  # Uni V3 Router
                data=calldata,
                value=0,
                gas_limit=gas_limit,
                max_fee_gwei=max_fee,
                prio_fee_gwei=prio,
                nonce=nonce,
            )
            signed = "0x" + raw_hex if not raw_hex.startswith("0x") else raw_hex
        except Exception as e:
            nm.release(signer.address)
            log.error("Backrun sign failed: %s", e)
            self._phase = ExecutionPhase.IDLE
            self._pending_context = None
            return {"outcome": "sign_failed", "phase": "backrun", "error": str(e)}

        # Build flashbots auth
        try:
            from eth_signer import sign_flashbots_auth
            body = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "eth_sendBundle",
                "params": [{"txs": [signed], "blockNumber": hex(current_block + 1)}],
            }
            raw_body = json.dumps(body, separators=(",", ":"))
            auth = sign_flashbots_auth(raw_body)
        except Exception:
            auth = ""

        result = await self._send_via_stealth(
            [signed], current_block + 1, signer.address, auth
        )

        if result.get("success"):
            log.info("Backrun submitted for block %d (impact=%.4f)", current_block + 1, impact)
            self._phase = ExecutionPhase.IDLE
            self._pending_context = None
            return {
                "outcome": "backrun_submitted",
                "phase": "backrun",
                "block_target": current_block + 1,
                "price_impact": impact,
                "estimated_profit_usd": estimated_profit,
                "gas_bid": {"max_fee": max_fee, "prio": prio, "gas_limit": gas_limit},
            }
        else:
            nm.release(signer.address)
            log.warning("Backrun submission failed: %s", result.get("error"))
            self._phase = ExecutionPhase.IDLE
            self._pending_context = None
            return {
                "outcome": "submit_failed",
                "phase": "backrun",
                "error": result.get("error"),
            }
