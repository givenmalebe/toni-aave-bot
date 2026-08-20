"""Alchemy relay — private mempool, Flashbots Protect, relay fallback."""

import json
import logging
import os
from typing import Optional

import aiohttp

log = logging.getLogger("alchemy_relay")

# Endpoints
FLASHBOTS_RELAY = "https://relay.flashbots.net"
ALCHEMY_PRIVATE = "https://eth-mainnet.g.alchemy.com/v2"


class AlchemyRelay:
    """Send transactions via Alchemy private relay and Flashbots."""

    def __init__(
        self,
        alchemy_api_key: Optional[str] = None,
        flashbots_relay: str = FLASHBOTS_RELAY,
    ):
        self.alchemy_api_key = alchemy_api_key or os.getenv("ALCHEMY_API_KEY", "")
        self.flashbots_relay = flashbots_relay
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def send_bundle(
        self,
        signed_txs: list[str],
        block_number: int,
        signer_address: str,
        signature: str,
    ) -> dict:
        """Send bundle via Flashbots relay."""
        session = await self._get_session()

        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_sendBundle",
            "params": [
                {
                    "txs": signed_txs,
                    "blockNumber": hex(block_number),
                    "minTimestamp": 0,
                    "maxTimestamp": 0,
                }
            ],
        }

        headers = {
            "Content-Type": "application/json",
            "X-Flashbots-Signature": f"{signer_address}:{signature}",
        }

        try:
            async with session.post(
                self.flashbots_relay,
                json=body,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                result = await resp.json()
                if "error" in result:
                    log.warning("Flashbots bundle failed: %s", result["error"])
                    return {"success": False, "error": result["error"]}
                log.info("Flashbots bundle submitted for block %d", block_number)
                return {"success": True, "result": result.get("result")}
        except Exception as e:
            log.warning("Flashbots relay error: %s", e)
            return {"success": False, "error": str(e)}

    async def send_private_tx(
        self,
        signed_tx: str,
    ) -> dict:
        """Send private transaction via Alchemy."""
        if not self.alchemy_api_key:
            return {"success": False, "error": "No Alchemy API key"}

        session = await self._get_session()
        url = f"{ALCHEMY_PRIVATE}/{self.alchemy_api_key}"

        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_sendRawPrivateTransaction",
            "params": [{"signedTransaction": signed_tx}],
        }

        try:
            async with session.post(
                url,
                json=body,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                result = await resp.json()
                if "error" in result:
                    log.warning("Alchemy private tx failed: %s", result["error"])
                    return {"success": False, "error": result["error"]}
                log.info("Alchemy private tx submitted")
                return {"success": True, "result": result.get("result")}
        except Exception as e:
            log.warning("Alchemy relay error: %s", e)
            return {"success": False, "error": str(e)}

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
