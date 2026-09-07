"""Native ETH transaction signing — replaces cast mktx subprocess overhead."""

import json
import os
import time
import logging
from typing import Optional

log = logging.getLogger("eth_signer")

_Account = None
_pk_type = None


def _ensure_deps():
    global _Account
    if _Account is None:
        from eth_account import Account
        _Account = Account


class NativeSigner:
    """Sign EIP-1559 transactions in-process. ~50ms vs cast mktx ~1-3s."""

    def __init__(self, keystore_path: str, password: str, rpc_urls=None):
        _ensure_deps()
        self._pk = None
        self._address = None
        self._nonce_cache = {}
        self._rpc_urls = rpc_urls or []
        if keystore_path and os.path.isfile(keystore_path):
            with open(keystore_path, "r") as f:
                ks = json.load(f)
            self._pk = _Account.decrypt(ks, password)
            self._address = _Account.from_key(self._pk).address.lower()
        self._ready = bool(self._pk)

    @property
    def ready(self):
        return self._ready

    @property
    def address(self):
        return self._address or ""

    def _rpc_url(self):
        if self._rpc_urls:
            return (
                self._rpc_urls[0]
                if isinstance(self._rpc_urls, (list, tuple))
                else self._rpc_urls
            )
        import liquidation_bot as lb

        urls = lb.RPC_CALL
        if isinstance(urls, (list, tuple)) and urls:
            return urls[0]
        return str(urls or "https://ethereum-rpc.publicnode.com")

    def _eth_call(self, method, params):
        import urllib.request

        url = self._rpc_url()
        body = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        )
        req = urllib.request.Request(
            url, data=body.encode(), headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read()).get("result")

    def get_nonce(self, address: str = None) -> int:
        addr = (address or self._address).lower()
        now = time.time()
        cached = self._nonce_cache.get(addr)
        if cached and now - cached[1] < 5:
            return cached[0]
        raw = self._eth_call("eth_getTransactionCount", [addr, "latest"])
        nonce = int(raw, 16) if raw else 0
        self._nonce_cache[addr] = (nonce, now)
        return nonce

    def bump_nonce(self, address: str = None):
        addr = (address or self._address).lower()
        cached = self._nonce_cache.get(addr)
        if cached:
            self._nonce_cache[addr] = (cached[0] + 1, time.time())

    def sign_tx(
        self,
        to: str,
        data: str = "0x",
        value: int = 0,
        gas_limit: int = 1_500_000,
        max_fee_gwei: float = 30.0,
        prio_fee_gwei: float = 3.0,
        nonce: int = None,
    ) -> str:
        if not self._ready:
            raise RuntimeError("signer not initialized")
        if nonce is None:
            nonce = self.get_nonce()
        tx = {
            "type": 2,
            "chainId": 1,
            "nonce": nonce,
            "to": to,
            "value": value,
            "data": data if data.startswith("0x") else "0x" + data,
            "gas": gas_limit,
            "maxFeePerGas": int(max_fee_gwei * 1e9),
            "maxPriorityFeePerGas": int(prio_fee_gwei * 1e9),
        }
        signed = _Account.sign_transaction(tx, self._pk)
        raw = (
            signed.raw_transaction
            if isinstance(signed.raw_transaction, bytes)
            else signed.rawTransaction
        )
        return raw.hex() if isinstance(raw, bytes) else raw

    def sign_eip191(self, message: bytes) -> str:
        """Sign a message with EIP-191 prefix (for Flashbots auth header)."""
        if not self._ready:
            return ""
        msg_hash = _keccak256(message)
        signed = _Account.signHash(msg_hash, self._pk)
        return "0x" + signed.signature.hex()


def _keccak256(data: bytes) -> bytes:
    try:
        from Crypto.Hash import keccak

        h = keccak.new(digest_bits=256)
        h.update(data)
        return h.digest()
    except ImportError:
        import hashlib

        return hashlib.sha3_256(data).digest()


def _sign_hash(msg_hash: bytes, pk: str) -> str:
    """EIP-191 sign a pre-hashed message."""
    signed = _Account.signHash(msg_hash, pk)
    return "0x" + signed.signature.hex()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
_signer: Optional[NativeSigner] = None


def get_signer() -> NativeSigner:
    global _signer
    if _signer is not None:
        return _signer
    ks = os.environ.get("SPONSOR_KEYSTORE") or os.environ.get("KEYSTORE_PATH", "")
    pw = os.environ.get("SPONSOR_PW") or os.environ.get("KEYSTORE_PW", "")
    if not ks or not pw:
        return NativeSigner("", "")
    _signer = NativeSigner(ks, pw)
    return _signer


def sign_flashbots_auth(body_json: str) -> str:
    """Produce X-Flashbots-Signature header value."""
    s = get_signer()
    if not s.ready:
        return ""
    body_hash = _keccak256(body_json.encode())
    sig = _sign_hash(body_hash, s._pk)
    return f"{s.address}:{sig}"


def sign_and_encode(
    to,
    data,
    value,
    gas_limit,
    max_fee_gwei,
    prio_fee_gwei,
    nonce=None,
):
    """One-call: sign and return hex-encoded raw tx."""
    s = get_signer()
    if not s.ready:
        raise RuntimeError("signer not ready")
    raw_hex = s.sign_tx(
        to, data, value, gas_limit, max_fee_gwei, prio_fee_gwei, nonce
    )
    return "0x" + raw_hex if not raw_hex.startswith("0x") else raw_hex
