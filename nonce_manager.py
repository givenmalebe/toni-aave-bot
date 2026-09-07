"""Nonce management — cache nonces, handle stale nonces, support replacement txs."""
import threading
import time
import logging

log = logging.getLogger("nonce_manager")


class NonceManager:
    """Thread-safe nonce cache with automatic refresh and bumping."""

    def __init__(self, rpc_fn=None):
        self._rpc_fn = rpc_fn  # callable(address) -> int
        self._cache = {}  # address -> (nonce, timestamp)
        self._pending = {}  # address -> nonce (currently in-flight)
        self._lock = threading.Lock()
        self._refresh_interval = 5  # seconds

    def get_nonce(self, address: str) -> int:
        addr = address.lower()
        with self._lock:
            cached = self._cache.get(addr)
            if cached and time.time() - cached[1] < self._refresh_interval:
                return cached[0]
        # Refresh from RPC
        nonce = self._fetch_nonce(addr)
        with self._lock:
            self._cache[addr] = (nonce, time.time())
        return nonce

    def _fetch_nonce(self, address: str) -> int:
        if self._rpc_fn:
            return self._rpc_fn(address)
        # Fallback: direct RPC
        import json
        import urllib.request
        import os

        rpc = os.environ.get(
            "ETH_RPC_URL", "https://ethereum-rpc.publicnode.com"
        )
        body = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_getTransactionCount",
            "params": [address, "latest"],
        })
        req = urllib.request.Request(
            rpc,
            data=body.encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read()).get("result")
        return int(result, 16) if result else 0

    def reserve(self, address: str) -> int:
        """Reserve next nonce for a pending tx. Returns the nonce to use."""
        addr = address.lower()
        nonce = self.get_nonce(addr)
        with self._lock:
            self._pending[addr] = nonce + 1
            self._cache[addr] = (nonce + 1, time.time())
        return nonce

    def release(self, address: str):
        """Release reserved nonce (tx failed/was dropped)."""
        addr = address.lower()
        with self._lock:
            self._pending.pop(addr, None)
            # Force refresh on next get
            self._cache.pop(addr, None)

    def force_refresh(self, address: str = None):
        """Force nonce refresh from RPC."""
        with self._lock:
            if address:
                self._cache.pop(address.lower(), None)
            else:
                self._cache.clear()


# Module-level singleton
_manager = None


def get_nonce_manager(rpc_fn=None) -> NonceManager:
    global _manager
    if _manager is None:
        _manager = NonceManager(rpc_fn)
    return _manager
