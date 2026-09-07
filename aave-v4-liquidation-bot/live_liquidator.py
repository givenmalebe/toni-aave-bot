#!/usr/bin/env python3
"""Sign + Flashbots submit surface for the TONI dashboard.

Sim-only by default (`sim_only=True`): never POSTs bundles.
LIVE: decrypt keystore once in memory, `cast mktx`, then spray eth_sendBundle.
No disk I/O on the race path.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time

import liquidation_bot as lb
import stealth
from nonce_manager import get_nonce_manager

SPONSOR_KEYSTORE = os.environ.get("SPONSOR_KEYSTORE", "")
SPONSOR_PW = os.environ.get("SPONSOR_PW", "")
SPONSOR_AMOUNT_ETH = float(os.environ.get("SPONSOR_AMOUNT_ETH", "0.03"))
LIQ_SIG = os.environ.get(
    "LIQ_SIG", "flashLiquidate(address,address,address,uint256)")
LIQ_DEBUG_WRITE = os.environ.get("LIQ_DEBUG_WRITE", "0") == "1"

_OPP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "opps")

_CAST_CANDIDATES = [
    os.environ.get("CAST", ""),
    r"C:\Users\Surf\.foundry\bin\cast.exe",
    r"C:\Users\Surf\.foundry\bin\cast",
    "cast",
]

_UNLOCKED = {}  # role -> (pk, address)
_UNLOCK_LOCK = threading.Lock()
_CAST_BIN = None


def latest_block() -> int:
    return int(lb.jrpc(lb.RPC_CALL, "eth_blockNumber", []), 16)


def load_borrowers():
    extra = os.environ.get("LIQ_BORROWERS", "")
    env_addrs = [a.strip().lower() for a in extra.split(",") if a.strip()]
    cached = lb.load_borrower_cache()
    seen, out = set(), []
    for a in env_addrs + list(cached):
        if a.startswith("0x") and a not in seen:
            seen.add(a)
            out.append(a)
    return out


def emit_opportunity(user, out, block, long_tail=False) -> str:
    """Debug JSON dump — do not call from the race path."""
    os.makedirs(_OPP_DIR, exist_ok=True)
    path = os.path.join(_OPP_DIR, f"{int(time.time())}_{str(user)[:10]}.json")
    rec = {
        "user": user,
        "block": block,
        "long_tail": bool(long_tail),
        "plan": out,
        "sim_only": True,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=2)
    return path


def _cast_bin() -> str:
    global _CAST_BIN
    if _CAST_BIN:
        return _CAST_BIN
    for c in _CAST_CANDIDATES:
        if not c:
            continue
        if os.path.isfile(c):
            _CAST_BIN = c
            return c
        w = shutil.which(c)
        if w:
            _CAST_BIN = w
            return w
    return ""


def _run_cast(args, timeout=20):
    bin_ = _cast_bin()
    if not bin_:
        return 127, "foundry cast not found"
    try:
        p = subprocess.run(
            [bin_, *args],
            capture_output=True, text=True, timeout=timeout,
            env={**os.environ, "FOUNDRY_DISABLE_NIGHTLY_WARNING": "1"},
        )
        out = (p.stdout or "").strip() or (p.stderr or "").strip()
        return p.returncode, out[:1200]
    except Exception as e:  # noqa: BLE001
        return 1, str(e)[:240]


def _unlock(role="bot"):
    """Decrypt keystore once; keep pk in process memory (never disk)."""
    with _UNLOCK_LOCK:
        hit = _UNLOCKED.get(role)
        if hit:
            return hit
        if role == "sponsor":
            path, pw = SPONSOR_KEYSTORE, SPONSOR_PW
        else:
            path, pw = lb.KEYSTORE_PATH, lb.KEYSTORE_PW
        if not path or not os.path.isfile(path) or not pw:
            raise RuntimeError(f"{role} keystore missing")
        rc, out = _run_cast(["wallet", "decrypt-keystore", path, pw], timeout=30)
        if rc != 0 or not out:
            raise RuntimeError(f"decrypt {role} failed: {out[:160]}")
        pk = out.split()[-1].strip()
        if not pk.startswith("0x"):
            pk = "0x" + pk
        rc2, addr = _run_cast(["wallet", "address", "--private-key", pk], timeout=8)
        if rc2 != 0 or not str(addr).startswith("0x"):
            raise RuntimeError(f"address {role} failed: {addr[:120]}")
        rec = (pk, addr.strip().lower())
        _UNLOCKED[role] = rec
        return rec


def _rpc_url():
    urls = lb.RPC_CALL
    if isinstance(urls, (list, tuple)) and urls:
        return urls[0]
    return str(urls or "https://ethereum-rpc.publicnode.com")


def _plan_args(out):
    to = (out.get("contract") or os.environ.get("LIQ_CONTRACT") or "").strip()
    sig = out.get("liq_sig") or LIQ_SIG
    extra = out.get("liq_args")
    if extra:
        return to, sig, [str(a) for a in extra]
    coll = (out.get("collateralAsset") or out.get("coll_addr") or "").strip()
    debt = (out.get("debtAsset") or out.get("debt_addr") or "").strip()
    user = (out.get("user") or "").strip()
    cover = out.get("debtToCover")
    if cover is None:
        cover = str((1 << 256) - 1)
    else:
        cover = str(cover)
    return to, sig, [coll, debt, user, cover]


def _encode_calldata(sig: str, args: list[str]) -> str:
    """Encode selector + abi.encode(args) for common liquidation signatures."""
    import hashlib

    def keccak256(text: str) -> bytes:
        try:
            from Crypto.Hash import keccak
            h = keccak.new(digest_bits=256)
            h.update(text.encode())
            return h.digest()
        except ImportError:
            return hashlib.sha3_256(text.encode()).digest()

    def pad_addr(a: str) -> str:
        return a.lower().replace("0x", "").rjust(64, "0")

    def pad_u256(n) -> str:
        return hex(int(n) & ((1 << 256) - 1))[2:].rjust(64, "0")

    selector = keccak256(sig)[:4].hex()
    data = "0x" + selector
    for arg in args:
        a = str(arg).strip()
        if a.startswith("0x") and len(a) == 42:
            data += pad_addr(a)
        else:
            data += pad_u256(a)
    return data


def _replace_tx(to, sig, call_args, gas_gwei, prio_mult=1.0, gas_limit=1_500_000, role="bot"):
    """Build a replacement tx with higher gas price using the same nonce."""
    pk, addr = _unlock(role)
    nm = get_nonce_manager()
    nonce = nm.get_nonce(addr)  # use current cached nonce (replacement)
    prio = max(0.05, float(gas_gwei) * 0.2) * float(prio_mult or 1.0)
    max_fee = max(float(gas_gwei) * 1.5, float(gas_gwei) + prio * 1.5)
    # Bump fees for replacement
    max_fee *= 1.2
    prio *= 1.3

    try:
        from eth_signer import get_signer
        signer = get_signer()
        if signer.ready:
            data = _encode_calldata(sig, call_args)
            raw_hex = signer.sign_tx(
                to, data, 0, gas_limit, max_fee, prio, nonce
            )
            return "0x" + raw_hex, addr
    except ImportError:
        pass

    args = [
        "mktx", to, sig, *[str(a) for a in call_args],
        "--private-key", pk,
        "--rpc-url", _rpc_url(),
        "--chain", "1",
        "--nonce", str(nonce),
        "--gas-limit", str(int(gas_limit)),
        "--priority-gas-price", f"{prio:.4f}gwei",
        "--gas-price", f"{max_fee:.4f}gwei",
    ]
    rc, raw = _run_cast(args, timeout=20)
    if rc != 0 or not str(raw).startswith("0x"):
        raise RuntimeError(f"replace mktx failed: {raw[:200]}")
    return raw.strip().split()[-1], addr


def _sign_tx(out, target_block, role="bot", prio_mult=1.0):
    """Sign flash-liq call in memory. Uses native nonce management."""
    to, sig, call_args = _plan_args(out)
    if not to or not to.startswith("0x"):
        raise RuntimeError("LIQ_CONTRACT unset — cannot sign")
    if not call_args:
        raise RuntimeError("plan missing call args")
    pk, addr = _unlock(role)
    nm = get_nonce_manager()
    nonce = nm.reserve(addr)
    gas_gwei = float(out.get("gas_gwei") or 2.0)
    prio = max(0.05, gas_gwei * 0.2) * float(prio_mult or 1.0)
    max_fee = max(gas_gwei * 1.35, gas_gwei + prio)
    if float(prio_mult or 1) > 1:
        max_fee *= 1.15

    # Try native signing first, fall back to cast
    try:
        from eth_signer import get_signer
        signer = get_signer()
        if signer.ready:
            data = _encode_calldata(sig, call_args)
            raw_hex = signer.sign_tx(
                to, data, 0,
                int(out.get("gas_limit") or 1_500_000),
                max_fee, prio, nonce
            )
            return "0x" + raw_hex, addr, None
    except ImportError:
        pass

    # Fallback to cast mktx
    args = [
        "mktx", to, sig, *call_args,
        "--private-key", pk,
        "--rpc-url", _rpc_url(),
        "--chain", "1",
        "--nonce", str(nonce),
        "--gas-limit", str(int(out.get("gas_limit") or 1_500_000)),
        "--priority-gas-price", f"{prio:.4f}gwei",
        "--gas-price", f"{max_fee:.4f}gwei",
    ]
    rc, raw = _run_cast(args, timeout=20)
    if rc != 0 or not str(raw).startswith("0x"):
        nm.release(addr)
        raise RuntimeError(f"mktx failed: {raw[:200]}")
    return raw.strip().split()[-1], addr, None


def _sign_sponsor(target_block):
    pk, addr = _unlock("sponsor")
    bot_pk, bot_addr = _unlock("bot")
    amt = SPONSOR_AMOUNT_ETH
    args = [
        "mktx", bot_addr,
        "--value", f"{amt}ether",
        "--private-key", pk,
        "--rpc-url", _rpc_url(),
        "--chain", "1",
        "--gas-limit", "21000",
    ]
    rc, raw = _run_cast(args, timeout=20)
    if rc != 0 or not str(raw).startswith("0x"):
        raise RuntimeError(f"sponsor mktx failed: {raw[:200]}")
    return raw.strip().split()[-1], addr


def _sign_raw_to(to, sig, call_args, gas_gwei=2.0, prio_mult=1.0,
                 gas_limit=700_000, role="bot"):
    """Sign an arbitrary contract call (ETH arb). In-memory pk cache."""
    pk, addr = _unlock(role)
    prio = max(0.05, float(gas_gwei) * 0.2) * float(prio_mult or 1.0)
    max_fee = max(float(gas_gwei) * 1.35, float(gas_gwei) + prio)
    args = [
        "mktx", to, sig, *[str(a) for a in call_args],
        "--private-key", pk,
        "--rpc-url", _rpc_url(),
        "--chain", "1",
        "--gas-limit", str(int(gas_limit)),
        "--priority-gas-price", f"{prio:.4f}gwei",
        "--gas-price", f"{max_fee:.4f}gwei",
    ]
    rc, raw = _run_cast(args, timeout=20)
    if rc != 0 or not str(raw).startswith("0x"):
        raise RuntimeError(f"mktx failed: {raw[:200]}")
    return raw.strip().split()[-1], addr


def _flashbots_auth(raw_json: str) -> str | None:
    """X-Flashbots-Signature using cached bot key (EIP-191 of keccak(body))."""
    # Try native first
    try:
        from eth_signer import sign_flashbots_auth
        result = sign_flashbots_auth(raw_json)
        if result:
            return result
    except (ImportError, Exception):
        pass
    # Fallback to cast
    try:
        pk, addr = _unlock("bot")
    except Exception:
        return None
    rc, hx = _run_cast(["keccak", raw_json], timeout=8)
    if rc != 0 or not str(hx).startswith("0x"):
        return None
    rc2, sig = _run_cast(
        ["wallet", "sign", "--private-key", pk, hx.strip()], timeout=8)
    if rc2 != 0 or not str(sig).startswith("0x"):
        return None
    return f"{addr}:{sig.strip()}"


def build_bundle_body(signed_hex, target_block):
    hx = signed_hex if str(signed_hex).startswith("0x") else "0x" + str(signed_hex)
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_sendBundle",
        "params": [{
            "txs": [hx],
            "blockNumber": hex(int(target_block)),
        }],
    }


def _debug_write_async(path, body):
    if not LIQ_DEBUG_WRITE or not path:
        return
    def _w():
        try:
            stealth.write_relay_payloads(path, body)
        except Exception:
            pass
    threading.Thread(target=_w, daemon=True).start()


def _submit(path, body, target_block, sim_only=True, sponsor_hex=None):
    """In-memory bundle submit. `path` is unused except optional debug dump."""
    if path:
        _debug_write_async(path, body)
    if sim_only:
        return {
            "stage": "simulated",
            "reason": "sim-only — bundle not sent",
            "block": target_block,
            "sim_only": True,
            "relays": [n for n, _ in stealth.relay_endpoints()],
        }
    # LIVE: spray builders. Fail open to Flashbots if extras error.
    txs = list(((body.get("params") or [{}])[0] or {}).get("txs") or [])
    if sponsor_hex and sponsor_hex not in txs:
        txs = [sponsor_hex] + [t for t in txs if t]
        body = {
            **body,
            "params": [{
                **((body.get("params") or [{}])[0] or {}),
                "txs": txs,
                "blockNumber": hex(int(target_block)),
            }],
        }
    raw = json.dumps(body, separators=(",", ":"))
    auth = _flashbots_auth(raw)
    result = stealth.send_bundle(body, auth_header=auth)
    result["block"] = target_block
    result["sim_only"] = False
    result["auth"] = bool(auth)
    return result
