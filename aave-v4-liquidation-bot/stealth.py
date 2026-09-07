#!/usr/bin/env python3
"""Flashbots + builder bundle I/O.

`write_relay_payloads` is optional debug dump (off the race path).
`send_bundle` sprays the same eth_sendBundle JSON to Flashbots and other
common builders. Extra relays fail open; Flashbots is the last-resort error.
"""
from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# Public eth_sendBundle endpoints. Overridable via env; extras are optional.
RELAYS = [
    ("flashbots", os.environ.get(
        "FLASHBOTS_RELAY", "https://relay.flashbots.net")),
    ("titan", os.environ.get("TITAN_RELAY", "https://rpc.titanbuilder.xyz")),
    ("beaver", os.environ.get("BEAVER_RELAY", "https://rpc.beaverbuild.org")),
    ("rsync", os.environ.get("RSYNC_RELAY", "https://rsync-builder.xyz")),
    ("builder0x69", os.environ.get("BUILDER0X69_RELAY", "https://builder0x69.io")),
    ("f1b", os.environ.get("F1B_RELAY", "https://rpc.f1b.io")),
    ("payload", os.environ.get("PAYLOAD_RELAY", "https://rpc.payload.de")),
]


def relay_endpoints():
    extra = os.environ.get("BUNDLE_RELAYS", "")
    out = list(RELAYS)
    for part in extra.split(","):
        url = part.strip()
        if url.startswith("http"):
            out.append((url.split("//", 1)[-1][:24], url))
    return out


def write_relay_payloads(path, body):
    """Debug dump only — never call this on the sign/send hot path."""
    if not path:
        return path
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(body, f, indent=2)
    return path


def _post_one(name, url, raw, headers, timeout):
    r = requests.post(
        url, data=raw,
        headers=headers,
        timeout=timeout,
    )
    text = (r.text or "")[:240]
    ok = r.status_code < 400
    try:
        js = r.json()
        if isinstance(js, dict) and js.get("error"):
            ok = False
            text = str(js["error"])[:240]
        elif isinstance(js, dict) and js.get("result") is not None:
            text = str(js["result"])[:160]
    except Exception:
        pass
    return name, ok, r.status_code, text


def send_bundle(body, auth_header=None, timeout=1.8):
    """POST eth_sendBundle in-memory to every configured builder in parallel.

    Fail-open: extra-builder errors are ignored if Flashbots (or any peer)
    accepts. If everything fails, the Flashbots error is returned.
    """
    raw = json.dumps(body, separators=(",", ":")).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": _UA,
    }
    if auth_header:
        headers["X-Flashbots-Signature"] = auth_header

    endpoints = relay_endpoints()
    results = []
    fb_err = None
    any_ok = False
    n_ok = 0
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(endpoints)))) as ex:
        futs = [
            ex.submit(_post_one, name, url, raw, headers, timeout)
            for name, url in endpoints
        ]
        for fut in as_completed(futs):
            try:
                name, ok, code, text = fut.result()
            except Exception as e:  # noqa: BLE001
                name, ok, code, text = "?", False, 0, str(e)[:160]
            results.append({"relay": name, "ok": ok, "status": code,
                            "text": text})
            if name == "flashbots" and not ok:
                fb_err = text
            if ok:
                any_ok = True
                n_ok += 1
    if any_ok:
        return {
            "stage": "sent",
            "reason": f"bundle to {n_ok}/{len(endpoints)} builders",
            "relays": results,
            "n_ok": n_ok,
        }
    return {
        "stage": "error",
        "reason": (fb_err or (results[0]["text"] if results else "no relays"))[:240],
        "relays": results,
        "n_ok": 0,
    }
