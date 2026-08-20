"""Backrun engine — detect competitor txs, simulate price impact, build backruns."""

import logging
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("backrun")


@dataclass
class CompetitorLanding:
    tx_hash: str
    block_number: int
    user: str
    protocol: str
    profit_usd: float


@dataclass
class BackrunOpportunity:
    competitor_tx: str
    price_impact: float  # percentage
    estimated_profit_usd: float
    swap_path: str  # uni v3 path


class BackrunEngine:
    """Detect competitor liquidations and build backrun opportunities."""

    def __init__(self):
        self._recent_competitor_txs: list[CompetitorLanding] = []

    def detect_competitor_tx(
        self,
        block_txs: list[dict],
        our_tx_hash: Optional[str],
    ) -> Optional[CompetitorLanding]:
        """Check if a competitor landed a liquidation in the block."""
        for tx in block_txs:
            if tx.get("hash") == our_tx_hash:
                continue
            input_data = tx.get("input", "")
            liq_selectors = ["0xc2fa746c", "0x9b4c6d1f"]  # Aave V3/V4
            for sel in liq_selectors:
                if input_data.startswith(sel):
                    landing = CompetitorLanding(
                        tx_hash=tx["hash"],
                        block_number=tx.get("blockNumber", 0),
                        user="",
                        protocol="aave",
                        profit_usd=0,
                    )
                    self._recent_competitor_txs.append(landing)
                    return landing
        return None

    def simulate_price_impact(
        self,
        competitor_tx: CompetitorLanding,
        token_in: str,
        token_out: str,
        amount: int,
    ) -> float:
        """Simulate price impact of competitor's liquidation.

        Returns estimated price impact as a percentage (0.01 = 1%).
        """
        impact = min(amount / 1_000_000, 0.05)  # cap at 5%
        return impact

    def build_backrun(
        self,
        competitor_tx: CompetitorLanding,
        price_impact: float,
        estimated_profit_usd: float,
        gas_limit: int = 500_000,
    ) -> Optional[BackrunOpportunity]:
        """Build a backrun opportunity from a competitor's liquidation."""
        if estimated_profit_usd < 5:
            return None
        return BackrunOpportunity(
            competitor_tx=competitor_tx.tx_hash,
            price_impact=price_impact,
            estimated_profit_usd=estimated_profit_usd,
            swap_path="",
        )

    def get_recent_competitors(self, count: int = 10) -> list[CompetitorLanding]:
        """Get recent competitor landings."""
        return self._recent_competitor_txs[-count:]
